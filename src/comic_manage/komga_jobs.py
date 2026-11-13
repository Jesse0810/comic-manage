"""Durable, secret-free automatic indexing jobs for verified local CBZ exports.

The desktop scheduler supplies current credentials for one bounded pass. Jobs do
not generate files, perform scans, or inherit a different connection's scope.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from threading import Event, Lock
import time
from typing import Callable
from uuid import uuid4
import zipfile

from .komga_auto import AutoSyncIssue, match_book, synchronize_entities
from .komga_client import KomgaClient, KomgaError, normalize_url
from .metadata import Metadata, parse_xml, to_comicinfo
from .sources import Cancelled, check_cancel


DUE_STATES = ("PendingIndex", "PendingSync", "RetryableFailure")
RETRY_STATES = DUE_STATES + ("NeedsManualReview", "PermanentFailure")
LEASE_SECONDS = 300
MAX_SCHEDULE_TIMESTAMP = 253402300799  # Last representable UTC second in year 9999.
_lock_guard = Lock()
_runner_locks: dict[str, Lock] = {}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _stamp(value: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if value is None else value, timezone.utc).isoformat(timespec="seconds")


def _file_hash(path: Path, cancel: Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while data := handle.read(1024 * 1024):
            check_cancel(cancel)
            digest.update(data)
    return digest.hexdigest()


def _schema(repo) -> None:
    with repo.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS komga_auto_jobs (
                comic_id TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, metadata_json TEXT NOT NULL, metadata_hash TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL, output_path TEXT NOT NULL DEFAULT '',
                output_root TEXT NOT NULL DEFAULT '', relative_path TEXT NOT NULL DEFAULT '',
                artifact_hash TEXT NOT NULL DEFAULT '', xml_hash TEXT NOT NULL DEFAULT '',
                server_url TEXT NOT NULL DEFAULT '', library_id TEXT NOT NULL DEFAULT '',
                book_id TEXT NOT NULL DEFAULT '', series_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, synced_at TEXT NOT NULL DEFAULT '',
                target_hash TEXT NOT NULL DEFAULT '', last_snapshot TEXT NOT NULL DEFAULT '{}',
                lease_token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
                stale INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS komga_auto_due ON komga_auto_jobs(server_url,library_id,status,next_attempt);
            CREATE TABLE IF NOT EXISTS komga_auto_backups (
                id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, comic_id TEXT NOT NULL,
                metadata_hash TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS komga_auto_book_leases (
                server_url TEXT NOT NULL, library_id TEXT NOT NULL, book_id TEXT NOT NULL,
                owner TEXT NOT NULL, lease_until REAL NOT NULL,
                PRIMARY KEY(server_url,library_id,book_id)
            );
        """)
        # Also allow a development build's pending tasks to survive this additive
        # marker. Released catalogs do not have this queue before 1.11.
        if "stale" not in {row["name"] for row in db.execute("PRAGMA table_info(komga_auto_jobs)")}:
            db.execute("ALTER TABLE komga_auto_jobs ADD COLUMN stale INTEGER NOT NULL DEFAULT 0")


