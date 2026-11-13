"""Entity-only reconciliation after a verified local CBZ has entered Komga.

This flow deliberately omits native Book and shared Series fields. The verified
CBZ remains the portable source; this API step only rebuilds server indexes.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath
from typing import Callable
from urllib.parse import quote

from .komga import MANAGED_ENTITY_KINDS, komga_metadata
from .komga_client import KomgaError, path_matches, server_path
from .metadata import Metadata
from .sources import check_cancel


class AutoSyncIssue(KomgaError):
    def __init__(self, message: str, code: str, state: str):
        super().__init__(message, code)
        self.state = state


def _issue(message: str, code: str = "KOMGA_AUTO_REVIEW") -> AutoSyncIssue:
    return AutoSyncIssue(message, code, "NeedsManualReview")


def _relative(value: str) -> str:
    if not isinstance(value, str):
        raise _issue("待同步的 CBZ 相对路径无效。")
    relative = value.replace("\\", "/").lstrip("/")
    if not relative or any(part in ("", ".", "..") for part in relative.split("/")) or not relative.lower().endswith(".cbz"):
        raise _issue("待同步的 CBZ 相对路径无效。")
    return relative


def _title(book: dict) -> str | None:
    metadata = book.get("metadata")
    return metadata.get("title") if isinstance(metadata, dict) else None


def _eligible(book: dict, library_id: str) -> bool:
    return (isinstance(book, dict) and not book.get("deleted") and book.get("libraryId") == library_id
            and isinstance(book.get("id"), str) and bool(book["id"])
            and isinstance(book.get("seriesId"), str) and bool(book["seriesId"]))


def match_book(books: list[dict], library_id: str, relative_path: str, title: str) -> dict:
    """Use exact Title with full relative path, then extension-free name fallback."""
    relative = _relative(relative_path)
    filename = PurePosixPath(relative).stem
    if not isinstance(title, str) or not title or not isinstance(library_id, str) or not library_id:
        raise _issue("已审核标题或实际 Library ID 缺失。")
    if not isinstance(books, list) or any(not isinstance(book, dict) for book in books):
        raise AutoSyncIssue("Komga 漫画列表格式无效。", "KOMGA_RESPONSE", "PermanentFailure")
    active = [book for book in books if not book.get("deleted")]
    relevant_foreign = [book for book in active if book.get("libraryId") != library_id
                        and (path_matches(book.get("url", ""), relative)
                             or (book.get("name") == filename and _title(book) == title))]
    if relevant_foreign:
        raise _issue("目标文件出现在其他 Komga 书库，已停止自动匹配，请检查 Library ID 和目录。", "KOMGA_AUTO_LIBRARY")
    eligible = [book for book in active if _eligible(book, library_id)]
    path_candidates = [book for book in eligible if path_matches(book.get("url", ""), relative)]
    exact = [book for book in path_candidates if _title(book) == title]
    if len(exact) == 1:
        return deepcopy(exact[0])
    if len(exact) > 1:
        raise _issue("CBZ 路径与标题匹配到多本漫画，请人工核对。", "KOMGA_AUTO_AMBIGUOUS")
    if path_candidates:
        raise _issue("Komga 中同路径漫画的标题与已审核 ComicInfo Title 不一致，请检查是否扫描完成或文件被替换。", "KOMGA_AUTO_TITLE")
    name_candidates = [book for book in eligible if book.get("name") == filename]
    exact = [book for book in name_candidates if _title(book) == title]
    if len(exact) == 1:
        return deepcopy(exact[0])
    if len(exact) > 1:
        raise _issue("无扩展名文件名与标题匹配到多本漫画，请人工核对。", "KOMGA_AUTO_AMBIGUOUS")
    if name_candidates:
        raise _issue("Komga 中同名漫画的标题与已审核 ComicInfo Title 不一致，请核对。", "KOMGA_AUTO_TITLE")
    raise AutoSyncIssue("等待 Komga 扫描并建立与 CBZ 路径和标题唯一对应的 Book。", "KOMGA_AUTO_PENDING_INDEX", "PendingIndex")


def _key(value: str) -> str:
    # No NFKC: preserve original names, including full-width characters.
    return value.strip().lower()


def _records(value, fields: tuple[str, str], label: str) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) or any(not isinstance(item.get(field), str) or not item[field].strip() for field in fields) for item in value):
        raise _issue(f"远端{label}格式无效，已保留原值，请人工核对。", "KOMGA_AUTO_DATA")
    return [{field: item[field].strip() for field in fields} for item in value]


def _deduplicate(records: list[dict], fields: tuple[str, str]) -> list[dict]:
    result, seen = [], set()
    for item in records:
        key = tuple(_key(item[field]) for field in fields)
        if key not in seen:
            result.append({field: item[field].strip() for field in fields})
            seen.add(key)
    return result


def _keys(records: list[dict], fields: tuple[str, str]) -> list[tuple[str, str]]:
    return [tuple(_key(item[field]) for field in fields) for item in records]


def target_entities(metadata: Metadata, existing: list[dict] | None) -> list[dict]:
    """Replace owned kinds, including the complete character set; keep third parties."""
    current = _records(existing, ("kind", "name"), "实体")
    managed = {_key(kind) for kind in MANAGED_ENTITY_KINDS} | {"character"}
    unknown = [item for item in current if _key(item["kind"]) not in managed]
    desired = komga_metadata(metadata)["notableEntities"]
    desired.extend({"kind": "character", "name": name} for name in metadata.characters)
    return _deduplicate(unknown + desired, ("kind", "name"))


def entity_hash(entities: list[dict]) -> str:
    canonical = sorted(_keys(entities, ("kind", "name")))
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _same_location(first, second) -> bool:
    first, second = server_path(first), server_path(second)
    if first.startswith("//") or (len(first) > 2 and first[1:3] == ":/"):
        return first.lower() == second.lower()
    return first == second


def _check_identity(current: dict, planned: dict, library_id: str, relative_path: str, title: str) -> None:
    if (not _eligible(current, library_id) or current.get("id") != planned.get("id")
            or current.get("seriesId") != planned.get("seriesId")
            or not _same_location(current.get("url", ""), planned.get("url", ""))
            or current.get("name") != planned.get("name") or _title(current) != title):
        raise _issue("匹配后的 Book ID、标题、路径或系列归属发生变化，请人工核对。", "KOMGA_AUTO_IDENTITY")
    filename = PurePosixPath(_relative(relative_path)).stem
    if not (path_matches(current.get("url", ""), relative_path) or current.get("name") == filename):
        raise _issue("Book 已不再对应已审核 CBZ，已停止写回。", "KOMGA_AUTO_IDENTITY")


def _check_metadata(record: dict) -> dict:
    value = record.get("metadata")
    if not isinstance(value, dict):
        raise _issue("Komga 元数据格式无效，已停止自动同步。", "KOMGA_AUTO_DATA")
    return value


def _verify_records(actual, target: list[dict], fields: tuple[str, str], label: str) -> None:
    records = _records(actual, fields, label)
    keys = _keys(records, fields)
    expected = _keys(target, fields)
    if len(keys) != len(set(keys)) or set(keys) != set(expected):
        raise AutoSyncIssue(f"Komga {label}回读与目标不一致，尚未标记同步成功，将重新读取后对账。", "KOMGA_AUTO_READBACK", "RetryableFailure")


def synchronize_entities(client, metadata: Metadata, library_id: str, relative_path: str, *,
                         book: dict | None = None, expected_book_id: str = "",
                         backup: Callable[[dict], None] | None = None,
                         guard: Callable[[], None] | None = None) -> dict:
    """One bounded reconciliation attempt; retries and persistence belong to the queue."""
    if metadata.publication_format not in ("", "独立作品", "单行本", "合集", "分卷", "连载"):
        raise _issue("自动同步的出版形态仅接受独立作品、单行本、合集、分卷、连载，旧词或自定义值请人工核对后重新审核。", "KOMGA_AUTO_PUBLICATION_FORMAT")
    metadata.validate_review()
    relative = _relative(relative_path)
    title = metadata.display_title
    def check():
        check_cancel(client.cancel)
        if guard:
            guard()
    check()
    planned = deepcopy(book) if book is not None else match_book(client.books(library_id), library_id, relative, title)
    # A caller-provided or remembered ID never bypasses strict matching.
    match_book([planned], library_id, relative, title)
    if expected_book_id and planned.get("id") != expected_book_id:
        raise _issue("同一路径已对应其他 Book ID，旧任务不会自动改绑，请人工确认。", "KOMGA_AUTO_REPLACED")
    book_id, series_id = planned["id"], planned["seriesId"]
    escaped = quote(book_id, safe="")
    check()
    current = client.request("GET", f"api/v1/books/{escaped}")
    _check_identity(current, planned, library_id, relative, title)
    existing = _check_metadata(current)
    entities = target_entities(metadata, existing.get("notableEntities"))
    digest = entity_hash(entities)
    result = {"bookId": book_id, "seriesId": series_id, "targetHash": digest,
              "targetEntities": deepcopy(entities), "entities": deepcopy(entities),
              "updated": False, "seriesUpdated": False, "snapshot": None, "seriesSnapshot": None}
    original = _records(existing.get("notableEntities"), ("kind", "name"), "实体")
    keys = _keys(original, ("kind", "name"))
    changed = (len(keys) != len(set(keys)) or set(keys) != set(_keys(entities, ("kind", "name")))
               or existing.get("notableEntitiesLock") is not True)
    if changed:
        request = {"notableEntities": entities, "notableEntitiesLock": True}
        check()
        if not callable(backup):
            raise AutoSyncIssue("自动同步缺少可持久化的写前备份，已停止写回。", "KOMGA_AUTO_BACKUP", "PermanentFailure")
        backup({"kind": "book", "bookId": book_id, "seriesId": series_id, "libraryId": library_id,
                    "relativePath": relative, "title": title, "before": deepcopy(existing),
                    "request": deepcopy(request), "targetHash": digest})
        check()
        client.request("PATCH", f"api/v1/books/{escaped}/metadata", request)
        result["updated"] = True
    check()
    observed = client.request("GET", f"api/v1/books/{escaped}")
    _check_identity(observed, planned, library_id, relative, title)
    observed_metadata = _check_metadata(observed)
    _verify_records(observed_metadata.get("notableEntities"), entities, ("kind", "name"), "实体")
    if observed_metadata.get("notableEntitiesLock") is not True:
        raise AutoSyncIssue("Komga 实体锁定状态回读不一致，将稍后重新对账。", "KOMGA_AUTO_READBACK", "RetryableFailure")
    result["snapshot"] = deepcopy(observed)

    # Only an actual series with an explicit alternate title can affect Series.
    # No fake numbering, grouping, title, age, genre or publisher updates here.
    if metadata.collection_mode == "series" and metadata.localized_series.strip():
        if planned.get("oneshot") is True:
            raise _issue("Komga 目标是 One-Shot，不能写入真实系列别名。", "KOMGA_AUTO_SERIES")
        escaped_series = quote(series_id, safe="")
        check()
        series = client.request("GET", f"api/v1/series/{escaped_series}")
        def verify_series(value):
            if (not isinstance(value, dict) or value.get("id") != series_id
                    or value.get("libraryId") != library_id or value.get("oneshot") is True
                    or value.get("deleted") or _check_metadata(value).get("title") != metadata.series.strip()):
                raise _issue("真实 Series 的身份或名称与已审核资料不一致，已停止别名写回。", "KOMGA_AUTO_SERIES")
        verify_series(series)
        prior = _check_metadata(series)
        original_aliases = _records(prior.get("alternateTitles"), ("label", "title"), "系列别名")
        aliases = _deduplicate(original_aliases + komga_metadata(metadata)["alternateTitles"], ("label", "title"))
        alias_keys = _keys(original_aliases, ("label", "title"))
        if (len(alias_keys) != len(set(alias_keys)) or set(alias_keys) != set(_keys(aliases, ("label", "title")))
                or prior.get("alternateTitlesLock") is not True):
            request = {"alternateTitles": aliases, "alternateTitlesLock": True}
            check()
            if not callable(backup):
                raise AutoSyncIssue("自动同步缺少可持久化的写前备份，已停止写回。", "KOMGA_AUTO_BACKUP", "PermanentFailure")
            backup({"kind": "series", "bookId": book_id, "seriesId": series_id, "libraryId": library_id,
                        "relativePath": relative, "title": title, "before": deepcopy(prior),
                        "request": deepcopy(request), "targetHash": digest})
            check()
            client.request("PATCH", f"api/v1/series/{escaped_series}/metadata", request)
            result["seriesUpdated"] = True
        check()
        observed_series = client.request("GET", f"api/v1/series/{escaped_series}")
        verify_series(observed_series)
        final_series_metadata = _check_metadata(observed_series)
        _verify_records(final_series_metadata.get("alternateTitles"), aliases, ("label", "title"), "系列别名")
        if final_series_metadata.get("alternateTitlesLock") is not True:
            raise AutoSyncIssue("Komga 系列别名锁定状态回读不一致，将稍后重试。", "KOMGA_AUTO_READBACK", "RetryableFailure")
        result["seriesSnapshot"] = deepcopy(observed_series)
    check()
    return result
