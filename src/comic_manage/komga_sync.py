"""Synchronize reviewed local metadata to exactly matched Komga books."""
from __future__ import annotations

from collections import Counter, defaultdict
import math
from pathlib import Path
import sqlite3
from threading import Event
from typing import Callable
from urllib.parse import quote, urlsplit

from .komga import MANAGED_ENTITY_KINDS, komga_book_payload
from .komga_client import KomgaClient, KomgaError, path_matches, server_path
from .metadata import Metadata, split_values, title_key
from .sources import Cancelled, check_cancel

SUPPORTED_ROLES = frozenset({"writer", "penciller", "translator"})
AGE_VALUES = {"Everyone": 0, "MA15+": 15, "R18+": 18}
DIRECTIONS = {"No": "LEFT_TO_RIGHT", "YesAndRightToLeft": "RIGHT_TO_LEFT"}


def _records(value, fields: tuple[str, ...]) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) or any(not isinstance(item.get(field), str) or not item[field].strip() for field in fields) for item in value):
        raise ValueError("远端资料列表格式无效，本次已跳过以保留现有值。")
    return value


def _locked(payload: dict, field: str, value) -> None:
    payload[field] = value
    payload[field + "Lock"] = True


def book_payload(metadata: Metadata, current: dict) -> tuple[dict, list[str]]:
    """Replace locally editable values while retaining unsupported author roles and entities."""
    existing = current.get("metadata") or {}
    if not isinstance(existing, dict):
        raise ValueError("远端漫画资料格式无效。")
    managed = {title_key(kind) for kind in MANAGED_ENTITY_KINDS} | {"character"}
    entities = [{"kind": item["kind"], "name": item["name"]} for item in _records(existing.get("notableEntities"), ("kind", "name")) if title_key(item["kind"]) not in managed]
    payload = komga_book_payload(metadata)
    payload["notableEntities"] = entities + payload["notableEntities"]
    payload["notableEntities"].extend({"kind": "character", "name": name} for name in metadata.characters)
    authors = [{"name": item["name"], "role": item["role"]} for item in _records(existing.get("authors"), ("name", "role")) if item["role"].strip().casefold() not in SUPPORTED_ROLES]
    authors.extend({"name": name, "role": "writer"} for name in metadata.authors)
    authors.extend({"name": name, "role": "penciller"} for name in metadata.pencillers)
    authors.extend({"name": name, "role": "translator"} for name in split_values(metadata.translator))
    labels = {item["url"]: item["label"] for item in _records(existing.get("links"), ("label", "url"))}
    links = [{"label": labels.get(url) or urlsplit(url).netloc, "url": url} for url in metadata.web]
    for field, value in (("title", metadata.display_title), ("summary", metadata.summary),
                         ("authors", authors), ("tags", metadata.all_tags), ("links", links)):
        _locked(payload, field, value)
    warnings = []
    if metadata.collection_mode == "series":
        _locked(payload, "number", metadata.number.strip())
        try:
            number = float(metadata.number)
        except ValueError:
            number = math.nan
        if math.isfinite(number) and abs(number) <= 3.4028235e38:
            _locked(payload, "numberSort", number)
        else:
            warnings.append("册号已同步，非数值或超出范围的册号无法直接重置 Komga 排序值。")
    if metadata.year and metadata.month and metadata.day:
        _locked(payload, "releaseDate", f"{int(metadata.year):04d}-{int(metadata.month):02d}-{int(metadata.day):02d}")
    elif not any((metadata.year, metadata.month, metadata.day)):
        _locked(payload, "releaseDate", None)
    else:
        warnings.append("Komga API 只接受完整日期，已保留本机部分日期并跳过远端发行日更新。")
    return payload, warnings


def _majority(values: list[str], label: str) -> tuple[str | None, str | None]:
    counts = Counter(value for value in values if value)
    if not counts:
        return None, None
    highest = max(counts.values())
    winners = [value for value, count in counts.items() if count == highest]
    if len(winners) != 1:
        return None, f"系列的{label}出现平票，本次保留远端该字段，其余确定值继续同步。"
    return winners[0], None


