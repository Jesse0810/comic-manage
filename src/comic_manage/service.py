"""Local JSON-lines service for Electron, without Qt or a network listener."""
from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from queue import Empty, Queue
import secrets
import sqlite3
import sys
from threading import Event, Lock, Thread
import time
from typing import Callable, TextIO

from .metadata import AGE_RATINGS, GENRES, METADATA_RULES_VERSION, Metadata, to_comicinfo
from .scrape import scrape_preview
from .scrape_http import ScrapeError
from .model_assist import assist_preview, test_profile
from .library_agent import AgentError, LibraryAgent
from .model_transport import ModelTransportError
from .komga import komga_book_payload, komga_series_payload, komga_warnings
from .komga_manifest import export_manifest
from .komga_compare import compare_uploads
from .komga_client import KomgaError, test_connection
from .komga_sync import synchronize
from .komga_jobs import KomgaJobs, list_jobs, record_approval, record_validated_export, enqueue_export, retry_jobs, run_due
from .komga_remote import approve_remote, preview_changes, pull_library, resolve_remote
from .batch_review import BatchReview
from .packaging import ExportPlan, export_cbz, make_plan
from .repository import Comic, Repository
from .sources import (SKIP_DIRS, Cancelled, Snapshot, check_cancel, discover, inspect_source,
                      is_inside, is_link, natural_key, normalized_path, source_id)

MAX_PAGE_BYTES = 32 * 1024 * 1024
MAX_REQUEST_CHARS = 8 * 1024 * 1024
PLAN_TTL_SECONDS = 15 * 60
LEGACY_VARIANT_CHECK_SECONDS = 5.0
BATCH_TEXT_FIELDS = frozenset({
    "series", "localized_series", "number", "count", "volume", "translator", "genre", "work_group",
    "publication_format", "year", "month", "day", "language", "localized_series_label", "publisher",
})
BATCH_LIST_FIELDS = frozenset({"pencillers", "original_works", "characters", "web", "tags", "release_methods", "release_platforms"})
BATCH_CHOICES = {
    "collection_mode": ("oneshot", "series"),
    "release_type": ("", "同人发行", "商业出版", "个人独立发布"),
    "age_rating": AGE_RATINGS,
    "color": ("", "黑白", "全彩", "部分彩色"),
    "reading_direction": ("", "LEFT_TO_RIGHT", "RIGHT_TO_LEFT", "VERTICAL", "WEBTOON"),
    "text_version": ("", "有文字", "无文字"),
    "manga": ("Unknown", "Yes", "No", "YesAndRightToLeft"),
}
PAGE_MIMES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
              ".tif": "image/tiff", ".tiff": "image/tiff", ".avif": "image/avif"}


class ServiceError(ValueError):
    def __init__(self, message: str, code: str = "INVALID_ARGUMENT"):
        super().__init__(message)
        self.code = code


@dataclass
class PreparedExport:
    created_at: float
    output_root: Path
    plans: list[ExportPlan]
    comparison_scope: tuple[str, str] | None = None
    findings: dict | None = None


@dataclass
class PreparedImport:
    created_at: float
    snapshots: dict[str, Snapshot]


