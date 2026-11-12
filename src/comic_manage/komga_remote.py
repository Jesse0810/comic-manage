"""Read Komga metadata into reviewed local working copies bound to exact remote IDs."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import math
import struct
from threading import Event
from typing import Callable
from urllib.parse import quote

from .komga_client import KomgaClient, KomgaError, normalize_url, server_path
from .metadata import AGE_RATINGS, Metadata, title_key
from .sources import Cancelled, check_cancel
from .komga_payloads import remote_book_payload, remote_series_payload

READ_ONLY_FIELDS = frozenset({"collection_mode", "cover_index", "original_xml", "original_title", "chinese_title", "translated", "full_color", "volume"})
SERIES_FIELDS = frozenset({"series", "publisher", "language", "manga", "count", "localized_series", "localized_series_label"})
DATE_FIELDS = frozenset({"year", "month", "day"})


def komga_scope(url: str, library_id: str) -> str:
    return hashlib.sha256((normalize_url(url) + "\n" + library_id).encode("utf-8")).hexdigest()[:20]


def remote_identity(scope: str, book_id: str) -> str:
    return "komga-" + hashlib.sha256((scope + "\n" + book_id).encode("utf-8")).hexdigest()[:24]


def semantic_snapshot(book: dict, series: dict) -> dict:
    pages = (book.get("media") or {}).get("pagesCount", 0)
    pages = pages if type(pages) is int and pages >= 0 else 0
    return {"book": {key: deepcopy(book.get(key)) for key in ("id", "libraryId", "seriesId", "url", "deleted", "oneshot", "metadata")}
            | {"media": {"pagesCount": pages}},
            "series": {key: deepcopy(series.get(key)) for key in ("id", "libraryId", "url", "deleted", "oneshot", "booksCount", "metadata")}}


def snapshot_fingerprint(snapshot: dict) -> str:
    value = deepcopy(snapshot)
    list_fields = {"authors", "tags", "links", "notableEntities", "genres", "alternateTitles"}
    for entity in value.values():
        metadata = entity.get("metadata") or {}
        for field in ("created", "lastModified", "createdDate", "lastModifiedDate"):
            metadata.pop(field, None)
        for field in list_fields:
            if isinstance(metadata.get(field), list):
                metadata[field].sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        number_sort = metadata.get("numberSort")
        if type(number_sort) in (int, float) and abs(number_sort) <= 3.4028235e38 and math.isfinite(number_sort):
            metadata["numberSort"] = struct.unpack("!f", struct.pack("!f", number_sort))[0]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _pairs(value, first: str, second: str) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) or not isinstance(item.get(first), str) or not isinstance(item.get(second), str) for item in value):
        raise ValueError("远端元数据列表格式无效。")
    return value


def metadata_from_remote(book: dict, series: dict, preferred_alias_label: str = "zh-Hans") -> tuple[Metadata, list[str]]:
    raw = book.get("metadata") or {}
    shared = series.get("metadata") or {}
    if not isinstance(raw, dict) or not isinstance(shared, dict):
        raise ValueError("远端作品或系列元数据格式无效。")
    entities = _pairs(raw.get("notableEntities"), "kind", "name")
    authors = _pairs(raw.get("authors"), "role", "name")
    links = _pairs(raw.get("links"), "label", "url")
    aliases = _pairs(shared.get("alternateTitles"), "label", "title")
    tags = raw.get("tags") or []
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("远端标签格式无效。")
    grouped = {}
    for entity in entities:
        grouped.setdefault(title_key(entity["kind"]), []).append(entity["name"])
    namespaced = {}
    ordinary = []
    for tag in tags:
        prefix, separator, name = tag.replace("：", ":", 1).partition(":")
        if separator and prefix.strip() in ("原作", "角色", "发行性质", "出版形态", "修正状态", "内容类型", "年龄分级"):
            namespaced.setdefault(prefix.strip(), []).append(name.strip())
        else:
            ordinary.append(tag)
    one = lambda key: (grouped.get(key) or namespaced.get(key) or [""])[0]
    known_ages = list(dict.fromkeys(grouped.get("年龄分级", [])))
    age = known_ages[0] if len(known_ages) == 1 and known_ages[0] in AGE_RATINGS else "Unknown"
    known_colors = grouped.get("色彩", [])
    color = "Yes" if known_colors == ["黑白"] else "No" if len(known_colors) == 1 and known_colors[0] in ("全彩", "彩色", "彩色/非黑白", "彩色 / 非黑白") else "Unknown"
    title = raw.get("title") or book.get("name") or ""
    if not isinstance(title, str):
        raise ValueError("远端作品名称格式无效。")
    year = month = day = ""
    warnings = []
    if raw.get("releaseDate"):
        try:
            released = date.fromisoformat(raw["releaseDate"])
            year, month, day = str(released.year), str(released.month), str(released.day)
        except (ValueError, TypeError):
            warnings.append("远端发行日无法由当前编辑字段表示，未编辑时会保留原值。")
    if not known_ages:
        warnings.append("逐册年龄分级尚无独立实体，共享系列分级仅供参考。")
    if not grouped.get("内容类型"):
        warnings.append("逐册内容类型尚无独立实体，共享系列类型仅供参考。")
    oneshot = bool(book.get("oneshot"))
    alias = next((item for item in aliases if item["label"].casefold() == preferred_alias_label.casefold()),
                 next((item for item in aliases if item["label"].casefold() == "zh-hans"), aliases[0] if aliases else None))
    number = raw.get("number") or ""
    count = shared.get("totalBookCount")
    values = {
        "title": title, "original_title": title, "collection_mode": "oneshot" if oneshot else "series",
        "series": "" if oneshot else shared.get("title") or "", "number": "" if oneshot else str(number),
        "count": str(count) if not oneshot and type(count) is int and count > 0 else "",
        "localized_series": alias["title"] if alias and not oneshot else "",
        "localized_series_label": alias["label"] if alias and not oneshot else "zh-Hans",
        "authors": [item["name"] for item in authors if item["role"].casefold() == "writer"],
        "pencillers": [item["name"] for item in authors if item["role"].casefold() == "penciller"],
        "translator": ", ".join(item["name"] for item in authors if item["role"].casefold() == "translator"),
        "publisher": shared.get("publisher") or "", "language": shared.get("language") or "",
        "manga": {"RIGHT_TO_LEFT": "YesAndRightToLeft", "LEFT_TO_RIGHT": "No"}.get(shared.get("readingDirection"), "Unknown"),
        "genre": ", ".join(grouped.get("内容类型", [])), "age_rating": age, "black_and_white": color,
        "summary": raw.get("summary") or "", "web": list(dict.fromkeys(item["url"] for item in links)),
        "original_works": grouped.get("原作") or namespaced.get("原作", []),
        "characters": grouped.get("character") or namespaced.get("角色", []),
        "release_type": one("发行性质"), "publication_format": one("出版形态"),
        "uncensored": one("修正状态") == "无修正", "tags": ordinary,
        "year": year, "month": month, "day": day,
    }
    return Metadata(**values), warnings


def require_scope(source: dict | None, client: KomgaClient, library_id: str) -> dict:
    if source is None or source["server_url"] != client.url or source["library_id"] != library_id or source["scope"] != komga_scope(client.url, library_id):
        raise KomgaError("此记录不属于当前配置的 Komga 服务器和书库，请切回对应配置。", "KOMGA_SCOPE")
    return source


def fetch_snapshot(client: KomgaClient, source: dict) -> dict:
    book = client.request("GET", "api/v1/books/" + quote(source["book_id"], safe=""))
    if not isinstance(book, dict) or book.get("id") != source["book_id"] or book.get("libraryId") != source["library_id"] or book.get("deleted"):
        raise KomgaError("远端漫画不存在或已离开当前书库，请重新读取书库。", "KOMGA_SOURCE")
    series_id = book.get("seriesId")
    if not isinstance(series_id, str) or not series_id:
        raise KomgaError("远端漫画缺少有效系列归属。", "KOMGA_SOURCE")
    series = client.request("GET", "api/v1/series/" + quote(series_id, safe=""))
    if not isinstance(series, dict) or series.get("id") != series_id or series.get("libraryId") != source["library_id"] or series.get("deleted"):
        raise KomgaError("远端系列不属于当前书库，请重新读取书库。", "KOMGA_SOURCE")
    return semantic_snapshot(book, series)


def validate_changes(metadata: Metadata, fields: set[str]) -> None:
    if fields & READ_ONLY_FIELDS:
        raise ValueError("远端归属、封面及内部保留字段不能通过此元数据编辑修改。")
    if "title" in fields and not metadata.display_title:
        raise ValueError("作品名称不能留空。")
    if "number" in fields and metadata.collection_mode == "series" and not metadata.number.strip():
        raise ValueError("Komga 册号不能写入空值。")
    if "series" in fields and metadata.collection_mode == "series" and not metadata.series.strip():
        raise ValueError("真实系列名称不能留空。")
    if metadata.collection_mode == "oneshot" and fields & {"series", "number", "count", "localized_series", "localized_series_label"}:
        raise ValueError("One-Shot 的系列归属和编号由远端组织方式决定。")
    checked = fields - ({"language"} if not metadata.language else set())
    metadata.validate_fields(checked)


def preview_changes(repo, comic, metadata: Metadata, explicit: set[str]) -> dict:
    source = repo.get_komga_source(comic.id)
    fields = set(source["dirty_fields"]) | explicit
    base = source["base_metadata"]
    fields = {field for field in fields if getattr(metadata, field) != base.get(field)}
    validate_changes(metadata, fields)
    source = {**source, "dirty_fields": sorted(fields)}
    book, warnings = remote_book_payload(metadata, source["snapshot"]["book"], fields)
    series, shared_warnings = remote_series_payload([(metadata, source)], source["snapshot"]["series"])
    warnings.extend(shared_warnings)
    if series:
        warnings.append("系列字段只在本次覆盖全部有效册且逐册信息足够时写入，实际同步会再次核对。")
    return {"xml": "", "komga": {"book": book, "series": series or None}, "warnings": warnings}


def finalize_sync(repo, comic, client: KomgaClient, library_id: str, book_patch: dict, series_patch: dict) -> None:
    source = require_scope(repo.get_komga_source(comic.id), client, library_id)
    snapshot = fetch_snapshot(client, source)
    expected = deepcopy(source["snapshot"])
    expected["book"]["metadata"].update(book_patch)
    expected["series"]["metadata"].update(series_patch)
    if snapshot_fingerprint(snapshot) != snapshot_fingerprint(expected):
        repo.note_komga_conflict(comic.id, snapshot)
        raise KomgaError("写入后远端元数据又发生变化，已保留本机编辑并标记冲突，请重新核对。", "KOMGA_CONFLICT")
    current = repo.get(comic.id).metadata.to_dict()
    base, _ = metadata_from_remote(snapshot["book"], snapshot["series"], current["localized_series_label"])
    fields = set(source["dirty_fields"])
    refreshed = Metadata.from_dict({**base.to_dict(), **{field: current[field] for field in fields}})
    if "age_rating" in fields and "uncensored" not in fields:
        refreshed.uncensored = current["uncensored"]
    remaining = sorted(field for field in fields if getattr(refreshed, field) != getattr(base, field))
    repo.rebase_komga(comic.id, refreshed, base, snapshot, snapshot_fingerprint(snapshot), remaining,
                      action="komga_sync_snapshot", synced=True)


def approve_remote(repo, identity: str, url: str, api_key: str, library_id: str, cancel: Event) -> None:
    client = KomgaClient(url, api_key, cancel=cancel)
    source = require_scope(repo.get_komga_source(identity), client, library_id)
    if source["unavailable"]:
        raise KomgaError("远端记录已不可用，请重新读取书库。", "KOMGA_SOURCE")
    snapshot = fetch_snapshot(client, source)
    if snapshot_fingerprint(snapshot) != snapshot_fingerprint(source["snapshot"]):
        repo.note_komga_conflict(identity, snapshot)
        raise KomgaError("远端资料已变化，请选择保留本机编辑或使用远端资料，再重新审核。", "KOMGA_CONFLICT")
    if source["conflict"]:
        raise KomgaError("请先处理此记录的远端冲突，再重新审核。", "KOMGA_CONFLICT")
    validate_changes(repo.get(identity).metadata, set(source["dirty_fields"]))
    check_cancel(cancel)
    repo.approve_komga(identity)


def resolve_remote(repo, identity: str, resolution: str, url: str, api_key: str, library_id: str, cancel: Event) -> None:
    if resolution not in ("keep_local", "use_remote"):
        raise ValueError("请选择保留本机编辑或使用远端资料。")
    client = KomgaClient(url, api_key, cancel=cancel)
    source = require_scope(repo.get_komga_source(identity), client, library_id)
    snapshot = fetch_snapshot(client, source)
    current = repo.get(identity).metadata.to_dict()
    base, _ = metadata_from_remote(snapshot["book"], snapshot["series"], current["localized_series_label"])
    fields = set(source["dirty_fields"]) if resolution == "keep_local" else set()
    rebased = Metadata.from_dict({**base.to_dict(), **{field: current[field] for field in fields}})
    fields = sorted(field for field in fields if getattr(rebased, field) != getattr(base, field))
    check_cancel(cancel)
    repo.rebase_komga(identity, rebased, base, snapshot, snapshot_fingerprint(snapshot), fields, action="komga_resolve_" + resolution)


def pull_library(repo, url: str, api_key: str, library_id: str, cancel: Event, progress: Callable[[str], None]) -> dict:
    client = KomgaClient(url, api_key, cancel=cancel)
    scope = komga_scope(client.url, library_id)
    libraries = [item for item in client.libraries() if item["id"] == library_id]
    if len(libraries) != 1:
        raise KomgaError("当前配置的 Komga 书库不存在或无权访问。", "KOMGA_LIBRARY")
    result = {"total": 0, "added": 0, "updated": 0, "kept": 0, "skipped": 0, "unavailable": 0,
              "cancelled": False, "errors": [], "ids": [], "scope": scope}
    cache = {}
    try:
        progress("正在分页读取 Komga 书库")
        books = client.books(library_id)
        result["total"] = len(books)
        seen = {book["id"] for book in books if book.get("libraryId") == library_id and not book.get("deleted")}
        for index, book in enumerate(books, 1):
            check_cancel(cancel)
            if book.get("libraryId") != library_id or book.get("deleted"):
                result["skipped"] += 1
                continue
            try:
                series_id = book.get("seriesId")
                if not isinstance(series_id, str) or not series_id:
                    raise ValueError("远端漫画缺少有效系列。")
                if series_id not in cache:
                    series = client.request("GET", "api/v1/series/" + quote(series_id, safe=""))
                    if not isinstance(series, dict) or series.get("id") != series_id or series.get("libraryId") != library_id or series.get("deleted"):
                        raise ValueError("远端系列不属于所选书库。")
                    cache[series_id] = series
                snapshot = semantic_snapshot(book, cache[series_id])
                identity = remote_identity(scope, book["id"])
                previous = repo.get_komga_source(identity)
                preferred = repo.get(identity, include_removed=True).metadata.localized_series_label if previous else "zh-Hans"
                metadata, warnings = metadata_from_remote(book, cache[series_id], preferred)
                source = {"server_url": client.url, "library_id": library_id, "library_name": libraries[0]["name"],
                          "book_id": book["id"], "series_id": series_id, "remote_path": server_path(book.get("url", "")),
                          "scope": scope, "source": f"komga://{scope}/{quote(book['id'], safe='')}"}
                action = repo.upsert_komga(identity, source, metadata, snapshot, snapshot_fingerprint(snapshot), snapshot["book"]["media"]["pagesCount"], " ".join(warnings))
                result[action] += 1
                if action != "skipped":
                    result["ids"].append(identity)
                progress(f"已读取 {index}/{len(books)} 本 · {metadata.display_title}")
            except KomgaError as error:
                if error.code in ("KOMGA_AUTH", "KOMGA_FORBIDDEN", "KOMGA_REDIRECT"):
                    raise
                result["skipped"] += 1
                result["errors"].append(str(error))
            except (ValueError, TypeError) as error:
                result["skipped"] += 1
                result["errors"].append(str(error))
        check_cancel(cancel)
        result["unavailable"] = repo.mark_komga_missing(scope, seen)
    except Cancelled:
        result["cancelled"] = True
    return result
