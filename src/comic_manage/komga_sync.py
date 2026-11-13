"""Synchronize reviewed local metadata to exactly matched Komga books."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import sqlite3
from threading import Event
from typing import Callable
from urllib.parse import quote

from .komga_payloads import book_payload, series_payload, remote_book_payload, remote_series_payload
from .komga_client import KomgaClient, KomgaError, path_matches, server_path
from .metadata import title_key
from .sources import Cancelled, check_cancel
from .komga_remote import (SERIES_FIELDS, finalize_sync, require_scope, semantic_snapshot,
                           snapshot_fingerprint, validate_changes)

def _relative_path(comic) -> str:
    location = comic.output or (comic.source if comic.kind == "cbz" else "")
    if not location:
        raise ValueError("尚无可精确匹配的 CBZ 路径，请先生成并导入 Komga。")
    path = Path(location)
    return f"{path.parent.name}/{path.name}" if path.parent.name else path.name


def _valid_book(book: dict, library_id: str, relative: str) -> bool:
    return (not book.get("deleted") and book.get("libraryId") == library_id
            and isinstance(book.get("seriesId"), str) and bool(book["seriesId"])
            and path_matches(book.get("url", ""), relative))


def _same_path(first: str, second: str) -> bool:
    first, second = server_path(first), server_path(second)
    if first.startswith("//") or (len(first) > 2 and first[1:3] == ":/"):
        return first.casefold() == second.casefold()
    return first == second


def synchronize(repo, identities: list[str], url: str, api_key: str, library_id: str,
                cancel: Event, progress: Callable[[str], None]) -> dict:
    client = KomgaClient(url, api_key, cancel=cancel)
    if not isinstance(library_id, str) or not library_id:
        raise KomgaError("请先选择要同步的 Komga 书库。")
    result = {"total": len(identities), "updated": 0, "skipped": 0, "failed": 0,
              "seriesUpdated": 0, "unchanged": 0, "cancelled": False, "books": [], "series": []}
    completed = set()
    titles = {}
    plans = []
    successful = defaultdict(list)
    remote_acknowledged = []
    checked_series = {}
    series_sent = {}
    try:
        if sum(item["id"] == library_id for item in client.libraries()) != 1:
            raise KomgaError("配置的 Komga 书库不存在或无权访问，请重新选择。", "KOMGA_LIBRARY")
        progress("正在读取 Komga 书库与漫画列表")
        remote_books = client.books(library_id)
        remote_by_id = {item["id"]: item for item in remote_books}
        target_ids = Counter()
        for identity in identities:
            check_cancel(cancel)
            title = identity
            try:
                comic = repo.get(identity)
                title = comic.metadata.display_title
                titles[identity] = title
                if comic.status not in ("approved", "packaged"):
                    raise ValueError("请先人工审核通过，再同步当前元数据。")
                if comic.kind == "komga":
                    source = require_scope(repo.get_komga_source(identity), client, library_id)
                    if source["unavailable"] or source["conflict"]:
                        raise ValueError("远端记录不可用或存在冲突，请重新读取并处理后再审核。")
                    comic.metadata.validate_review()
                    validate_changes(comic.metadata, set(source["dirty_fields"]))
                    book = remote_by_id.get(source["book_id"])
                    if not book or book.get("deleted") or book.get("libraryId") != library_id or book.get("seriesId") != source["series_id"] or not _same_path(book.get("url", ""), source["remote_path"]):
                        raise ValueError("远端 ID、书库、归属或路径已变化，请重新读取。")
                    plans.append((comic, None, book))
                    target_ids[book["id"]] += 1
                    continue
                comic.metadata.validate(comic.page_count)
                relative = _relative_path(comic)
                mapping = repo.komga_mapping(client.url, library_id, identity)
                mapped = remote_by_id.get(mapping["book_id"]) if mapping else None
                if mapped and _valid_book(mapped, library_id, relative) and _same_path(mapped.get("url", ""), mapping["remote_path"]):
                    matches = [mapped]
                else:
                    matches = [book for book in remote_books if _valid_book(book, library_id, relative)]
                if len(matches) != 1:
                    raise ValueError(f"CBZ 相对路径匹配到 {len(matches)} 本漫画，需在选定书库中唯一匹配后才能同步。")
                book = matches[0]
                plans.append((comic, relative, book))
                target_ids[book["id"]] += 1
            except (ValueError, OSError) as error:
                result["books"].append({"id": identity, "title": title, "status": "skipped", "message": str(error)})
                completed.add(identity)
        for comic, relative, planned in plans:
            check_cancel(cancel)
            identity = comic.id
            title = comic.metadata.display_title
            entry = {"id": identity, "title": title, "bookId": planned["id"]}
            if target_ids[planned["id"]] != 1:
                entry.update(status="skipped", message="多条本机记录匹配同一本 Komga 漫画，请仅选择一条后重试。")
                result["books"].append(entry)
                completed.add(identity)
                continue
            progress(f"正在同步漫画 · {title}")
            try:
                escaped = quote(planned["id"], safe="")
                current = client.request("GET", f"api/v1/books/{escaped}")
                if comic.kind == "komga":
                    source = require_scope(repo.get_komga_source(identity), client, library_id)
                    valid = (isinstance(current, dict) and not current.get("deleted") and current.get("libraryId") == library_id
                             and current.get("seriesId") == source["series_id"] and _same_path(current.get("url", ""), source["remote_path"]))
                else:
                    valid = isinstance(current, dict) and _valid_book(current, library_id, relative)
                if not valid or current.get("id") != planned["id"] or current["seriesId"] != planned["seriesId"]:
                    raise ValueError("远端漫画身份或路径发生变化，本次已跳过。")
                # Keep local review authoritative if another catalog writer changed it while the request ran.
                latest = repo.get(identity)
                if latest.status not in ("approved", "packaged") or latest.metadata.to_dict() != comic.metadata.to_dict():
                    raise ValueError("本机资料在同步期间发生变化，请重新审核后重试。")
                if comic.kind == "komga":
                    if current["seriesId"] not in checked_series:
                        checked_series[current["seriesId"]] = client.request("GET", "api/v1/series/" + quote(current["seriesId"], safe=""))
                    observed = semantic_snapshot(current, checked_series[current["seriesId"]])
                    if snapshot_fingerprint(observed) != snapshot_fingerprint(source["snapshot"]):
                        repo.note_komga_conflict(identity, observed)
                        raise ValueError("远端元数据已变化，请处理冲突并重新审核后再同步。")
                    payload, warnings = remote_book_payload(comic.metadata, current, set(source["dirty_fields"]))
                else:
                    payload, warnings = book_payload(comic.metadata, current)
                if payload:
                    client.request("PATCH", f"api/v1/books/{escaped}/metadata", payload)
                if comic.kind != "komga":
                    try:
                        repo.save_komga_mapping(client.url, library_id, identity, current["id"], current["seriesId"], server_path(current["url"]))
                    except (sqlite3.Error, OSError):
                        warnings.append("远端元数据已同步，但本机映射保存失败，下次将按精确文件路径重新匹配。")
                entry.update(status="success", changed=bool(payload), message="已同步本次明确修改的漫画字段。" if payload else "漫画字段没有修改，无需写入。", warnings=warnings)
                if comic.kind == "komga":
                    remote_acknowledged.append((comic, entry, payload))
                successful[current["seriesId"]].append((comic, current))
            except KomgaError as error:
                entry.update(status="failed", message=str(error))
                if error.code in ("KOMGA_AUTH", "KOMGA_FORBIDDEN", "KOMGA_REDIRECT"):
                    result["books"].append(entry)
                    completed.add(identity)
                    raise
            except (ValueError, OSError) as error:
                entry.update(status="skipped", message=str(error))
            result["books"].append(entry)
            completed.add(identity)
        # Refresh membership after book updates, so newly added or moved books prevent partial-series writes.
        current_books = client.books(library_id) if successful else []
        current_by_id = {book["id"]: book for book in current_books}
        series_names = defaultdict(set)
        for series_id, records in successful.items():
            for comic, _ in records:
                if comic.metadata.collection_mode == "series":
                    series_names[title_key(comic.metadata.series)].add(series_id)
        for series_id, records in successful.items():
            check_cancel(cancel)
            progress(f"正在检查完整系列 · {records[0][0].metadata.display_title}")
            entry = {"id": series_id, "title": records[0][0].metadata.series or records[0][0].metadata.display_title}
            try:
                remote_records = [(comic.metadata, repo.get_komga_source(comic.id)) for comic, _ in records if comic.kind == "komga"]
                if remote_records and len(remote_records) != len(records):
                    raise ValueError("本机文件和远端工作副本混合选择，请按同一来源选择完整系列后同步共享字段。")
                if remote_records:
                    series_fields = SERIES_FIELDS | {"age_rating", "genre"}
                    if records[0][0].metadata.collection_mode == "oneshot":
                        series_fields = series_fields | {"title", "summary"}
                    if not any(set(source["dirty_fields"]) & series_fields for _, source in remote_records):
                        entry.update(status="success", changed=False, message="系列共享字段没有修改，无需写入。")
                        result["series"].append(entry)
                        continue
                members = {book["id"] for book in current_books if not book.get("deleted") and book.get("libraryId") == library_id and book.get("seriesId") == series_id}
                if members != {book["id"] for _, book in records}:
                    raise ValueError("当前只选中了系列的一部分，请全选 Komga 系列中的所有已审核漫画后再同步系列字段。")
                escaped = quote(series_id, safe="")
                current = client.request("GET", f"api/v1/series/{escaped}")
                if not isinstance(current, dict) or current.get("id") != series_id or current.get("libraryId") != library_id or current.get("deleted"):
                    raise ValueError("远端系列身份发生变化，本次已跳过。")
                if type(current.get("booksCount")) is not int or current["booksCount"] != len(members):
                    raise ValueError("Komga 系列的当前册数缺失或发生变化，请重新读取全系列后同步。")
                for comic, book in records:
                    fresh = current_by_id[book["id"]]
                    latest = repo.get(comic.id)
                    if not _same_path(fresh.get("url", ""), book["url"]) or latest.status not in ("approved", "packaged") or latest.metadata.to_dict() != comic.metadata.to_dict():
                        raise ValueError("漫画路径或本机审核资料在同步期间发生变化，本次跳过系列字段。")
                parent_paths = {server_path(book["url"]).rsplit("/", 1)[0] for _, book in records}
                allowed_paths = set(parent_paths)
                if current.get("oneshot") and len(records) == 1:
                    allowed_paths.add(server_path(records[0][1]["url"]))
                if len(parent_paths) != 1 or not any(_same_path(current.get("url", ""), path) for path in allowed_paths):
                    raise ValueError("远端系列目录与已匹配漫画不一致，本次已跳过。")
                if remote_records:
                    for (comic, book), (_, source) in zip(records, remote_records):
                        observed = semantic_snapshot(source["snapshot"]["book"], current)
                        if snapshot_fingerprint(observed) != snapshot_fingerprint(source["snapshot"]):
                            repo.note_komga_conflict(comic.id, semantic_snapshot(current_by_id[book["id"]], current))
                            raise ValueError("远端共享字段已变化，请处理冲突后重新审核。")
                    payload, warnings = remote_series_payload(remote_records, current)
                else:
                    payload, warnings = series_payload([comic.metadata for comic, _ in records], current)
                if len(series_names.get(title_key(records[0][0].metadata.series), set())) > 1:
                    warnings.append("多个 Komga 系列使用了相同本机名称，API 只更新显示名称。合并归属需整理目录并重新扫描。")
                if payload:
                    client.request("PATCH", f"api/v1/series/{escaped}/metadata", payload)
                    result["seriesUpdated"] += 1
                    if remote_records:
                        series_sent[series_id] = payload
                entry.update(status="success", changed=bool(payload), message="已同步本次明确修改的系列字段。" if payload else "没有可写入的系列字段。", warnings=warnings)
            except KomgaError as error:
                entry.update(status="failed", message=str(error))
                if error.code in ("KOMGA_AUTH", "KOMGA_FORBIDDEN", "KOMGA_REDIRECT"):
                    result["series"].append(entry)
                    raise
            except (ValueError, OSError) as error:
                entry.update(status="skipped", message=str(error))
            result["series"].append(entry)
    except Cancelled:
        result["cancelled"] = True
    except KomgaError as error:
        if not result["books"]:
            raise
        result["error"] = str(error)
    for comic, entry, book_patch in remote_acknowledged:
        try:
            source = repo.get_komga_source(comic.id)
            if source["conflict"]:
                raise ValueError("共享资料存在新冲突，已保留本机编辑供处理。")
            finalize_sync(repo, comic, client, library_id, book_patch, series_sent.get(source["series_id"], {}))
        except KomgaError as error:
            entry.setdefault("warnings", []).append(str(error) if error.code == "KOMGA_CONFLICT" else "远端已核对或更新，但本机基线尚未刷新，请重新读取后核对。")
        except (ValueError, OSError, sqlite3.Error, Cancelled):
            entry.setdefault("warnings", []).append("远端已核对或更新，但本机基线尚未刷新，请重新读取后核对。")
    for identity in identities:
        if identity not in completed:
            result["books"].append({"id": identity, "title": titles.get(identity, identity),
                                    "status": "skipped", "message": "已取消同步。" if result["cancelled"] else "同步已中止，请检查连接或权限后重试。"})
    result["updated"] = sum(item["status"] == "success" and item.get("changed", True) for item in result["books"])
    result["unchanged"] = sum(item["status"] == "success" and not item.get("changed", True) for item in result["books"])
    for status in ("skipped", "failed"):
        result[status] = sum(item["status"] == status for item in result["books"])
    return result
