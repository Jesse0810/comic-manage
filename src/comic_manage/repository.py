"""SQLite catalog. Each operation owns its connection, safe for worker threads."""

from __future__ import annotations

from contextlib import contextmanager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .metadata import Metadata
from .sources import Snapshot, source_id


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Comic:
    id: str
    source: str
    kind: str
    fingerprint: str
    page_count: int
    metadata: Metadata
    status: str = "draft"
    output: str = ""
    warning: str = ""
    updated_at: str = ""
    remote: dict | None = None


class Repository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise ValueError("数据库版本比本程序新，请使用较新版本打开。")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS comics (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    page_count INTEGER NOT NULL, metadata TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft', output TEXT NOT NULL DEFAULT '',
                    warning TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, comic_id TEXT NOT NULL,
                    action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS removed_comics (
                    comic_id TEXT PRIMARY KEY, removed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS komga_mappings (
                    server_url TEXT NOT NULL, library_id TEXT NOT NULL, comic_id TEXT NOT NULL,
                    book_id TEXT NOT NULL, series_id TEXT NOT NULL, remote_path TEXT NOT NULL,
                    synced_at TEXT NOT NULL,
                    PRIMARY KEY (server_url, library_id, comic_id)
                );
                CREATE TABLE IF NOT EXISTS komga_sources (
                    comic_id TEXT PRIMARY KEY, server_url TEXT NOT NULL, library_id TEXT NOT NULL,
                    library_name TEXT NOT NULL, book_id TEXT NOT NULL, series_id TEXT NOT NULL,
                    remote_path TEXT NOT NULL, scope TEXT NOT NULL,
                    base_metadata TEXT NOT NULL, snapshot TEXT NOT NULL,
                    observed_snapshot TEXT NOT NULL DEFAULT '{}', dirty_fields TEXT NOT NULL DEFAULT '[]',
                    conflict INTEGER NOT NULL DEFAULT 0, unavailable INTEGER NOT NULL DEFAULT 0,
                    pending_sync INTEGER NOT NULL DEFAULT 0, synced_at TEXT NOT NULL DEFAULT '',
                    UNIQUE (server_url, library_id, book_id)
                );
                PRAGMA user_version = 1;
            """)
        self._migrate_metadata()

    def _migrate_metadata(self) -> None:
        # Back up before converting either an earlier catalog or the former Kavita rules.
        with self.connect() as db:
            legacy = [(row["id"], json.loads(row["metadata"]), row["warning"])
                      for row in db.execute("SELECT id, metadata, warning FROM comics")
                      if not {"title", "collection_mode", "localized_series_label"}.issubset(json.loads(row["metadata"]))]
        if not legacy:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        rule = "metadata-v1_1" if any("title" not in previous for _, previous, _ in legacy) else "komga-v1_1-K"
        backup = self.path.with_name(f"{self.path.stem}.before-{rule}-{stamp}.sqlite3")
        self.backup(backup)
        with self.connect() as db:
            for comic_id, previous, warning in legacy:
                metadata = Metadata.from_dict(previous)
                note = "已切换 Komga 元数据规范，原资料已自动备份，请核对独立作品或系列方式、本册编号、年龄分级与译者后重新审核。"
                if previous.get("age_rating", "Unknown") not in ("Unknown", "Everyone", "MA15+", "R18+", "Adults Only 18+", "全年龄", "R15", "R18"):
                    note += f" 原分级 {previous['age_rating']} 无法准确对应，已留待确认。"
                db.execute("UPDATE comics SET metadata=?, status='draft', warning=?, updated_at=? WHERE id=?", (
                    json.dumps(metadata.to_dict(), ensure_ascii=False), "；".join(filter(None, [warning, note])), now(), comic_id))
                self._event(db, comic_id, "metadata_rules_migration", json.dumps({"backup": str(backup), "previous": previous}, ensure_ascii=False))
            db.execute("INSERT INTO settings VALUES ('metadata_rules', '1.1-K') ON CONFLICT(key) DO UPDATE SET value=excluded.value")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _decode(row) -> Comic:
        value = dict(row)
        value["metadata"] = Metadata.from_dict(json.loads(value["metadata"]))
        return Comic(**value)

    def all(self, *, include_removed: bool = False) -> list[Comic]:
        with self.connect() as db:
            condition = "" if include_removed else " WHERE id NOT IN (SELECT comic_id FROM removed_comics)"
            comics = [self._decode(row) for row in db.execute(
                "SELECT * FROM comics" + condition + " ORDER BY updated_at DESC, id")]
            sources = {row["comic_id"]: self._source_decode(row) for row in db.execute("SELECT * FROM komga_sources")}
        for comic in comics:
            if comic.id in sources:
                comic.remote = self._source_public(sources[comic.id])
        return comics

    def get(self, comic_id: str, *, include_removed: bool = False) -> Comic:
        with self.connect() as db:
            condition = "" if include_removed else " AND id NOT IN (SELECT comic_id FROM removed_comics)"
            row = db.execute("SELECT * FROM comics WHERE id=?" + condition, (comic_id,)).fetchone()
        if not row:
            raise ValueError("作品记录不存在。")
        comic = self._decode(row)
        if comic.kind == "komga":
            source = self.get_komga_source(comic_id)
            if source:
                comic.remote = self._source_public(source)
        return comic

    @staticmethod
    def _source_decode(row) -> dict:
        source = dict(row)
        for field in ("base_metadata", "snapshot", "observed_snapshot", "dirty_fields"):
            source[field] = json.loads(source[field])
        for field in ("conflict", "unavailable", "pending_sync"):
            source[field] = bool(source[field])
        return source

    @staticmethod
    def _source_public(source: dict) -> dict:
        snapshot = source["observed_snapshot"] or source["snapshot"]
        shared = (snapshot.get("series") or {}).get("metadata") or {}
        return {"serverUrl": source["server_url"], "libraryId": source["library_id"],
                "libraryName": source["library_name"], "bookId": source["book_id"],
                "seriesId": source["series_id"], "scope": source["scope"],
                "hasLocalChanges": bool(source["dirty_fields"]), "conflict": source["conflict"],
                "unavailable": source["unavailable"], "pendingSync": source["pending_sync"],
                "syncedAt": source["synced_at"], "dirtyFields": source["dirty_fields"],
                "sharedMetadata": {key: shared.get(key) for key in ("title", "ageRating", "genres", "publisher", "language", "readingDirection", "totalBookCount", "alternateTitles")}}

    def get_komga_source(self, comic_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM komga_sources WHERE comic_id=?", (comic_id,)).fetchone()
        return self._source_decode(row) if row else None

    def upsert_komga(self, identity: str, source: dict, metadata: Metadata, snapshot: dict,
                     fingerprint: str, page_count: int, warning: str) -> str:
        encoded = json.dumps(snapshot, ensure_ascii=False)
        with self.connect() as db:
            if db.execute("SELECT 1 FROM removed_comics WHERE comic_id=?", (identity,)).fetchone():
                return "skipped"
            previous = db.execute("SELECT * FROM comics WHERE id=?", (identity,)).fetchone()
            remote_row = db.execute("SELECT * FROM komga_sources WHERE comic_id=?", (identity,)).fetchone()
            if previous and remote_row:
                remote = self._source_decode(remote_row)
                changed = previous["fingerprint"] != fingerprint
                protected = bool(remote["dirty_fields"] or remote["pending_sync"] or remote["conflict"])
                if protected:
                    conflict = changed or remote["conflict"]
                    db.execute("UPDATE komga_sources SET observed_snapshot=?,conflict=?,unavailable=0,library_name=? WHERE comic_id=?",
                               (encoded if changed else "{}", int(conflict), source["library_name"], identity))
                    if conflict:
                        db.execute("UPDATE comics SET status='draft',warning=?,updated_at=? WHERE id=?",
                                   ("远端资料已变化。本机未同步的编辑已保留，请处理冲突后重新审核。", now(), identity))
                    return "kept"
                if not changed and not remote["unavailable"]:
                    return "kept"
                db.execute("UPDATE comics SET metadata=?,fingerprint=?,page_count=?,status='draft',warning=?,updated_at=? WHERE id=?",
                           (json.dumps(metadata.to_dict(), ensure_ascii=False), fingerprint, page_count, warning, now(), identity))
                db.execute("UPDATE komga_sources SET series_id=?,remote_path=?,library_name=?,base_metadata=?,snapshot=?,observed_snapshot='{}',conflict=0,unavailable=0,pending_sync=0 WHERE comic_id=?",
                           (source["series_id"], source["remote_path"], source["library_name"], json.dumps(metadata.to_dict(), ensure_ascii=False), encoded, identity))
                self._event(db, identity, "komga_refresh", "读取远端最新资料。")
                return "updated"
            db.execute("INSERT INTO comics (id,source,kind,fingerprint,page_count,metadata,warning,updated_at) VALUES (?,?,'komga',?,?,?,?,?)",
                       (identity, source["source"], fingerprint, page_count, json.dumps(metadata.to_dict(), ensure_ascii=False), warning, now()))
            db.execute("INSERT INTO komga_sources (comic_id,server_url,library_id,library_name,book_id,series_id,remote_path,scope,base_metadata,snapshot) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (identity, source["server_url"], source["library_id"], source["library_name"], source["book_id"], source["series_id"], source["remote_path"], source["scope"], json.dumps(metadata.to_dict(), ensure_ascii=False), encoded))
            self._event(db, identity, "komga_pull", "从 Komga 书库读取资料。")
            return "added"

    def mark_komga_missing(self, scope: str, seen: set[str]) -> int:
        count = 0
        with self.connect() as db:
            for row in db.execute("SELECT comic_id,book_id FROM komga_sources WHERE scope=?", (scope,)).fetchall():
                if row["book_id"] not in seen:
                    db.execute("UPDATE komga_sources SET unavailable=1 WHERE comic_id=?", (row["comic_id"],))
                    db.execute("UPDATE comics SET status='draft',warning=?,updated_at=? WHERE id=?",
                               ("远端漫画已移除或不可访问，本机资料和编辑仍保留。", now(), row["comic_id"]))
                    count += 1
        return count

    def note_komga_conflict(self, comic_id: str, snapshot: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE komga_sources SET observed_snapshot=?,conflict=1,pending_sync=0 WHERE comic_id=?",
                       (json.dumps(snapshot, ensure_ascii=False), comic_id))
            db.execute("UPDATE comics SET status='draft',warning=?,updated_at=? WHERE id=?",
                       ("远端资料在读取后发生变化，请处理冲突后重新审核。", now(), comic_id))

    def rebase_komga(self, comic_id: str, metadata: Metadata, base: Metadata, snapshot: dict,
                    fingerprint: str, dirty_fields: list[str], *, action: str, synced: bool = False) -> None:
        with self.connect() as db:
            previous = db.execute("SELECT metadata,status FROM comics WHERE id=?", (comic_id,)).fetchone()
            self._event(db, comic_id, action, previous["metadata"])
            book = snapshot["book"]
            db.execute("UPDATE comics SET metadata=?,fingerprint=?,page_count=?,status=?,warning='',updated_at=? WHERE id=?",
                       (json.dumps(metadata.to_dict(), ensure_ascii=False), fingerprint, book["media"]["pagesCount"], previous["status"] if synced else "draft", now(), comic_id))
            db.execute("UPDATE komga_sources SET base_metadata=?,snapshot=?,observed_snapshot='{}',dirty_fields=?,conflict=0,unavailable=0,pending_sync=0,series_id=?,remote_path=?,synced_at=CASE WHEN ? THEN ? ELSE synced_at END WHERE comic_id=?",
                       (json.dumps(base.to_dict(), ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False), json.dumps(dirty_fields), book["seriesId"], book["url"], int(synced), now(), comic_id))

    def approve_komga(self, comic_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE comics SET status='approved',updated_at=? WHERE id=?", (now(), comic_id))
            db.execute("UPDATE komga_sources SET pending_sync=1 WHERE comic_id=?", (comic_id,))
            self._event(db, comic_id, "komga_approve", "已在线核对远端快照并审核本机修改。")

    def is_removed(self, comic_id: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM removed_comics WHERE comic_id=?", (comic_id,)).fetchone() is not None

    def upsert_scan(self, snapshot: Snapshot, *, restore: bool = False,
                    defaults: dict | None = None, overwrite_existing: bool = False) -> str | None:
        comic_id = source_id(snapshot.path)
        metadata, warning = snapshot.initial_metadata()
        defaults = defaults or {}
        with self.connect() as db:
            removed = db.execute("SELECT 1 FROM removed_comics WHERE comic_id=?", (comic_id,)).fetchone()
            if removed and not restore:
                return None
            previous = db.execute("SELECT * FROM comics WHERE id=?", (comic_id,)).fetchone()
            if previous:
                if previous["fingerprint"] != snapshot.fingerprint:
                    db.execute("""UPDATE comics SET fingerprint=?, page_count=?, status='draft',
                        warning=?, updated_at=? WHERE id=?""", (
                        snapshot.fingerprint, len(snapshot.pages),
                        "源文件已变化，请重新预览并审核。" + warning, now(), comic_id))
                    self._event(db, comic_id, "source_changed", snapshot.fingerprint)
                if removed:
                    db.execute("DELETE FROM removed_comics WHERE comic_id=?", (comic_id,))
                    db.execute("UPDATE comics SET status='draft', updated_at=? WHERE id=?", (now(), comic_id))
                    self._event(db, comic_id, "restore", str(snapshot.path))
                if defaults and overwrite_existing:
                    metadata = Metadata.from_dict(json.loads(previous["metadata"]))
                    for key, value in defaults.items():
                        setattr(metadata, key, value)
                    db.execute("UPDATE comics SET metadata=?, status='draft', updated_at=? WHERE id=?", (
                        json.dumps(metadata.to_dict(), ensure_ascii=False), now(), comic_id))
                    self._event(db, comic_id, "batch_edit", "导入时批量更新作者或类型")
                return comic_id
            for key, value in defaults.items():
                setattr(metadata, key, value)
            db.execute("""INSERT INTO comics
                (id, source, kind, fingerprint, page_count, metadata, warning, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
                comic_id, str(snapshot.path), snapshot.kind, snapshot.fingerprint,
                len(snapshot.pages), json.dumps(metadata.to_dict(), ensure_ascii=False), warning, now()))
            self._event(db, comic_id, "import", str(snapshot.path))
        return comic_id

    def remove(self, comic_ids: list[str]) -> None:
        """Hide catalog entries only. Source images and exported files are untouched."""
        with self.connect() as db:
            self._require_visible(db, comic_ids)
            if any(db.execute("SELECT kind FROM comics WHERE id=?", (identity,)).fetchone()[0] == "komga" for identity in comic_ids):
                raise ValueError("Komga 远端记录请保留在远端工作区中管理。")
            for comic_id in comic_ids:
                db.execute("INSERT INTO removed_comics (comic_id, removed_at) VALUES (?, ?)", (comic_id, now()))
                self._event(db, comic_id, "remove", "从漫画库移除，保留源文件、成品和人工资料")

    @staticmethod
    def _require_visible(db, comic_ids: list[str]):
        for comic_id in comic_ids:
            row = db.execute("SELECT 1 FROM comics WHERE id=? AND id NOT IN "
                             "(SELECT comic_id FROM removed_comics)", (comic_id,)).fetchone()
            if row is None:
                raise ValueError("作品记录不存在或已移除。")

    def batch_edit(self, comic_ids: list[str], changes: dict) -> int:
        count = 0
        fields = set(changes)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_visible(db, comic_ids)
            for comic_id in comic_ids:
                row = db.execute("SELECT metadata FROM comics WHERE id=?", (comic_id,)).fetchone()
                stored = json.loads(row["metadata"])
                before = Metadata.from_dict(stored).to_dict()
                metadata = Metadata.from_dict({**before, **changes})
                actual = {key: value for key, value in metadata.to_dict().items() if before[key] != value}
                try:
                    # Canonicalization is allowed for checked fields only.
                    unselected = actual.keys() - fields
                    if "pencillers" in unselected:
                        raise ValueError("作者和已有绘画者重名，请同时勾选并调整独立绘画者。")
                    if "localized_series" in unselected:
                        raise ValueError("新系列名和已有系列别名相同，请同时勾选并调整系列别名。")
                    if unselected:
                        raise ValueError("本次填写会影响未勾选的字段，请检查批量编辑内容。")
                    if changes.get("uncensored") and metadata.age_rating != "R18+":
                        raise ValueError("只有 R18 作品可以设置为无修正。")
                    metadata.validate_fields(fields)
                except ValueError as error:
                    raise ValueError(f"《{before['title']}》{error}") from error
                if not actual:
                    continue
                self._record_komga_edit(db, comic_id, before, metadata.to_dict(), fields)
                db.execute("UPDATE comics SET metadata=?, status='draft', updated_at=? WHERE id=?", (
                    json.dumps({**stored, **actual}, ensure_ascii=False), now(), comic_id))
                self._event(db, comic_id, "batch_edit", f"批量修改 {', '.join(actual)}。需要重新审核。")
                count += 1
        return count

    @staticmethod
    def _record_komga_edit(db, comic_id: str, before: dict, after: dict, fields: set[str]) -> None:
        row = db.execute("SELECT base_metadata,dirty_fields FROM komga_sources WHERE comic_id=?", (comic_id,)).fetchone()
        if row is None:
            return
        if before["collection_mode"] != after["collection_mode"]:
            raise ValueError("远端作品方式由 Komga 文件目录决定，元数据编辑不能移动系列归属。")
        if before["cover_index"] != after["cover_index"]:
            raise ValueError("远端封面由 Komga 管理，当前只支持元数据编辑。")
        applicable = {field for field in fields if before.get(field) != after.get(field)}
        if after["collection_mode"] == "oneshot" and applicable & {"series", "number", "count", "localized_series", "localized_series_label"}:
            raise ValueError("One-Shot 的系列归属、编号和别名不能通过此编辑修改。")
        base = json.loads(row["base_metadata"])
        dirty = set(json.loads(row["dirty_fields"])) | fields
        dirty = sorted(field for field in dirty if after.get(field) != base.get(field))
        db.execute("UPDATE komga_sources SET dirty_fields=?,pending_sync=0 WHERE comic_id=?", (json.dumps(dirty), comic_id))

    def save(self, comic_id: str, metadata: Metadata, *, fields: set[str] | None = None) -> None:
        # Drafts may be incomplete. Every edit invalidates previous approval/export status.
        with self.connect() as db:
            row = db.execute("SELECT metadata FROM comics WHERE id=?", (comic_id,)).fetchone()
            before = Metadata.from_dict(json.loads(row["metadata"])).to_dict()
            after = metadata.to_dict()
            explicit = fields if fields is not None else {key for key, value in after.items() if before.get(key) != value}
            self._record_komga_edit(db, comic_id, before, after, explicit)
            db.execute("UPDATE comics SET metadata=?, status='draft', updated_at=? WHERE id=?", (
                json.dumps(metadata.to_dict(), ensure_ascii=False), now(), comic_id))
            self._event(db, comic_id, "edit", "人工修改元数据")

    def approve(self, comic_id: str, snapshot: Snapshot) -> None:
        comic = self.get(comic_id)
        if str(snapshot.path) != comic.source or snapshot.fingerprint != comic.fingerprint:
            raise ValueError("源文件已变化，请重新扫描、检查并审核。")
        comic.metadata.validate(len(snapshot.pages))
        with self.connect() as db:
            db.execute("UPDATE comics SET status='approved', warning='', updated_at=? WHERE id=?", (now(), comic_id))
            self._event(db, comic_id, "approve", snapshot.fingerprint)

    def mark_packaged(self, comic_id: str, output: Path, report: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE comics SET status='packaged', output=?, updated_at=? WHERE id=?", (str(output), now(), comic_id))
            self._event(db, comic_id, "package_verified", json.dumps(report, ensure_ascii=False))

    @staticmethod
    def _event(db, comic_id: str, action: str, detail: str):
        db.execute("INSERT INTO events (comic_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
                   (comic_id, action, detail, now()))

    def setting(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def komga_mapping(self, server_url: str, library_id: str, comic_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM komga_mappings WHERE server_url=? AND library_id=? AND comic_id=?",
                             (server_url, library_id, comic_id)).fetchone()
        return dict(row) if row else None

    def save_komga_mapping(self, server_url: str, library_id: str, comic_id: str,
                          book_id: str, series_id: str, remote_path: str) -> None:
        with self.connect() as db:
            db.execute("""INSERT INTO komga_mappings VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_url, library_id, comic_id) DO UPDATE SET
                book_id=excluded.book_id, series_id=excluded.series_id,
                remote_path=excluded.remote_path, synced_at=excluded.synced_at""",
                (server_url, library_id, comic_id, book_id, series_id, remote_path, now()))

    def backup(self, destination: Path):
        # SQLite backup API gives a consistent catalog even while other connections exist.
        if destination.exists():
            raise FileExistsError("备份文件已存在，请选择新文件名。")
        with destination.open("xb"):
            pass
        try:
            with self.connect() as db, closing(sqlite3.connect(destination)) as target:
                db.backup(target)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