def _get(repo, job_id: str) -> dict | None:
    with repo.connect() as db:
        row = db.execute("SELECT * FROM komga_auto_jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def _artifact_stat(path: Path) -> tuple:
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _current(repo, job: dict, *, artifact: bool = True, full_artifact: bool = True,
             cancel: Event | None = None) -> None:
    check_cancel(cancel)
    comic = repo.get(job["comic_id"])
    if (comic.kind == "komga" or comic.status not in ("approved", "packaged")
            or _hash(comic.metadata.to_dict()) != job["metadata_hash"]
            or comic.fingerprint != job["source_fingerprint"]):
        raise AutoSyncIssue("已审核元数据或来源版本已变化，请重新审核并生成对应 CBZ。", "AUTO_STALE", "NeedsManualReview")
    comic.metadata.validate(comic.page_count)
    if artifact:
        if not comic.output or str(Path(comic.output).resolve()) != job["output_path"]:
            raise AutoSyncIssue("已验证 CBZ 的位置已变化，请重新生成或核对成品。", "AUTO_ARTIFACT", "NeedsManualReview")
        path = Path(job["output_path"])
        before = _artifact_stat(path)
        if job.get("_artifact_stat") is not None and before != job["_artifact_stat"]:
            raise AutoSyncIssue("成品文件在同步期间发生变化，请重新核对。", "AUTO_ARTIFACT", "NeedsManualReview")
        if full_artifact and _file_hash(path, cancel) != job["artifact_hash"]:
            raise AutoSyncIssue("已验证 CBZ 的内容已变化，本次不会同步旧任务。", "AUTO_ARTIFACT", "NeedsManualReview")
        if _artifact_stat(path) != before:
            raise AutoSyncIssue("成品文件在读取期间发生变化，请重新核对。", "AUTO_ARTIFACT", "NeedsManualReview")
        if full_artifact:
            job["_artifact_stat"] = before
    check_cancel(cancel)


def _public(job: dict, *, unbound_ready: bool = False) -> dict:
    return {"id": job["id"], "comicId": job["comic_id"], "title": job["title"],
            "status": job["status"], "outputPath": job["output_path"], "relativePath": job["relative_path"],
            "serverUrl": job["server_url"], "libraryId": job["library_id"],
            "bookId": job["book_id"], "seriesId": job["series_id"], "attempts": job["attempts"],
            "lastError": job["last_error"], "nextAttemptAt": _stamp(job["next_attempt"]) if job["next_attempt"] else "",
            "syncedAt": job["synced_at"], "updatedAt": job["updated_at"],
            "metadataVersion": job["metadata_hash"],
            "canRetry": not job["stale"] and (unbound_ready or (job["status"] in RETRY_STATES and bool(job["artifact_hash"])))}


def _unbound_ready(repo, job: dict) -> bool:
    if (job["status"] != "Approved" or job["server_url"] or job["library_id"] or job["stale"]
            or not job["output_path"] or not job["output_root"]):
        return False
    try:
        comic = repo.get(job["comic_id"])
        return (comic.kind != "komga" and comic.status == "packaged" and comic.output
                and _hash(comic.metadata.to_dict()) == job["metadata_hash"]
                and comic.fingerprint == job["source_fingerprint"]
                and str(Path(comic.output).resolve()) == job["output_path"]
                and Path(job["output_path"]).is_file())
    except (ValueError, OSError):
        return False


def invalidate_jobs(repo, identities: list[str] | None = None) -> int:
    """Retire stale badges promptly without reading potentially huge archives."""
    _schema(repo)
    wanted = None if identities is None else set(identities)
    with repo.connect() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM komga_auto_jobs")
                if wanted is None or row["comic_id"] in wanted]
    changed = []
    for job in rows:
        try:
            comic = repo.get(job["comic_id"])
            valid = (comic.kind != "komga" and comic.status in ("approved", "packaged")
                     and _hash(comic.metadata.to_dict()) == job["metadata_hash"]
                     and comic.fingerprint == job["source_fingerprint"]
                     and (not job["output_path"] or (comic.output and str(Path(comic.output).resolve()) == job["output_path"])))
        except (ValueError, OSError):
            valid = False
        if not valid:
            changed.append((job["id"], job["metadata_hash"]))
    with repo.connect() as db:
        for job_id, version in changed:
            db.execute("""UPDATE komga_auto_jobs SET status='NeedsManualReview',next_attempt=0,stale=1,
                last_error='审核版本、列表记录或成品引用已变化，请重新审核并生成对应 CBZ。',updated_at=?
                WHERE id=? AND metadata_hash=? AND (status!='NeedsManualReview' OR next_attempt!=0 OR stale!=1)""",
                       (_stamp(), job_id, version))
    return len(changed)


def list_jobs(repo) -> list[dict]:
    _schema(repo)
    invalidate_jobs(repo)
    with repo.connect() as db:
        jobs = [dict(row) for row in db.execute("SELECT * FROM komga_auto_jobs ORDER BY updated_at DESC,comic_id")]
    return [_public(job, unbound_ready=_unbound_ready(repo, job)) for job in jobs]