def series_payload(metadata: list[Metadata], current: dict) -> tuple[dict, list[str]]:
    modes = {item.collection_mode for item in metadata}
    if len(modes) != 1 or (modes == {"oneshot"}) != bool(current.get("oneshot")):
        raise ValueError("本机作品方式与 Komga 目录组织不同，请整理文件路径并重新扫描后再同步系列。")
    oneshot = modes == {"oneshot"}
    if oneshot and len(metadata) != 1:
        raise ValueError("Komga One-Shot 系列包含多本记录，请核对书库目录设置。")
    if not oneshot and len({title_key(item.series) for item in metadata}) != 1:
        raise ValueError("所选记录属于不同的本机真实系列，API 无法移动或拆分 Komga 系列。")
    payload = {}
    warnings = []
    title = metadata[0].display_title if oneshot else metadata[0].series.strip()
    _locked(payload, "title", title)
    if oneshot:
        _locked(payload, "summary", metadata[0].summary)
    _locked(payload, "ageRating", max(AGE_VALUES[item.age_rating] for item in metadata))
    for field, values, label in (
        ("publisher", [item.publisher.strip() for item in metadata], "出版方"),
        ("language", [item.language.strip() for item in metadata], "语言"),
        ("readingDirection", [DIRECTIONS.get(item.manga, "") for item in metadata], "阅读方向"),
    ):
        value, warning = _majority(values, label)
        if warning:
            warnings.append(warning)
            continue
        _locked(payload, field, value if field == "readingDirection" else value or "")
    genres = list(dict.fromkeys(genre for item in metadata for genre in split_values(item.genre)))
    _locked(payload, "genres", genres)
    known_counts = [int(item.count) for item in metadata if item.count]
    _locked(payload, "totalBookCount", 1 if oneshot else max(known_counts, default=None))
    if not oneshot:
        aliases = {}
        conflicting_aliases = False
        for item in metadata:
            label, alias = item.localized_series_label.strip(), item.localized_series.strip()
            key = label.casefold()
            if key in aliases and aliases[key]["title"] != alias:
                conflicting_aliases = True
                break
            aliases[key] = {"label": label, "title": alias}
        if conflicting_aliases:
            warnings.append("同一系列的同标签别名不一致，本次保留远端别名，其余确定值继续同步。")
        else:
            existing = current.get("metadata") or {}
            if not isinstance(existing, dict):
                raise ValueError("远端系列资料格式无效。")
            titles = [{"label": item["label"], "title": item["title"]} for item in _records(existing.get("alternateTitles"), ("label", "title")) if item["label"].casefold() not in aliases]
            titles.extend(item for item in aliases.values() if item["title"])
            _locked(payload, "alternateTitles", titles)
    if len(set(known_counts)) > 1:
        warnings.append("系列总册数采用各册已知 Count 的最大值。")
    return payload, warnings


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
              "seriesUpdated": 0, "cancelled": False, "books": [], "series": []}
    completed = set()
    titles = {}
    plans = []
    successful = defaultdict(list)
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
                if not isinstance(current, dict) or current.get("id") != planned["id"] or not _valid_book(current, library_id, relative) or current["seriesId"] != planned["seriesId"]:
                    raise ValueError("远端漫画身份或路径发生变化，本次已跳过。")
                # Keep local review authoritative if another catalog writer changed it while the request ran.
                latest = repo.get(identity)
                if latest.status not in ("approved", "packaged") or latest.metadata.to_dict() != comic.metadata.to_dict():
                    raise ValueError("本机资料在同步期间发生变化，请重新审核后重试。")
                payload, warnings = book_payload(comic.metadata, current)
                client.request("PATCH", f"api/v1/books/{escaped}/metadata", payload)
                try:
                    repo.save_komga_mapping(client.url, library_id, identity, current["id"], current["seriesId"], server_path(current["url"]))
                except (sqlite3.Error, OSError):
                    warnings.append("远端元数据已同步，但本机映射保存失败，下次将按精确文件路径重新匹配。")
                entry.update(status="success", message="已同步全部可表达的已审核漫画字段。", warnings=warnings)
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
                payload, warnings = series_payload([comic.metadata for comic, _ in records], current)
                if len(series_names.get(title_key(records[0][0].metadata.series), set())) > 1:
                    warnings.append("多个 Komga 系列使用了相同本机名称，API 只更新显示名称。合并归属需整理目录并重新扫描。")
                client.request("PATCH", f"api/v1/series/{escaped}/metadata", payload)
                entry.update(status="success", message="已同步完整系列的聚合字段。", warnings=warnings)
                result["seriesUpdated"] += 1
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
    for identity in identities:
        if identity not in completed:
            result["books"].append({"id": identity, "title": titles.get(identity, identity),
                                    "status": "skipped", "message": "已取消同步。" if result["cancelled"] else "同步已中止，请检查连接或权限后重试。"})
    for status, key in (("success", "updated"), ("skipped", "skipped"), ("failed", "failed")):
        result[key] = sum(item["status"] == status for item in result["books"])
    return result
