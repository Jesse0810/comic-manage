"""Read-only review previews and one-use confirmation of reviewed Komga writes."""
from __future__ import annotations

from collections import Counter, OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
from threading import Event
import time
from typing import Callable

from .komga_client import KomgaClient, KomgaError
from .komga_remote import SERIES_FIELDS, fetch_snapshot, require_scope, snapshot_fingerprint, validate_changes
from .komga_sync import _relative_path, _same_path, _valid_book, synchronize
from .sources import Cancelled, check_cancel, inspect_source

PLAN_TTL_SECONDS = 15 * 60
MAX_PLANS = 32
STOP_CODES = frozenset({"KOMGA_AUTH", "KOMGA_FORBIDDEN", "KOMGA_REDIRECT"})


@dataclass
class CheckedItem:
    identity: str
    state: dict
    book_id: str
    series_id: str
    remote_path: str
    snapshot_hash: str


@dataclass
class ReviewPlan:
    created_at: float
    server_url: str
    library_id: str
    credential_hash: str
    items: list[dict]
    checked: dict[str, CheckedItem]


class BatchReview:
    def __init__(self, repo, progress: Callable[[str], None]):
        self.repo = repo
        self.progress = progress
        self.plans: OrderedDict[str, ReviewPlan] = OrderedDict()

    def _row(self, identity: str) -> dict:
        row = {"id": identity, "title": identity, "kind": "", "status": "blocked", "message": "尚未检查。", "authors": [], "genre": "", "ageRating": "Unknown"}
        try:
            comic = self.repo.get(identity)
            row.update(title=comic.metadata.display_title, kind=comic.kind, authors=list(comic.metadata.authors), genre=comic.metadata.genre, ageRating=comic.metadata.age_rating)
            if comic.kind == "komga":
                source = self.repo.get_komga_source(identity)
                row["dirtyFields"] = list(source["dirty_fields"]) if source else []
        except ValueError as error:
            row["message"] = str(error)
        return row

    @staticmethod
    def _books(client: KomgaClient, library_id: str) -> list[dict]:
        if sum(item["id"] == library_id for item in client.libraries()) != 1:
            raise KomgaError("配置的 Komga 书库不存在或无权访问，请重新选择。", "KOMGA_LIBRARY")
        return client.books(library_id)

    def _check(self, identity: str, client: KomgaClient, library_id: str, books: list[dict], cancel: Event) -> CheckedItem:
        check_cancel(cancel)
        state = self.repo.review_state(identity)
        comic = self.repo.get(identity)
        if comic.kind == "komga":
            source = require_scope(self.repo.get_komga_source(identity), client, library_id)
            if source["unavailable"] or source["conflict"]:
                raise ValueError("远端记录不可用或存在冲突，请重新读取并处理后再审核。")
            comic.metadata.validate_review()
            validate_changes(comic.metadata, set(source["dirty_fields"]))
            matches = [book for book in books if book["id"] == source["book_id"] and not book.get("deleted") and book.get("libraryId") == library_id and book.get("seriesId") == source["series_id"] and _same_path(book.get("url", ""), source["remote_path"])]
        elif comic.kind in ("folder", "cbz"):
            snapshot = inspect_source(Path(comic.source))
            if str(snapshot.path) != comic.source or snapshot.fingerprint != comic.fingerprint:
                raise ValueError("源文件已变化，请重新扫描、检查并审核。")
            comic.metadata.validate(len(snapshot.pages))
            relative = _relative_path(comic)
            mapping = self.repo.komga_mapping(client.url, library_id, identity)
            mapped = next((book for book in books if mapping and book["id"] == mapping["book_id"]), None)
            matches = [mapped] if mapped and _valid_book(mapped, library_id, relative) and _same_path(mapped.get("url", ""), mapping["remote_path"]) else [book for book in books if _valid_book(book, library_id, relative)]
        else:
            raise ValueError("作品来源类型无效，请重新读取列表。")
        if len(matches) != 1:
            raise ValueError(f"在当前书库精确匹配到 {len(matches)} 本漫画，需唯一匹配后才能审核并同步；本功能不会生成或上传漫画。")
        target = matches[0]
        observed = fetch_snapshot(client, {"book_id": target["id"], "library_id": library_id})
        live = observed["book"]
        if live["seriesId"] != target["seriesId"] or not _same_path(live.get("url", ""), target.get("url", "")):
            raise ValueError("远端漫画归属或路径在检查期间变化，请重新读取后再试。")
        snapshot_hash = snapshot_fingerprint(observed)
        if comic.kind == "komga" and snapshot_hash != snapshot_fingerprint(source["snapshot"]):
            raise ValueError("远端资料已变化，请重新读取并处理冲突后再审核。")
        check_cancel(cancel)
        if state != self.repo.review_state(identity):
            raise ValueError("本机资料在检查期间发生变化，请重新预览确认。")
        return CheckedItem(identity, state, target["id"], live["seriesId"], live["url"], snapshot_hash)

    @staticmethod
    def _credential_hash(api_key: str) -> str:
        return hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()

    def prepare(self, identities: list[str], url: str, api_key: str, library_id: str, cancel: Event) -> dict:
        client = KomgaClient(url, api_key, cancel=cancel)
        rows = [self._row(identity) for identity in identities]
        result = {"token": "", "total": len(rows), "ready": 0, "blocked": len(rows), "cancelled": False, "items": rows, "warnings": []}
        checked = {}
        books = []
        stopped = ""
        try:
            self.progress("正在读取书库，准备批量审核预览…")
            books = self._books(client, library_id)
            for index, row in enumerate(rows, 1):
                check_cancel(cancel)
                self.progress(f"正在检查 {index}/{len(rows)} 本 · {row['title']}")
                try:
                    item = self._check(row["id"], client, library_id, books, cancel)
                    checked[row["id"]] = item
                    metadata = item.state["metadata"]
                    row.update(title=metadata["title"], kind=item.state["kind"], authors=list(metadata["authors"]), genre=metadata["genre"], ageRating=metadata["age_rating"])
                    if item.state["remote"]:
                        row["dirtyFields"] = json.loads(item.state["remote"]["dirty_fields"])
                    row.update(status="ready", message="校验通过，确认后审核并同步。")
                except KomgaError as error:
                    row["message"] = str(error)
                    if error.code in STOP_CODES:
                        raise
                except (ValueError, OSError) as error:
                    row["message"] = str(error)
            check_cancel(cancel)
        except Cancelled:
            result["cancelled"] = True
            stopped = "预览已取消，请重新检查后确认。"
        except KomgaError as error:
            stopped = "预览因连接或权限问题中止，请重新检查。"
            result["error"] = str(error)
            result["warnings"].append(str(error))
        if stopped:
            checked.clear()
            for row in rows:
                if row["status"] == "ready" or row["message"] == "尚未检查。":
                    row.update(status="blocked", message=stopped)
        counts = Counter(item.book_id for item in checked.values())
        for row in rows:
            item = checked.get(row["id"])
            if item and counts[item.book_id] > 1:
                row.update(status="blocked", message="多条所选记录指向同一本 Komga 漫画，请仅保留一条再预览。")
                checked.pop(row["id"])
        result["ready"] = len(checked)
        result["blocked"] = len(rows) - len(checked)
        for series_id in {item.series_id for item in checked.values()}:
            group = [item for item in checked.values() if item.series_id == series_id]
            shared_dirty = any(not item.state["remote"] or set(json.loads(item.state["remote"]["dirty_fields"])) &
                               (SERIES_FIELDS | {"genre", "age_rating"} | ({"title", "summary"} if item.state["metadata"]["collection_mode"] == "oneshot" else set())) for item in group)
            members = {book["id"] for book in books if book.get("seriesId") == series_id and book.get("libraryId") == library_id and not book.get("deleted")}
            if shared_dirty and members != {item.book_id for item in group}:
                name = group[0].state["metadata"]["series"] or group[0].state["metadata"]["title"]
                result["warnings"].append(f"系列“{name}”尚未选全可审核漫画，系列共享字段可能跳过；逐册字段仍会同步，以实际同步结果为准。")
        if checked:
            timestamp = time.monotonic()
            self.plans = OrderedDict((token, plan) for token, plan in self.plans.items() if timestamp - plan.created_at <= PLAN_TTL_SECONDS)
            while len(self.plans) >= MAX_PLANS:
                self.plans.popitem(last=False)
            token = secrets.token_urlsafe(32)
            self.plans[token] = ReviewPlan(timestamp, client.url, library_id, self._credential_hash(api_key), deepcopy(rows), checked)
            result["token"] = token
        return result

    def commit(self, token: str, url: str, api_key: str, library_id: str, cancel: Event) -> dict:
        plan = self.plans.pop(token, None)
        if plan is None:
            raise KomgaError("审核预览不存在或已使用，请重新预览确认。", "REVIEW_PLAN_INVALID")
        if time.monotonic() - plan.created_at > PLAN_TTL_SECONDS:
            raise KomgaError("审核预览已过期，请重新预览确认。", "REVIEW_PLAN_EXPIRED")
        client = KomgaClient(url, api_key, cancel=cancel)
        if client.url != plan.server_url or library_id != plan.library_id or not hmac.compare_digest(self._credential_hash(api_key), plan.credential_hash):
            raise KomgaError("Komga 连接配置在预览后变化，请重新预览确认。", "REVIEW_PLAN_SCOPE")
        rows = [{"id": row["id"], "title": row["title"], "status": "skipped", "message": row["message"] if row["status"] == "blocked" else "尚未审核。"} for row in plan.items]
        result = {"total": len(rows), "reviewed": 0, "skipped": 0, "failed": 0, "cancelled": False, "items": rows, "sync": None}
        valid = {}
        approved = []
        stopped = False
        try:
            self.progress("正在重新校验已确认的审核预览…")
            books = self._books(client, library_id)
            for row in rows:
                expected = plan.checked.get(row["id"])
                if expected is None:
                    continue
                check_cancel(cancel)
                try:
                    current = self._check(row["id"], client, library_id, books, cancel)
                    if current != expected:
                        raise ValueError("作品资料、源文件或远端资料在预览后变化，请重新预览确认。")
                    valid[row["id"]] = current
                except KomgaError as error:
                    row.update(status="failed", message=str(error))
                    if error.code in STOP_CODES:
                        raise
                except (ValueError, OSError) as error:
                    row["message"] = str(error)
            check_cancel(cancel)
            for row in rows:
                item = valid.get(row["id"])
                if item is None:
                    continue
                check_cancel(cancel)
                self.progress(f"正在确认审核 · {row['title']}")
                try:
                    if item.state["kind"] != "komga":
                        snapshot = inspect_source(Path(item.state["source"]))
                        if snapshot.fingerprint != item.state["fingerprint"]:
                            raise ValueError("源文件在预览后发生变化，本次已跳过。")
                    status = self.repo.approve_checked(row["id"], item.state, cancel)
                    row.update(status="approved", message="本次确认通过，保持已生成状态并进入同步。" if status == "packaged" else "本次确认通过，进入同步。")
                    approved.append(row["id"])
                except ValueError as error:
                    row["message"] = str(error)
                except (OSError, sqlite3.Error) as error:
                    row.update(status="failed", message=str(error))
            check_cancel(cancel)
            if approved:
                self.progress("批量审核已完成，正在同步本次通过的漫画…")
                guards = {identity: {"bookId": valid[identity].book_id, "seriesId": valid[identity].series_id,
                                    "path": valid[identity].remote_path, "snapshotHash": valid[identity].snapshot_hash,
                                    "serverUrl": client.url, "libraryId": library_id,
                                    **{field: valid[identity].state[field] for field in ("metadata", "kind", "source", "fingerprint", "output")}}
                          for identity in approved}
                result["sync"] = synchronize(self.repo, approved, client.url, api_key, library_id, cancel, self.progress, expected_targets=guards)
                result["cancelled"] = result["sync"]["cancelled"]
                if result["sync"].get("error"):
                    result["error"] = result["sync"]["error"]
        except Cancelled:
            result["cancelled"] = True
            stopped = True
        except KomgaError as error:
            result["error"] = str(error)
            stopped = True
        if stopped:
            for row in rows:
                if row["id"] in plan.checked and row["status"] == "skipped" and row["message"] == "尚未审核。":
                    row["message"] = "已取消，尚未审核。" if result["cancelled"] else "连接或权限检查中止，尚未审核。"
        result["reviewed"] = len(approved)
        result["skipped"] = sum(row["status"] == "skipped" for row in rows)
        result["failed"] = sum(row["status"] == "failed" for row in rows)
        return result