def record_approval(repo, comic_id: str) -> dict | None:
    """Record a local approval; it cannot contact Komga before verified export."""
    _schema(repo)
    comic = repo.get(comic_id)
    if comic.kind == "komga":
        return None
    if comic.status not in ("approved", "packaged"):
        raise ValueError("只有人工审核通过的作品可创建自动同步任务。")
    comic.metadata.validate(comic.page_count)
    values = comic.metadata.to_dict()
    version = _hash(values)
    with repo.connect() as db:
        existing = db.execute("SELECT * FROM komga_auto_jobs WHERE comic_id=?", (comic_id,)).fetchone()
        if existing and not existing["stale"] and existing["metadata_hash"] == version and existing["source_fingerprint"] == comic.fingerprint:
            row = dict(existing)
            return _public(row, unbound_ready=_unbound_ready(repo, row))
        stamp = _stamp()
        db.execute("DELETE FROM komga_auto_jobs WHERE comic_id=?", (comic_id,))
        db.execute("""INSERT INTO komga_auto_jobs
            (comic_id,id,title,metadata_json,metadata_hash,source_fingerprint,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,'Approved',?,?)""",
                   (comic_id, uuid4().hex, comic.metadata.display_title, _json(values), version, comic.fingerprint, stamp, stamp))
        row = db.execute("SELECT * FROM komga_auto_jobs WHERE comic_id=?", (comic_id,)).fetchone()
    return _public(dict(row))


def _published_output(repo, comic_id: str, output_root: Path | str) -> dict:
    comic = repo.get(comic_id)
    if comic.kind == "komga" or comic.status != "packaged" or not comic.output:
        raise ValueError("自动实体同步需要已生成并验证的本地 CBZ。")
    comic.metadata.validate(comic.page_count)
    root = Path(output_root).resolve(strict=True)
    path = Path(comic.output).resolve(strict=True)
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        raise ValueError("成品不在本次配置的输出目录内。") from None
    if path.suffix.lower() != ".cbz" or not path.is_file():
        raise ValueError("自动同步仅接受最终发布的 CBZ 文件。")
    return {"comic": comic, "root": root, "path": path, "relative": relative}


def _prepared_export(repo, comic_id: str, output_root: Path | str, cancel: Event | None = None) -> dict:
    prepared = _published_output(repo, comic_id, output_root)
    comic, path = prepared["comic"], prepared["path"]
    expected_xml = to_comicinfo(comic.metadata, comic.page_count)
    initial_hash = _file_hash(path, cancel)
    # Export already verifies source member digests before its atomic rename. Re-
    # validate the published archive, so no altered or partial file enters a job.
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("成品 ZIP CRC 或文件清单校验失败。")
        for name in names:
            with archive.open(name) as member:
                while member.read(1024 * 1024):
                    check_cancel(cancel)
        xml = archive.read("ComicInfo.xml")
        parse_xml(xml)
        if xml != expected_xml:
            raise ValueError("成品 ComicInfo.xml 与当前审核元数据不一致，请重新生成。")
    from .image_validation import verify_page_images
    verify_page_images(path, cancel)
    artifact_hash = _file_hash(path, cancel)
    if artifact_hash != initial_hash:
        raise ValueError("成品在入队校验时发生变化，请重新核对。")
    prepared.update(artifact_hash=artifact_hash, xml_hash=hashlib.sha256(xml).hexdigest())
    return prepared