def _string(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ServiceError(f"{name} 必须是有效文本。")
    return value


def _strings(value, name: str) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 10_000:
        raise ServiceError(f"{name} 必须是有效列表。")
    return list(dict.fromkeys(_string(v, name) for v in value))


def _path(value, name: str) -> Path:
    result = Path(_string(value, name)).expanduser()
    if not result.is_absolute():
        raise ServiceError(f"{name} 必须是完整路径。")
    return Path(os.path.abspath(result))


def _metadata(value, previous: Metadata | None = None) -> Metadata:
    if not isinstance(value, dict):
        raise ServiceError("metadata 必须是作品资料对象。")
    merged = previous.to_dict() if previous else Metadata().to_dict()
    for key, item in value.items():
        if key not in merged:
            continue
        if key in ("authors", "tags", "pencillers", "web", "original_works", "characters", "release_methods", "release_platforms"):
            if not isinstance(item, list) or len(item) > 10_000 or any(not isinstance(v, str) for v in item):
                raise ServiceError(f"{key} 必须是文本列表。")
        elif key in ("translated", "uncensored", "full_color"):
            if type(item) is not bool:
                raise ServiceError(f"{key} 必须是布尔值。")
        elif key == "cover_index":
            if type(item) is not int or item < 0:
                raise ServiceError("cover_index 必须是非负整数。")
        elif not isinstance(item, str) or len(item) > 2 * 1024 * 1024:
            raise ServiceError(f"{key} 必须是长度合适的文本。")
        merged[key] = item
    if "title" not in value and any(key in value for key in ("original_title", "chinese_title", "translated")):
        merged.pop("title", None)  # Older clients may still submit the former title fields.
    return Metadata.from_dict(merged)


def _batch_authors(value) -> list[str]:
    authors = list(dict.fromkeys(author.strip() for author in _strings(value, "作者")))
    if any(len(author) > 500 for author in authors):
        raise ServiceError("作者名称过长。")
    return authors


def _import_defaults(value) -> dict:
    if not isinstance(value, dict) or set(value) - {"authors", "genre"}:
        raise ServiceError("导入默认值仅支持作者和类型。")
    result = {}
    if "authors" in value:
        result["authors"] = _batch_authors(value["authors"])
    if "genre" in value:
        genre = _string(value["genre"], "类型").strip()
        if genre not in GENRES:
            raise ServiceError("内容类型请选择漫画、小说、CG集、画集、设定集或插画集；发行性质与题材请分别填写。")
        result["genre"] = genre
    return result


def _batch_changes(value) -> dict:
    if not isinstance(value, dict) or not value:
        raise ServiceError("请勾选要批量编辑的字段。")
    result = {}
    for field, item in value.items():
        if field == "authors":
            result[field] = _batch_authors(item)
        elif field in BATCH_LIST_FIELDS:
            if not isinstance(item, list) or len(item) > 1000:
                raise ServiceError(f"{field} 必须是文本列表。")
            items = [_string(entry, field).strip() for entry in item]
            limit = 32767 if field == "web" else 500
            if any(len(entry) > limit for entry in items):
                raise ServiceError(f"{field} 的值过长。")
            result[field] = items
        elif field in BATCH_TEXT_FIELDS or field in BATCH_CHOICES:
            limit = 200 if field == "genre" else 32767
            if not isinstance(item, str) or len(item) > limit or "\x00" in item or (item and not item.strip()):
                raise ServiceError(f"{field} 必须是有效文本，或留空以清除。")
            item = item.strip()
            if field in BATCH_CHOICES and item not in BATCH_CHOICES[field]:
                raise ServiceError(f"{field} 的选项无效。")
            result[field] = item
        elif field == "uncensored":
            if type(item) is not bool:
                raise ServiceError("修正状态必须是布尔值。")
            result[field] = item
        else:
            raise ServiceError(f"不支持批量编辑字段 {field}。")
    if result.get("genre"):
        if result["genre"] not in GENRES:
            raise ServiceError("内容类型请选择漫画、小说、CG集、画集、设定集或插画集。普通题材请在标签中编辑。")
    return result


def _folder_link(path: Path) -> bool:
    # Python 3.11 does not provide Path.is_junction on Windows.
    return is_link(path) or bool(getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400)


class ComicService:
    METHODS = frozenset({"bootstrap", "scan", "import", "save", "approve", "page", "xml", "scrape_preview", "model_test", "model_assist", "agent_prepare", "agent_apply",
                         "plan", "export", "cancel", "set_output", "backup", "browse_folders",
                         "prepare_import", "commit_import", "remove", "batch_edit", "export_komga", "komga_test", "komga_sync", "komga_pull", "komga_resolve", "source_context", "prepare_review_sync", "commit_review_sync", "komga_jobs", "komga_jobs_retry", "komga_jobs_run"})

    def __init__(self, data_dir: Path, progress: Callable[[str], None] | None = None):
        self.data_dir = data_dir.resolve()
        self.repo = Repository(self.data_dir / "catalog.sqlite3")
        self.repo.clear_import_roots()
        self.progress = progress or (lambda message: None)
        self.plans: OrderedDict[str, PreparedExport] = OrderedDict()
        self.imports: OrderedDict[str, PreparedImport] = OrderedDict()
        self.reviews = BatchReview(self.repo, self.progress)
        self.library_agent = LibraryAgent(self.repo, self.progress)
        self.jobs = KomgaJobs(self.repo)
        self._legacy_variants_checked = False

    def _repair_legacy_variants(self, cancel: Event) -> None:
        """Backfill unreviewed legacy folder labels once, before the editor loads.

        Candidate selection excludes removed/protected records. A changed or
        unreadable source is left for the regular scan/review flow. Backup and
        revision checks precede every catalog edit; source files are read only.
        """
        snapshots, expected = [], {}
        candidates = self.repo.variant_repair_sources()
        states = {comic.id: self.repo.review_state(comic.id) for comic in candidates}
        if candidates:
            self.progress("正在核对旧文字版本目录资料…")
            results: Queue = Queue()
            stopped = Event()

            def read_sources():
                # NAS system calls may block even after cancellation. This
                # daemon only reads sources, never accesses or edits the DB.
                try:
                    for comic in candidates:
                        if stopped.is_set() or cancel.is_set():
                            return
                        fresh = None
                        try:
                            source = Path(comic.source)
                            if not _folder_link(source):
                                fresh = inspect_source(source)
                        except (OSError, ValueError):
                            pass
                        if stopped.is_set() or cancel.is_set():
                            return
                        results.put((comic, fresh))
                finally:
                    results.put(None)

            Thread(target=read_sources, name="legacy-variant-source-check", daemon=True).start()
            deadline = time.monotonic() + LEGACY_VARIANT_CHECK_SECONDS
            try:
                while (remaining := deadline - time.monotonic()) > 0:
                    check_cancel(cancel)
                    try:
                        result = results.get(timeout=min(0.1, remaining))
                    except Empty:
                        continue
                    if result is None:
                        break
                    comic, fresh = result
                    if (fresh is None or fresh.fingerprint != comic.fingerprint
                            or len(fresh.pages) != comic.page_count):
                        continue
                    _, _, fields = self.repo.preview_variant_repair(fresh, comic)
                    if fields:
                        snapshots.append(fresh)
                        expected[comic.id] = states[comic.id]
            finally:
                # Late results after the startup deadline cannot mutate data.
                stopped.set()
        check_cancel(cancel)
        if snapshots:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = self.repo.path.with_name(f"{self.repo.path.stem}.before-version-folder-repair-{stamp}.sqlite3")
            try:
                self.repo.backup(backup)
                repaired = self.repo.apply_variant_backfill(
                    snapshots, backup_path=backup, expected=expected, cancel=cancel)
                if repaired:
                    self.progress(f"已备份并修正 {len(repaired)} 个旧文字版本目录，请核对后审核。")
            except (OSError, ValueError, sqlite3.Error):
                # Failed backup/revision validation keeps the original catalog.
                self.progress("旧版本目录资料暂未修正，原资料已保留，可重新扫描核对。")
        self._legacy_variants_checked = True

    @staticmethod
    def serialize(comic: Comic) -> dict:
        return asdict(comic)

    def call(self, method: str, params: dict | None = None, cancel: Event | None = None):
        if method not in self.METHODS:
            raise ServiceError("不支持的操作。", "METHOD_NOT_FOUND")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ServiceError("params 必须是对象。")
        cancel = cancel if cancel is not None else Event()
        if method not in ("scan", "import", "export", "prepare_import", "commit_import", "cancel", "komga_sync", "komga_pull", "prepare_review_sync", "commit_review_sync", "agent_prepare", "agent_apply"):
            check_cancel(cancel)
        if method == "bootstrap":
            if not self._legacy_variants_checked:
                try:
                    self._repair_legacy_variants(cancel)
                except (OSError, ValueError, sqlite3.Error):
                    self._legacy_variants_checked = True
                    self.progress("旧版本目录资料暂未修正，原资料已保留，可重新扫描核对。")
            return {"comics": [self.serialize(c) for c in self.repo.all()],
                    "outputRoot": self.repo.setting("output_root"), "dataDir": str(self.data_dir),
                    "autoSyncJobs": list_jobs(self.repo), "metadataRules": METADATA_RULES_VERSION}
        if method == "komga_jobs":
            if params:
                raise ServiceError("同步任务列表不接受额外参数。")
            return {"jobs": list_jobs(self.repo)}
        if method in ("komga_jobs_retry", "komga_jobs_run"):
            allowed = {"ids", "url", "libraryId"} if method == "komga_jobs_retry" else {"url", "apiKey", "libraryId"}
            if set(params) - allowed:
                raise ServiceError("自动同步任务参数无效。")
            url = _string(params.get("url"), "Komga 地址")
            library = _string(params.get("libraryId"), "Komga 书库")
            if method == "komga_jobs_retry":
                return retry_jobs(self.repo, _strings(params.get("ids"), "任务 id"), url, library, cancel=cancel)
            return run_due(self.repo, url, _string(params.get("apiKey"), "API Key"), library,
                           cancel, self.progress, limit=1)
        if method == "source_context":
            comic = self.repo.get(_string(params.get("id"), "id"))
            return {"kind": comic.kind, "remote": comic.remote}
        if method == "scrape_preview":
            if set(params) - {"url", "apiKey", "affiliateId"}:
                raise ServiceError("刮削预览只接受作品链接和站点 API 凭据。")
            url = _string(params.get("url"), "作品链接")
            try:
                return scrape_preview(url, api_key=params.get("apiKey"),
                                      affiliate_id=params.get("affiliateId"), cancel=cancel)
            except ScrapeError as error:
                raise ServiceError(str(error), error.code) from None
            except (OSError, ValueError, KeyError, TypeError, RecursionError):
                raise ServiceError("无法读取该站点的作品资料，请核对链接或稍后重试。", "SCRAPE_RESPONSE") from None
        if method in ("agent_prepare", "agent_apply"):
            try:
                if method == "agent_prepare":
                    return self.library_agent.prepare(params, cancel)
                result = self.library_agent.apply(params, cancel)
                if result["updated"]:
                    self.plans.clear()
                return result
            except AgentError as error:
                raise ServiceError(str(error), error.code) from None
        if method in ("model_test", "model_assist"):
            allowed = {"profile", "api_key"}
            if method == "model_assist":
                allowed.update(("id", "metadata", "siteFields"))
            if set(params) - allowed:
                raise ServiceError("模型请求参数无效。")
            profile, api_key = params.get("profile"), params.get("api_key", "")
            if not isinstance(profile, dict) or not isinstance(api_key, str):
                raise ServiceError("模型接口或密钥格式无效。")
            try:
                if method == "model_test":
                    started = time.monotonic()
                    result = test_profile(profile, api_key, cancelled=cancel)
                    return {**result, "latency_ms": round((time.monotonic() - started) * 1000)}
                metadata, site = params.get("metadata"), params.get("siteFields")
                if not isinstance(metadata, dict) or (site is not None and not isinstance(site, dict)):
                    raise ServiceError("作品资料无效。")
                self.repo.get(_string(params.get("id"), "作品 id"))
                return assist_preview(profile, api_key, metadata, site_fields=site, cancelled=cancel)
            except ModelTransportError as error:
                raise ServiceError(str(error), error.code) from None
        if method == "scan":
            return self.scan(params, cancel)
        if method == "import":
            return self.import_files(params, cancel)
        if method == "browse_folders":
            return self.browse_folders(params, cancel)
        if method == "prepare_import":
            return self.prepare_import(params, cancel)
        if method == "commit_import":
            return self.commit_import(params, cancel)
        if method == "remove":
            identities = _strings(params.get("ids"), "作品 id")
            self.repo.remove(identities)
            self.plans.clear()
            return {"count": len(identities)}
        if method == "batch_edit":
            identities = _strings(params.get("ids"), "作品 id")
            changes = _batch_changes(params.get("changes"))
            count = self.repo.batch_edit(identities, changes)
            if count:
                self.plans.clear()
            return {"count": count, "comics": [self.serialize(self.repo.get(i)) for i in identities]}
        if method == "export_komga":
            identities = _strings(params.get("ids"), "作品 id")
            return export_manifest(self.repo, identities, _path(params.get("path"), "清单位置"))
        if method == "komga_test":
            return test_connection(_string(params.get("url"), "Komga 地址"), _string(params.get("apiKey"), "API Key"), cancel)
        if method == "komga_sync":
            return synchronize(self.repo, _strings(params.get("ids"), "作品 id"),
                               _string(params.get("url"), "Komga 地址"), _string(params.get("apiKey"), "API Key"),
                               _string(params.get("libraryId"), "Komga 书库"), cancel, self.progress)
        if method in ("prepare_review_sync", "commit_review_sync"):
            connection = (_string(params.get("url"), "Komga 地址"), _string(params.get("apiKey"), "API Key"), _string(params.get("libraryId"), "Komga 书库"), cancel)
            if method == "prepare_review_sync":
                return self.reviews.prepare(_strings(params.get("ids"), "作品 id"), *connection)
            return self.reviews.commit(_string(params.get("token"), "审核预览"), *connection)
        if method == "komga_pull":
            result = pull_library(self.repo, _string(params.get("url"), "Komga 地址"), _string(params.get("apiKey"), "API Key"),
                                  _string(params.get("libraryId"), "Komga 书库"), cancel, self.progress)
            if not result["cancelled"] and not result["errors"]:
                result["retired"] = len(self.repo.retire_indexed_uploads(result["scope"]))
            return result
        if method == "komga_resolve":
            identity = _string(params.get("id"), "id")
            if self.repo.get(identity).kind != "komga":
                raise ServiceError("此操作仅适用于 Komga 远端记录。")
            resolve_remote(self.repo, identity, _string(params.get("resolution"), "冲突处理方式"),
                           _string(params.get("url"), "Komga 地址"), _string(params.get("apiKey"), "API Key"),
                           _string(params.get("libraryId"), "Komga 书库"), cancel)
            return self.serialize(self.repo.get(identity))
        if method in ("save", "approve"):
            return self.save(params, approve=method == "approve", cancel=cancel)
        if method == "page":
            return self.page(params, cancel)
        if method == "xml":
            comic = self.repo.get(_string(params["id"], "id")) if params.get("id") else None
            metadata = _metadata(params.get("metadata", {}), comic.metadata if comic else None)
            if comic and comic.kind == "komga":
                before = comic.metadata.to_dict()
                explicit = {key for key, value in params.get("metadata", {}).items() if key in before and before[key] != value}
                return preview_changes(self.repo, comic, metadata, explicit)
            page_count = params.get("pageCount", comic.page_count if comic else None)
            if type(page_count) is not int or page_count < 1:
                raise ServiceError("pageCount 必须是正整数。")
            return {
                "xml": to_comicinfo(metadata, page_count).decode("utf-8"),
                "komga": {"book": komga_book_payload(metadata), "series": komga_series_payload(metadata)},
                "warnings": komga_warnings(metadata),
            }
        if method == "plan":
            return self.prepare(params, cancel)
        if method == "export":
            return self.export(params, cancel)
        if method == "cancel":
            cancel.set()
            return {"cancelled": True}
        if method == "set_output":
            path = _path(params.get("path"), "成品目录")
            self.repo.set_setting("output_root", str(path))
            self.plans.clear()
            return {"outputRoot": str(path)}
        if method == "backup":
            path = _path(params.get("path"), "备份路径")
            self.repo.backup(path)
            return {"path": str(path)}
        raise ServiceError("不支持的操作。", "METHOD_NOT_FOUND")

    def excluded_sources(self) -> list[Path]:
        output = self.repo.setting("output_root")
        return [self.data_dir] + ([Path(output)] if output else [])

    def browse_folders(self, params: dict, cancel: Event) -> dict:
        path = _path(params.get("path"), "目录")
        if _folder_link(path):
            raise ServiceError("不支持浏览符号链接或目录联接。")
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise ServiceError("请选择一个目录。")
        excluded = self.excluded_sources()

        def visible(child):
            return (child.name.casefold() not in SKIP_DIRS and child.is_dir() and not _folder_link(child)
                    and not any(is_inside(child, root) for root in excluded))

        children, errors = [], []
        if any(is_inside(path, root) for root in excluded):
            return {"path": str(path), "children": [], "errors": []}
        for child in sorted(path.iterdir(), key=lambda p: natural_key(p.name)):
            check_cancel(cancel)
            try:
                if visible(child):
                    try:
                        has_children = any(visible(grandchild) for grandchild in child.iterdir())
                    except OSError:
                        has_children = True
                    children.append({"path": str(child), "name": child.name, "hasChildren": has_children})
            except OSError as exc:
                errors.append(f"{child}: {exc}")
        return {"path": str(path), "children": children, "errors": errors}

    def prepare_import(self, params: dict, cancel: Event) -> dict:
        paths = [_path(p, "导入路径") for p in _strings(params.get("paths"), "导入路径")]
        recursive = params.get("recursive", True)
        if type(recursive) is not bool:
            raise ServiceError("recursive 必须是布尔值。")
        snapshots: dict[str, Snapshot] = {}
        items, errors = [], []
        excluded = self.excluded_sources()

        def add(snapshot: Snapshot):
            identity = source_id(snapshot.path)
            if identity in snapshots:
                return
            check_cancel(cancel)
            snapshots[identity] = snapshot
            try:
                previous = self.repo.get(identity, include_removed=True)
            except ValueError:
                previous = None
            metadata, warning = snapshot.initial_metadata()
            status = "new" if previous is None else ("removed" if self.repo.is_removed(identity) else "existing")
            if previous:
                metadata, warning, _ = self.repo.preview_variant_repair(snapshot, previous, allow_removed=True)
                if previous.fingerprint != snapshot.fingerprint:
                    warning = "源文件已变化，导入后需要重新审核。" + warning
            items.append({"id": identity, "source": str(snapshot.path), "kind": snapshot.kind,
                          "page_count": len(snapshot.pages), "metadata": metadata.to_dict(),
                          "import_status": status, "warning": warning})
            self.progress(f"已发现 {len(items)} 本 · {snapshot.path.name}")

        cancelled = False
        try:
            for path in paths:
                check_cancel(cancel)
                try:
                    if _folder_link(path):
                        raise ValueError("不支持导入符号链接或目录联接。")
                    if any(is_inside(path, root) for root in excluded):
                        raise ValueError("不能将资料库或成品目录作为漫画源导入。")
                    if path.is_dir() and recursive:
                        for snapshot in discover(path, exclude=excluded, cancel=cancel,
                                                 on_error=lambda p, error: errors.append(f"{p}: {error}")):
                            add(snapshot)
                    else:
                        add(inspect_source(path))
                except Cancelled:
                    raise
                except Exception as exc:
                    errors.append(f"{path}: {exc}")
            check_cancel(cancel)
        except Cancelled:
            cancelled = True
        timestamp = time.monotonic()
        self.imports = OrderedDict((token, value) for token, value in self.imports.items()
                                   if timestamp - value.created_at <= PLAN_TTL_SECONDS)
        while len(self.imports) >= 8:
            self.imports.popitem(last=False)
        token = secrets.token_urlsafe(32)
        self.imports[token] = PreparedImport(timestamp, snapshots)
        return {"token": token, "items": items, "errors": errors, "cancelled": cancelled}

    def commit_import(self, params: dict, cancel: Event) -> dict:
        token = _string(params.get("token"), "导入预览")
        identities = _strings(params.get("ids"), "作品 id")
        defaults = _import_defaults(params.get("defaults", {}))
        overwrite = params.get("overwriteExisting", False)
        if type(overwrite) is not bool:
            raise ServiceError("overwriteExisting 必须是布尔值。")
        prepared = self.imports.get(token)
        if prepared is None or time.monotonic() - prepared.created_at > PLAN_TTL_SECONDS:
            self.imports.pop(token, None)
            raise ServiceError("导入预览已失效，请重新选择并预览。", "INVALID_IMPORT")
        if any(identity not in prepared.snapshots for identity in identities):
            raise ServiceError("勾选的作品不在此导入预览中。", "INVALID_IMPORT")
        self.imports.pop(token)
        self.plans.clear()
        completed, errors, skipped = [], [], 0
        cancelled = False
        try:
            for identity in identities:
                check_cancel(cancel)
                snapshot = prepared.snapshots[identity]
                try:
                    self.progress(f"正在导入 · {snapshot.path.name}")
                    if _folder_link(snapshot.path):
                        raise ServiceError("源目录已变为链接，请重新选择并预览。", "SOURCE_CHANGED")
                    fresh = inspect_source(snapshot.path)
                    if (source_id(fresh.path) != identity or fresh.fingerprint != snapshot.fingerprint
                            or fresh.metadata_context_fingerprint != snapshot.metadata_context_fingerprint):
                        raise ServiceError("来源或目录识别信息在预览后已变化，请重新预览后导入。", "SOURCE_CHANGED")
                    check_cancel(cancel)
                    try:
                        previous = self.repo.get(identity, include_removed=True)
                    except ValueError:
                        previous = None
                    removed = self.repo.is_removed(identity)
                    self.repo.upsert_scan(fresh, restore=True, defaults=defaults, overwrite_existing=overwrite)
                    if (previous is not None and not removed and not (defaults and overwrite)
                            and previous.fingerprint == fresh.fingerprint
                            and previous.metadata.to_dict() == self.repo.get(identity).metadata.to_dict()):
                        skipped += 1
                    else:
                        completed.append(identity)
                except Cancelled:
                    raise
                except Exception as exc:
                    errors.append(f"{snapshot.path}: {exc}")
            check_cancel(cancel)
        except Cancelled:
            cancelled = True
        return {"count": len(completed), "skipped": skipped, "errors": errors,
                "cancelled": cancelled, "ids": completed}

    def scan(self, params: dict, cancel: Event) -> dict:
        current_only = "roots" not in params
        if current_only:
            roots = [c.source for c in self.repo.all() if c.kind in ("folder", "cbz")]
        else:
            roots = [str(_path(value, "扫描目录")) for value in _strings(params["roots"], "扫描目录")]
        self.plans.clear()
        output = self.repo.setting("output_root")
        excluded = [self.data_dir] + ([Path(output)] if output else [])
        count, errors, seen = 0, [], set()
        try:
            for root in roots:
                check_cancel(cancel)
                try:
                    if current_only:
                        if any(is_inside(Path(root), p) for p in excluded):
                            continue
                        if _folder_link(Path(root)):
                            raise ValueError("扫描目录已变为符号链接或目录联接。")
                        snapshots = [inspect_source(Path(root))]
                    else:
                        snapshots = discover(Path(root), exclude=excluded, cancel=cancel,
                                             on_error=lambda path, error: errors.append(f"{path}: {error}"))
                    for snapshot in snapshots:
                        identity = source_id(snapshot.path)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        check_cancel(cancel)
                        if self.repo.upsert_scan(snapshot) is not None:
                            count += 1
                            self.progress(f"已扫描 {count} 本 · {snapshot.path.name}")
                except (OSError, ValueError) as exc:
                    errors.append(f"{root}: {exc}")
            check_cancel(cancel)
        except Cancelled:
            return {"count": count, "errors": errors, "cancelled": True}
        return {"count": count, "errors": errors, "cancelled": False}

    def import_files(self, params: dict, cancel: Event) -> dict:
        paths = [_path(p, "CBZ 路径") for p in _strings(params.get("paths"), "CBZ 路径")]
        self.plans.clear()
        count, errors = 0, []
        try:
            for path in paths:
                check_cancel(cancel)
                try:
                    if path.suffix.lower() != ".cbz":
                        raise ValueError("导入文件必须为 CBZ。")
                    self.progress(f"正在导入 {path.name}")
                    snapshot = inspect_source(path)
                    check_cancel(cancel)
                    if self.repo.upsert_scan(snapshot) is not None:
                        count += 1
                except Cancelled:
                    raise
                except Exception as exc:
                    errors.append(f"{path}: {exc}")
            check_cancel(cancel)
        except Cancelled:
            return {"count": count, "errors": errors, "cancelled": True}
        return {"count": count, "errors": errors, "cancelled": False}

    def save(self, params: dict, *, approve: bool, cancel: Event) -> dict:
        identity = _string(params.get("id"), "id")
        comic = self.repo.get(identity)
        metadata = _metadata(params.get("metadata"), comic.metadata)
        self.plans.clear()
        before = comic.metadata.to_dict()
        explicit = {key for key, value in params.get("metadata", {}).items() if key in before and before[key] != value}
        if "genre" in explicit and params["metadata"]["genre"] and params["metadata"]["genre"] not in GENRES:
            raise ServiceError("内容类型请选择漫画、小说、CG集、画集、设定集或插画集。普通题材请在标签中编辑。")
        self.repo.save(identity, metadata, fields=explicit)
        if approve:
            check_cancel(cancel)
            if comic.kind == "komga":
                self.progress("正在在线核对 Komga 远端快照…")
                approve_remote(self.repo, identity, _string(params.get("url"), "Komga 地址"),
                               _string(params.get("apiKey"), "API Key"), _string(params.get("libraryId"), "Komga 书库"), cancel)
                return self.serialize(self.repo.get(identity))
            self.progress("正在确认源文件与作品资料…")
            snapshot = inspect_source(Path(comic.source))
            check_cancel(cancel)
            self.repo.approve(identity, snapshot)
            record_approval(self.repo, identity)
        return self.serialize(self.repo.get(identity))

    def page(self, params: dict, cancel: Event) -> dict:
        comic = self.repo.get(_string(params.get("id"), "id"))
        index = params.get("index", 0)
        if type(index) is not int or index < 0:
            raise ServiceError("页码必须是非负整数。")
        if comic.kind == "komga":
            from .komga_media import remote_page
            return remote_page(self.repo, comic.id, index, _string(params.get("url"), "Komga 地址"),
                               _string(params.get("apiKey"), "API Key"), _string(params.get("libraryId"), "Komga 书库"), cancel)
        snapshot = inspect_source(Path(comic.source))
        if snapshot.fingerprint != comic.fingerprint:
            raise ServiceError("源文件已变化，请重新扫描后预览。", "SOURCE_CHANGED")
        if index >= len(snapshot.pages):
            raise ServiceError("页码超出图片范围。")
        member = snapshot.pages[index]
        if member.size > MAX_PAGE_BYTES:
            raise ServiceError("此页超过 32 MiB，暂不能预览，仍可无损打包。", "PAGE_TOO_LARGE")
        mime = PAGE_MIMES.get(Path(member.name).suffix.lower())
        if mime is None:
            raise ServiceError("此图片格式暂不支持预览。")
        check_cancel(cancel)
        data = snapshot.page_bytes(index)
        if len(data) > MAX_PAGE_BYTES:
            raise ServiceError("此页超过 32 MiB，暂不能预览。", "PAGE_TOO_LARGE")
        check_cancel(cancel)
        return {"dataUrl": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
                "name": member.name, "index": index, "total": len(snapshot.pages)}

    def prepare(self, params: dict, cancel: Event) -> dict:
        if set(params) - {"ids", "outputRoot", "comparison"}:
            raise ServiceError("生成计划参数无效。")
        identities = _strings(params.get("ids"), "作品 id")
        output_root = _path(params.get("outputRoot"), "成品目录")
        comparison = self._upload_connection(params.get("comparison"))
        plans, destinations, items = [], set(), []
        for identity in identities:
            check_cancel(cancel)
            comic = self.repo.get(identity)
            if comic.kind == "komga":
                raise ServiceError("Komga 远端记录请直接同步元数据，不能作为本机图片来源生成 CBZ。")
            self.progress(f"检查生成计划 · {comic.metadata.display_title}")
            plan = make_plan(comic, output_root, allow_alternate=bool(comparison))
            destination = os.path.normcase(str(plan.destination))
            if destination in destinations:
                raise ServiceError("多本作品使用了相同输出路径，请调整后重试。")
            destinations.add(destination)
            plans.append(plan)
            items.append({"id": identity, "title": comic.metadata.display_title, "source": comic.source,
                          "destination": str(plan.destination), "pageCount": len(plan.snapshot.pages),
                          "attachmentCount": len(plan.snapshot.extras), "warnings": plan.warnings,
                          "bytes": sum(m.size for m in plan.snapshot.pages + plan.snapshot.extras) + len(plan.xml),
                          "local": {"authors": comic.metadata.authors, "language": comic.metadata.language,
                                    "textVersion": comic.metadata.text_version, "series": comic.metadata.series,
                                    "number": comic.metadata.number}})
        view = {"state": "unconfigured", "libraryName": ""}
        findings = None
        scope = None
        if comparison:
            url, key, library_id = comparison
            try:
                inspected = compare_uploads(plans, url, key, library_id, cancel, self.progress)
                findings = {item["id"]: item for item in inspected["items"]}
                for item in items:
                    item["komga"] = findings[item["id"]]
                scope = (url, library_id)
                view = {"state": "checked", "libraryName": inspected["libraryName"]}
            except KomgaError as error:
                view = {"state": "error", "libraryName": "", "message": str(error)}
        check_cancel(cancel)
        now = time.monotonic()
        self.plans = OrderedDict((token, p) for token, p in self.plans.items()
                                 if now - p.created_at <= PLAN_TTL_SECONDS)
        while len(self.plans) >= 8:
            self.plans.popitem(last=False)
        token = secrets.token_urlsafe(32)
        if view["state"] != "error":
            self.plans[token] = PreparedExport(now, output_root, plans, scope, findings)
        return {"token": token, "items": items, "totalBytes": sum(item["bytes"] for item in items), "komga": view}

    @staticmethod
    def _upload_connection(value) -> tuple[str, str, str] | None:
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {"url", "apiKey", "libraryId"}:
            raise ServiceError("Komga 查重连接参数无效。")
        return (_string(value["url"], "Komga 地址"), _string(value["apiKey"], "API Key"),
                _string(value["libraryId"], "Komga 书库"))

    def export(self, params: dict, cancel: Event) -> dict:
        if set(params) - {"token", "autoSync", "ids", "confirmedIds", "comparison"}:
            raise ServiceError("生成参数无效。")
        auto_sync = params.get("autoSync")
        if auto_sync is not None and (not isinstance(auto_sync, dict)
                or set(auto_sync) != {"url", "libraryId"}):
            raise ServiceError("自动同步配置无效。")
        if auto_sync is not None:
            _string(auto_sync.get("url"), "Komga 地址")
            _string(auto_sync.get("libraryId"), "Komga 书库")
        token = _string(params.get("token"), "生成计划")
        prepared = self.plans.pop(token, None)
        if prepared is None or time.monotonic() - prepared.created_at > PLAN_TTL_SECONDS:
            raise ServiceError("生成计划已失效，请重新预览并确认。", "INVALID_PLAN")
        connection = self._upload_connection(params.get("comparison"))
        scope = (connection[0], connection[2]) if connection else None
        if scope != prepared.comparison_scope:
            raise ServiceError("Komga 目标书库已变化，请重新预览并核对。", "INVALID_PLAN")
        ids = {p.comic.id for p in prepared.plans}
        selected = set(_strings(params["ids"], "选中的作品")) if "ids" in params else ids
        confirmed = set(_strings(params["confirmedIds"], "确认继续的作品")) if params.get("confirmedIds") != [] and "confirmedIds" in params else set()
        if not selected <= ids or not confirmed <= selected:
            raise ServiceError("所选作品不属于当前生成计划。", "INVALID_PLAN")
        expected = prepared.findings or {}
        needs_confirmation = {identity for identity in selected if identity in expected and expected[identity]["status"] != "new"}
        if needs_confirmation != confirmed:
            raise ServiceError("请先勾选并确认所有可能重复的作品。", "INVALID_PLAN")
        completed, errors, auto_sync_errors = [], [], []
        try:
            fresh = []
            for previous in prepared.plans:
                if previous.comic.id not in selected:
                    continue
                check_cancel(cancel)
                current = make_plan(self.repo.get(previous.comic.id), prepared.output_root, allow_alternate=bool(connection))
                if (current.destination != previous.destination or current.xml != previous.xml
                        or current.snapshot.fingerprint != previous.snapshot.fingerprint):
                    raise ServiceError("作品在生成计划确认后发生变化，请重新预览。", "INVALID_PLAN")
                fresh.append(current)
            if connection:
                inspected = compare_uploads(fresh, *connection, cancel=cancel, progress=self.progress)
                checked = {item["id"]: item for item in inspected["items"]}
                if any(checked.get(identity) != expected[identity] for identity in selected):
                    raise ServiceError("Komga 书库中的相似作品在确认后有变化，请重新预览核对。", "INVALID_PLAN")
            for plan in fresh:
                check_cancel(cancel)
                try:
                    report = export_cbz(plan, cancel=cancel, progress=self.progress, verify_images=bool(auto_sync))
                    self.repo.mark_packaged(plan.comic.id, plan.destination, report)
                    completed.append(str(plan.destination))
                    try:
                        record_validated_export(self.repo, plan.comic.id, prepared.output_root)
                        if auto_sync:
                            enqueue_export(self.repo, plan.comic.id, prepared.output_root,
                                           auto_sync["url"], auto_sync["libraryId"], cancel=cancel)
                    except Cancelled:
                        raise
                    except Exception as exc:
                        auto_sync_errors.append(f"{plan.destination}: CBZ 已生成，但未能加入 Komga 同步任务：{exc}")
                except Cancelled:
                    raise
                except Exception as exc:
                    errors.append(f"{plan.comic.source}: {exc}")
        except Cancelled:
            return {"completed": completed, "errors": errors, "autoSyncErrors": auto_sync_errors, "cancelled": True}
        return {"completed": completed, "errors": errors, "autoSyncErrors": auto_sync_errors, "cancelled": False}


def error_payload(exc: Exception) -> dict:
    if isinstance(exc, (ServiceError, KomgaError)):
        code = exc.code
    elif isinstance(exc, Cancelled):
        code = "CANCELLED"
    elif isinstance(exc, FileExistsError):
        code = "TARGET_EXISTS"
    elif isinstance(exc, FileNotFoundError):
        code = "SOURCE_UNAVAILABLE"
    elif isinstance(exc, OSError):
        code = "IO_ERROR"
    elif isinstance(exc, (ValueError, KeyError, TypeError)):
        code = "INVALID_ARGUMENT"
    else:
        code = "INTERNAL_ERROR"
    return {"message": str(exc) or type(exc).__name__, "code": code}


class JsonLineServer:
    def __init__(self, data_dir: Path, output: TextIO):
        self.output = output
        self.output_lock = Lock()
        self.state_lock = Lock()
        self.active: tuple[Thread, Event] | None = None
        self.service = ComicService(data_dir, self.progress)

    def emit(self, value: dict):
        with self.output_lock:
            self.output.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.output.flush()

    def progress(self, message: str):
        self.emit({"event": "task-progress", "data": {"message": message}})

    def accept(self, request):
        identity = request.get("id") if isinstance(request, dict) else None
        try:
            if not isinstance(request, dict):
                raise ServiceError("请求必须是对象。")
            if not isinstance(identity, str) or not identity or len(identity) > 128:
                raise ServiceError("请求 id 必须是非空文本。")
            method = request.get("method")
            if not isinstance(method, str) or method not in self.service.METHODS:
                raise ServiceError("不支持的操作。", "METHOD_NOT_FOUND")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ServiceError("params 必须是对象。")
            with self.state_lock:
                if method == "cancel":
                    if self.active:
                        self.active[1].set()
                    self.emit({"id": identity, "result": {"cancelled": True}})
                    return
                if self.active is not None:
                    raise ServiceError("正在执行其他操作，请稍候。", "BUSY")
                cancel = Event()
                worker = Thread(target=self.run_request, args=(identity, method, params, cancel),
                                name=f"comic-{method}")
                self.active = (worker, cancel)
                worker.start()
        except Exception as exc:
            self.emit({"id": identity, "error": error_payload(exc)})

    def run_request(self, identity: str, method: str, params: dict, cancel: Event):
        try:
            response = {"id": identity, "result": self.service.call(method, params, cancel)}
        except Exception as exc:
            response = {"id": identity, "error": error_payload(exc)}
        with self.state_lock:
            self.active = None
        self.emit(response)

    def serve(self, source: TextIO):
        try:
            while line := source.readline(MAX_REQUEST_CHARS + 1):
                if len(line) > MAX_REQUEST_CHARS:
                    while line and not line.endswith("\n"):
                        line = source.readline(MAX_REQUEST_CHARS + 1)
                    self.emit({"id": None, "error": {"message": "请求过大。", "code": "INVALID_ARGUMENT"}})
                    continue
                if not line.strip():
                    continue
                try:
                    request = json.loads(line)
                except (ValueError, UnicodeError):
                    self.emit({"id": None, "error": {"message": "无效的 JSON 请求。", "code": "PARSE_ERROR"}})
                    continue
                self.accept(request)
        finally:
            with self.state_lock:
                active = self.active
                if active:
                    active[1].set()
            if active:
                active[0].join()


class CatalogLock:
    """OS-held lock, released even if the service is terminated."""
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.handle = (data_dir / "desktop-service.lock").open("a+b")

    def __enter__(self):
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handle.seek(0)
            if self.handle.read(1) == b"":
                self.handle.write(b"\0")
                self.handle.flush()
        except OSError:
            self.handle.close()
            raise ServiceError("此资料库已由另一个漫画整理服务打开。", "CATALOG_LOCKED") from None
        return self

    def __exit__(self, *_):
        self.handle.close()


def main():
    parser = argparse.ArgumentParser(description="Comic Manage local desktop service")
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    try:
        with CatalogLock(args.data_dir):
            JsonLineServer(args.data_dir, sys.stdout).serve(sys.stdin)
    except Exception as exc:
        print(json.dumps({"error": error_payload(exc)}, ensure_ascii=False), file=sys.stderr, flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