def _write_export(db, prepared: dict, server: str = "", library_id: str = "", status: str = "Approved") -> dict:
    comic, root, path = prepared["comic"], prepared["root"], prepared["path"]
    stamp = _stamp()
    db.execute("DELETE FROM komga_auto_jobs WHERE comic_id=?", (comic.id,))
    db.execute("""INSERT INTO komga_auto_jobs
        (comic_id,id,title,metadata_json,metadata_hash,source_fingerprint,output_path,output_root,relative_path,
         artifact_hash,xml_hash,server_url,library_id,status,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (comic.id, uuid4().hex, comic.metadata.display_title, _json(comic.metadata.to_dict()),
                _hash(comic.metadata.to_dict()), comic.fingerprint, str(path), str(root), prepared["relative"],
                prepared.get("artifact_hash", ""), prepared.get("xml_hash", ""), server, library_id, status, stamp, stamp))
    return dict(db.execute("SELECT * FROM komga_auto_jobs WHERE comic_id=?", (comic.id,)).fetchone())


def record_validated_export(repo, comic_id: str, output_root: Path | str) -> dict:
    """Remember a published CBZ even with automation off or a failed enqueue.

    The normal exporter has already verified CRC/XML before its atomic publish.
    This unbound record is not due for network work. An explicit retry performs
    the additional automatic-sync image/hash validation and binds the connection.
    """
    _schema(repo)
    prepared = _published_output(repo, comic_id, output_root)
    comic, path = prepared["comic"], prepared["path"]
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("ComicInfo.xml")
        if xml != to_comicinfo(comic.metadata, comic.page_count):
            raise ValueError("成品 ComicInfo.xml 与当前审核元数据不一致。")
    prepared["xml_hash"] = hashlib.sha256(xml).hexdigest()
    with repo.connect() as db:
        existing = db.execute("SELECT * FROM komga_auto_jobs WHERE comic_id=?", (comic_id,)).fetchone()
        same = (existing and not existing["stale"] and existing["metadata_hash"] == _hash(comic.metadata.to_dict())
                and existing["source_fingerprint"] == comic.fingerprint and existing["output_path"] == str(path)
                and existing["output_root"] == str(prepared["root"]))
        if same:
            row = dict(existing)
        else:
            row = _write_export(db, prepared)
    return _public(row, unbound_ready=_unbound_ready(repo, row))


def enqueue_export(repo, comic_id: str, output_root: Path | str, url: str, library_id: str,
                   *, cancel: Event | None = None) -> dict:
    """Freeze the already published, verified CBZ, without rewriting that CBZ."""
    _schema(repo)
    server = normalize_url(url)
    if not isinstance(library_id, str) or not library_id.strip() or len(library_id) > 4096:
        raise ValueError("自动同步需要实际的 Komga Library ID。")
    prepared = _prepared_export(repo, comic_id, output_root, cancel)
    comic = prepared["comic"]
    with repo.connect() as db:
        existing = db.execute("SELECT * FROM komga_auto_jobs WHERE comic_id=?", (comic_id,)).fetchone()
        same = (existing and not existing["stale"] and existing["metadata_hash"] == _hash(comic.metadata.to_dict())
                and existing["artifact_hash"] == prepared["artifact_hash"] and existing["server_url"] == server
                and existing["library_id"] == library_id and existing["output_path"] == str(prepared["path"])
                and existing["output_root"] == str(prepared["root"]))
        row = dict(existing) if same else _write_export(db, prepared, server, library_id, "PendingIndex")
    return _public(row)


def retry_jobs(repo, identities: list[str], url: str, library_id: str, *, cancel: Event | None = None) -> dict:
    _schema(repo)
    server = normalize_url(url)
    if not isinstance(library_id, str) or not library_id.strip() or len(library_id) > 4096:
        raise ValueError("自动同步需要实际的 Komga Library ID。")
    if not isinstance(identities, list) or not identities or any(not isinstance(item, str) or not item for item in identities):
        raise ValueError("请勾选要重试的自动同步任务。")
    jobs = [_get(repo, identity) for identity in dict.fromkeys(identities)]
    prepared_exports = {}
    for job in jobs:
        check_cancel(cancel)
        if not job:
            raise ValueError("所选同步任务不存在。")
        unbound = job["status"] == "Approved" and not job["server_url"] and not job["library_id"]
        if not unbound and (job["server_url"] != server or job["library_id"] != library_id):
            raise ValueError("所选任务不属于当前 Komga 连接和书库。")
        if not unbound and job["status"] not in RETRY_STATES:
            raise ValueError("此任务当前无需重试。")
        if job["stale"]:
            raise ValueError("任务对应的审核版本已失效，请重新审核并生成对应 CBZ。")
        if job["lease_until"] > time.time():
            raise ValueError("任务正在同步，请等待完成。")
        _current(repo, job, artifact=not unbound, cancel=cancel)
        if unbound:
            if not _unbound_ready(repo, job):
                raise ValueError("请先生成对应审核版本的 CBZ，再为其创建同步任务。")
            prepared = _prepared_export(repo, job["comic_id"], job["output_root"], cancel)
            if _hash(prepared["comic"].metadata.to_dict()) != job["metadata_hash"]:
                raise ValueError("验证期间审核版本已变化，请重新审核生成。")
            prepared_exports[job["id"]] = prepared
    check_cancel(cancel)
    with repo.connect() as db:
        # Finish every preflight before mutating any job. Recheck the selected
        # job/version/lease inside this transaction, then commit the whole batch.
        for job in jobs:
            current = db.execute("SELECT * FROM komga_auto_jobs WHERE id=?", (job["id"],)).fetchone()
            comic = db.execute("SELECT * FROM comics WHERE id=? AND id NOT IN (SELECT comic_id FROM removed_comics)", (job["comic_id"],)).fetchone()
            if (not current or current["stale"] or current["lease_until"] > time.time()
                    or current["metadata_hash"] != job["metadata_hash"] or not comic
                    or current["status"] != job["status"]
                    or comic["status"] not in ("approved", "packaged")
                    or _hash(Metadata.from_dict(json.loads(comic["metadata"])).to_dict()) != job["metadata_hash"]
                    or comic["fingerprint"] != job["source_fingerprint"]
                    or (job["output_path"] and str(Path(comic["output"]).resolve()) != job["output_path"])
                    or (job["id"] in prepared_exports and comic["status"] != "packaged")):
                raise ValueError("所选任务在验证期间变化，请重新检查后重试。")
        for job in jobs:
            if job["id"] in prepared_exports:
                _write_export(db, prepared_exports[job["id"]], server, library_id, "PendingIndex")
            else:
                db.execute("""UPDATE komga_auto_jobs SET status='PendingIndex',next_attempt=0,last_error='',
                    lease_token='',lease_until=0,updated_at=? WHERE id=? AND metadata_hash=?""",
                           (_stamp(), job["id"], job["metadata_hash"]))
    return {"count": len(jobs), "jobs": list_jobs(repo)}


def _claim(repo, job: dict, current_time: float) -> str | None:
    token = uuid4().hex
    with repo.connect() as db:
        cursor = db.execute("""UPDATE komga_auto_jobs SET lease_token=?,lease_until=?
            WHERE id=? AND metadata_hash=? AND stale=0 AND lease_until<=? AND next_attempt<=?
            AND status IN ('PendingIndex','PendingSync','RetryableFailure')""",
                            (token, current_time + LEASE_SECONDS, job["id"], job["metadata_hash"], current_time, current_time))
    return token if cursor.rowcount == 1 else None


def _book_lease(repo, job: dict, book_id: str, token: str, current_time: float) -> bool:
    with repo.connect() as db:
        cursor = db.execute("""INSERT INTO komga_auto_book_leases VALUES (?,?,?,?,?)
            ON CONFLICT(server_url,library_id,book_id) DO UPDATE SET owner=excluded.owner,lease_until=excluded.lease_until
            WHERE lease_until<=? OR owner=?""",
                            (job["server_url"], job["library_id"], book_id, token, current_time + LEASE_SECONDS, current_time, token))
    return cursor.rowcount == 1


def _release(repo, job_id: str, token: str) -> None:
    with repo.connect() as db:
        db.execute("UPDATE komga_auto_jobs SET lease_token='',lease_until=0 WHERE id=? AND lease_token=?", (job_id, token))
        db.execute("DELETE FROM komga_auto_book_leases WHERE owner=?", (token,))


def _renew_leases(repo, job: dict, token: str, book_id: str = "") -> None:
    with repo.connect() as db:
        lease_until = time.time() + LEASE_SECONDS
        cursor = db.execute("UPDATE komga_auto_jobs SET lease_until=? WHERE id=? AND lease_token=? AND metadata_hash=? AND stale=0",
                            (lease_until, job["id"], token, job["metadata_hash"]))
        if cursor.rowcount != 1:
            raise AutoSyncIssue("任务已被较新的审核版本替代，本次已停止。", "AUTO_STALE", "NeedsManualReview")
        if book_id:
            cursor = db.execute("""UPDATE komga_auto_book_leases SET lease_until=?
                WHERE server_url=? AND library_id=? AND book_id=? AND owner=?""",
                                (lease_until, job["server_url"], job["library_id"], book_id, token))
            if cursor.rowcount != 1:
                raise AutoSyncIssue("对应 Book 正由另一同步任务处理，本次已停止。", "AUTO_LEASE", "RetryableFailure")


def _guard(repo, job: dict, token: str, cancel: Event | None, *, book_id: str = "",
           full_artifact: bool = False) -> None:
    # Cheap boundaries compare the fixed per-attempt stat fingerprint. Full SHA
    # remains mandatory at attempt start, immediately before each PATCH backup,
    # and before success. No-op attempts still hash at both ends.
    _renew_leases(repo, job, token, book_id)
    _current(repo, job, full_artifact=full_artifact, cancel=cancel)
    if full_artifact:
        # A slow NAS read may outlive its lease or overlap a new local edit. Never
        # let successful hashing revive a worker whose ownership was taken over.
        _renew_leases(repo, job, token, book_id)
        _current(repo, job, artifact=False, cancel=cancel)


def _backup(repo, job: dict, token: str, cancel: Event | None, record: dict) -> None:
    _guard(repo, job, token, cancel, book_id=record["bookId"], full_artifact=True)
    with repo.connect() as db:
        db.execute("INSERT INTO komga_auto_backups(job_id,comic_id,metadata_hash,created_at,detail) VALUES (?,?,?,?,?)",
                   (job["id"], job["comic_id"], job["metadata_hash"], _stamp(), _json(record)))


def _failure(repo, job: dict, token: str, error: Exception, current_time: float) -> str:
    state = getattr(error, "state", None)
    code = getattr(error, "code", "")
    http_status = getattr(error, "http_status", None)
    if state not in ("PendingIndex", "NeedsManualReview", "RetryableFailure", "PermanentFailure"):
        if code == "KOMGA_NETWORK" or code in ("KOMGA_RATE_LIMIT", "KOMGA_SERVER") or http_status == 429 or (http_status and 500 <= http_status <= 599):
            state = "RetryableFailure"
        elif isinstance(error, KomgaError):
            state = "PermanentFailure"
        else:
            state = "NeedsManualReview"
    attempts = job["attempts"] + 1
    delay = min(3600, 15 * 2 ** min(attempts - 1, 8))
    if state == "PendingIndex":
        delay = min(900, max(30, delay))
    retry_after = getattr(error, "retry_after", None)
    if isinstance(retry_after, (int, float)) and math.isfinite(retry_after) and retry_after > 0:
        delay = max(delay, retry_after)
    next_attempt = current_time + delay if state in DUE_STATES else 0
    message = str(error)[:2000]
    if next_attempt and (not math.isfinite(next_attempt) or next_attempt > MAX_SCHEDULE_TIMESTAMP):
        state, next_attempt = "PermanentFailure", 0
        message = "服务器要求的重试时间超出可表示范围，已停止自动重试，请检查服务端限流配置。"
    with repo.connect() as db:
        db.execute("""UPDATE komga_auto_jobs SET status=?,attempts=?,last_error=?,next_attempt=?,updated_at=?
            WHERE id=? AND lease_token=?""", (state, attempts, message, next_attempt, _stamp(current_time), job["id"], token))
        if code in ("KOMGA_AUTH", "KOMGA_FORBIDDEN"):
            # An invalid connection must not walk the rest of a large queue one
            # rejected request per scheduler tick. Explicit Retry resumes only
            # the user's selected jobs after they repair their configuration.
            db.execute("""UPDATE komga_auto_jobs SET status='PermanentFailure',next_attempt=0,
                last_error=?,updated_at=? WHERE server_url=? AND library_id=?
                AND status IN ('PendingIndex','PendingSync','RetryableFailure')""",
                       ("当前 Komga 连接或权限无效，尚未继续请求；请修复配置后勾选任务重试。",
                        _stamp(current_time), job["server_url"], job["library_id"]))
        elif (next_attempt and isinstance(retry_after, (int, float)) and math.isfinite(retry_after)
              and retry_after > 0 and state == "RetryableFailure"):
            # Retry-After concerns the server, not only the one Book that happened
            # to receive 429. Other due jobs in this scope wait for that hint too.
            db.execute("""UPDATE komga_auto_jobs SET next_attempt=MAX(next_attempt,?)
                WHERE server_url=? AND library_id=? AND status IN ('PendingIndex','PendingSync','RetryableFailure')""",
                       (next_attempt, job["server_url"], job["library_id"]))
    return state


def run_due(repo, url: str, api_key: str, library_id: str, cancel: Event | None = None,
            progress: Callable[[str], None] | None = None, *, limit: int = 1,
            current_time: float | None = None) -> dict:
    """Process only due tasks in the supplied scope. Never wait for backoff."""
    _schema(repo)
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("自动同步每次仅处理 1 至 5 项任务。")
    client = KomgaClient(url, api_key, cancel=cancel)
    if not isinstance(library_id, str) or not library_id:
        raise ValueError("请先选择实际的 Komga Library ID。")
    with _lock_guard:
        mutex = _runner_locks.setdefault(str(repo.path.resolve()), Lock())
    result = {"processed": 0, "synced": 0, "busy": False, "cancelled": False, "jobs": []}
    if not mutex.acquire(blocking=False):
        result["busy"] = True
        return result
    now = time.time() if current_time is None else current_time
    try:
        with repo.connect() as db:
            due = [dict(row) for row in db.execute("""SELECT * FROM komga_auto_jobs
                WHERE server_url=? AND library_id=? AND stale=0 AND status IN ('PendingIndex','PendingSync','RetryableFailure')
                AND next_attempt<=? AND lease_until<=? ORDER BY next_attempt,created_at,comic_id LIMIT ?""",
                                                  (client.url, library_id, now, now, limit))]
        # No due tasks means no request, including no credential-bearing health check.
        for job in due:
            check_cancel(cancel)
            token = _claim(repo, job, now)
            if not token:
                continue
            try:
                _guard(repo, job, token, cancel, full_artifact=True)
                if progress:
                    progress("正在自动同步实体 · " + job["title"])
                if sum(item["id"] == library_id for item in client.libraries()) != 1:
                    raise AutoSyncIssue("自动同步的 Komga 书库不存在或无权访问，请重新配置。", "AUTO_LIBRARY", "PermanentFailure")
                book = match_book(client.books(library_id), library_id, job["relative_path"], job["title"])
                if job["book_id"] and job["book_id"] != book["id"]:
                    raise AutoSyncIssue("原 Book ID 已被替换，请人工核对目标漫画。", "AUTO_IDENTITY", "NeedsManualReview")
                if not _book_lease(repo, job, book["id"], token, now):
                    with repo.connect() as db:
                        db.execute("UPDATE komga_auto_jobs SET next_attempt=? WHERE id=? AND lease_token=?", (now + 20, job["id"], token))
                    result["busy"] = True
                    continue
                with repo.connect() as db:
                    db.execute("UPDATE komga_auto_jobs SET status='PendingSync',book_id=?,series_id=?,updated_at=? WHERE id=? AND lease_token=?",
                               (book["id"], book["seriesId"], _stamp(now), job["id"], token))
                metadata = Metadata.from_dict(json.loads(job["metadata_json"]))
                synced = synchronize_entities(client, metadata, library_id, job["relative_path"], book=book,
                                              expected_book_id=job["book_id"] or None,
                                              backup=lambda record: _backup(repo, job, token, cancel, record),
                                              guard=lambda: _guard(repo, job, token, cancel, book_id=book["id"]))
                _guard(repo, job, token, cancel, book_id=book["id"], full_artifact=True)
                with repo.connect() as db:
                    cursor = db.execute("""UPDATE komga_auto_jobs SET status='Synced',last_error='',next_attempt=0,
                        book_id=?,series_id=?,synced_at=?,updated_at=?,target_hash=?,last_snapshot=?
                        WHERE id=? AND lease_token=? AND metadata_hash=?""",
                                        (synced["bookId"], synced["seriesId"], _stamp(now), _stamp(now), synced["targetHash"],
                                         _json({"book": synced["snapshot"], "series": synced.get("seriesSnapshot")}),
                                         job["id"], token, job["metadata_hash"]))
                result["synced"] += int(cursor.rowcount == 1)
                result["processed"] += 1
            except Cancelled:
                result["cancelled"] = True
                break
            except (AutoSyncIssue, KomgaError, ValueError, OSError, sqlite3.Error) as error:
                _failure(repo, job, token, error, now)
                result["processed"] += 1
                # Permission failures are configuration-wide: do not hit more jobs
                # in this pass. Each other pending task keeps its independent state.
                if getattr(error, "code", "") in ("KOMGA_AUTH", "KOMGA_FORBIDDEN"):
                    break
            finally:
                _release(repo, job["id"], token)
    except Cancelled:
        result["cancelled"] = True
    finally:
        mutex.release()
    result["jobs"] = list_jobs(repo)
    return result


class KomgaJobs:
    """Small facade for service ownership; all state remains in the catalog."""
    def __init__(self, repo):
        self.repo = repo
        _schema(repo)

    def list(self):
        return list_jobs(self.repo)

    def invalidate(self, identities=None):
        return invalidate_jobs(self.repo, identities)

    def record_approval(self, comic_id):
        return record_approval(self.repo, comic_id)

    def record_validated_export(self, comic_id, output_root):
        return record_validated_export(self.repo, comic_id, output_root)

    def enqueue_export(self, comic_id, output_root, url, library_id, *, cancel=None):
        return enqueue_export(self.repo, comic_id, output_root, url, library_id, cancel=cancel)

    def retry(self, identities, url, library_id, *, cancel=None):
        return retry_jobs(self.repo, identities, url, library_id, cancel=cancel)

    def run_due(self, url, api_key, library_id, cancel=None, progress=None, *, limit=1):
        return run_due(self.repo, url, api_key, library_id, cancel, progress, limit=limit)
