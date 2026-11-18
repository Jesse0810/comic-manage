"""Read-only matching of current export plans against the selected Komga library."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
from threading import Event
from urllib.parse import urlsplit

from .komga_client import KomgaClient, KomgaError, path_matches, server_path
from .metadata import Metadata, read_variant_fields, title_key
from .packaging import ExportPlan
from .sources import check_cancel


MAX_DISPLAY_MATCHES = 8
_DIGITS = re.compile(r"\d{1,30}(?:\.\d{1,20})?", re.ASCII)
_VARIANT_SUFFIX = re.compile(r"\s*(?:\[|【|\()(有文字|无文字|無文字|无字版|無字版)(?:\]|】|\))\s*$")
_TEXT_VERSIONS = {"有文字": "有文字", "无文字": "无文字", "無文字": "无文字", "无字版": "无文字", "無字版": "无文字"}
_LANGUAGE_ALIASES = {"zh-cn": "zh-hans", "zh-sg": "zh-hans", "zh-tw": "zh-hant",
                     "zh-hk": "zh-hant", "zh-mo": "zh-hant", "jpn": "ja", "eng": "en", "zho": "zh", "chi": "zh"}


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _entries(value) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _key(value: str) -> str:
    return " ".join(title_key(value).split())


def _titles(values: Iterable[str]) -> frozenset[str]:
    keys = set()
    for value in values:
        if value:
            keys.add(_key(value))
            if match := _VARIANT_SUFFIX.search(value):
                if base := _key(value[:match.start()]):
                    keys.add(base)
    return frozenset(keys)


def _variant_from_title(value: str) -> str:
    match = _VARIANT_SUFFIX.search(value)
    return _TEXT_VERSIONS[match.group(1)] if match else ""


def _link(value: str) -> tuple[str, int | None, str, str] | None:
    """Canonical exact identity only; queries can contain a book identifier."""
    if not isinstance(value, str) or len(value) > 8192:
        return None
    try:
        uri = urlsplit(value.strip())
        if (uri.scheme not in ("http", "https") or not uri.hostname
                or uri.username is not None or uri.password is not None):
            return None
        host = uri.hostname.rstrip(".").lower()
        if host in ("www.e-hentai.org", "www.nhentai.net", "www.dlsite.com"):
            host = host.removeprefix("www.")
        port = uri.port
        path = uri.path.rstrip("/")
        if not host or (not path and not uri.query):
            return None
        return host, None if port in (80 if uri.scheme == "http" else 443, None) else port, path, uri.query
    except ValueError:
        return None


def _links(values: Iterable[str]) -> frozenset[tuple]:
    return frozenset(key for value in values if (key := _link(value)) is not None)


def _number(raw: str) -> str | Decimal:
    normalized = _key(raw)
    return Decimal(normalized) if _DIGITS.fullmatch(normalized) else normalized


def _language(value: str) -> str:
    key = _key(value).replace("_", "-")
    return "" if key in ("unknown", "und", "zxx", "未知") else _LANGUAGE_ALIASES.get(key, key)


def _languages_conflict(first: str, second: str) -> bool:
    first, second = _language(first), _language(second)
    if not first or not second:
        return False
    # Missing script/region is less specific than a language with a script qualifier.
    return first != second and not first.startswith(second + "-") and not second.startswith(first + "-")


@dataclass(frozen=True)
class _Book:
    id: str
    title: str
    titles: frozenset[str]
    authors: tuple[str, ...]
    pencillers: tuple[str, ...]
    work_group: str
    links: frozenset[tuple]
    pages: int
    language: str
    version: str
    series: str
    series_titles: frozenset[str]
    number: str
    volume: str
    remote_path: str = ""

    @property
    def reference_keys(self) -> Iterable[tuple[str, object]]:
        yield from (("title", title) for title in self.titles)
        yield from (("link", link) for link in self.links)
        if self.work_group:
            yield "group", _key(self.work_group)
        if self.number:
            yield from (("series", (title, _number(self.number))) for title in self.series_titles)


def _local(plan: ExportPlan) -> _Book:
    value: Metadata = plan.comic.metadata
    series = value.series if value.collection_mode == "series" else ""
    return _Book(
        id=plan.comic.id, title=value.display_title, titles=_titles((value.display_title, value.original_title, value.chinese_title)),
        authors=tuple(value.authors), pencillers=tuple(value.pencillers), work_group=value.work_group or "",
        links=_links(value.web), pages=len(plan.snapshot.pages), language=value.language,
        version=value.text_version or _variant_from_title(value.display_title), series=series,
        series_titles=_titles((series, value.localized_series)) if series else frozenset(),
        number=value.number if series else "", volume=value.volume if series else "",
    )


def _remote(book: dict, series: dict) -> _Book:
    if not isinstance(book.get("metadata"), dict):
        raise KomgaError("Komga 返回了不完整的作品资料，请重新核对。", "KOMGA_RESPONSE")
    raw = book["metadata"]
    shared = _mapping(series.get("metadata"))
    series_title = _text(shared.get("title")) or _text(book.get("seriesTitle")) or _text(series.get("name"))
    aliases = [_text(entry.get("title")) for entry in _entries(shared.get("alternateTitles"))]
    oneshot = book.get("oneshot") is True or series.get("oneshot") is True
    title = _text(raw.get("title")) or _text(book.get("name")) or (series_title if oneshot else "")
    if not title:
        raise KomgaError("Komga 作品缺少可比较的名称，请等待书库分析完成后重试。", "KOMGA_RESPONSE")
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    variants, _ = read_variant_fields(_entries(raw.get("notableEntities")), tags)
    authors = _entries(raw.get("authors"))
    pages = _mapping(book.get("media")).get("pagesCount")
    remote_links = _entries(raw.get("links"))
    if oneshot:
        remote_links += _entries(shared.get("links"))
    return _Book(
        id=book["id"], title=title, titles=_titles([title, series_title, *aliases] if oneshot else [title]),
        authors=tuple(_text(item.get("name")) for item in authors
                      if _key(_text(item.get("role"))) == "writer" and _text(item.get("name"))),
        pencillers=tuple(_text(item.get("name")) for item in authors
                         if _key(_text(item.get("role"))) in ("penciller", "artist", "illustrator") and _text(item.get("name"))),
        work_group=variants["work_group"], links=_links(item.get("url") for item in remote_links),
        pages=pages if type(pages) is int and pages > 0 else 0,
        language=_text(raw.get("language")) or _text(shared.get("language")),
        version=variants["text_version"] or _variant_from_title(title), series="" if oneshot else series_title,
        series_titles=frozenset() if oneshot else _titles([series_title, *aliases]),
        number="" if oneshot else _text(raw.get("number")), volume="" if oneshot else _text(raw.get("volume")),
        remote_path=server_path(book.get("url", "")),
    )


def _shared_credits(first: _Book, second: _Book) -> tuple[bool, bool]:
    """Prefer writers; shared illustrators only provide evidence when writers are missing."""
    for first_names, second_names in ((first.authors, second.authors), (first.pencillers, second.pencillers)):
        left = {_key(value) for value in first_names if _key(value)}
        right = {_key(value) for value in second_names if _key(value)}
        if left and right:
            return left == right, not left & right
    return False, False


def _compare(local: _Book, remote: _Book, same_path: bool) -> tuple[int, dict] | None:
    same_title = bool(local.titles & remote.titles)
    same_group = bool(local.work_group and remote.work_group and _key(local.work_group) == _key(remote.work_group))
    same_link = bool(local.links & remote.links)
    same_series = bool(local.series_titles & remote.series_titles)
    number_known = bool(local.number and remote.number)
    same_number = bool(number_known and _number(local.number) == _number(remote.number))
    number_conflict = number_known and not same_number
    volume_conflict = bool(local.volume and remote.volume and _number(local.volume) != _number(remote.volume))
    if same_series and (number_conflict or volume_conflict) and not same_path:
        # The same series or series-level store URL is not evidence that two volumes are the same book.
        return None

    credits_match, credits_conflict = _shared_credits(local, remote)
    same_series_book = same_series and same_number
    if not (same_path or same_group or same_link or same_series_book or (same_title and not credits_conflict)):
        return None

    pages_match = bool(local.pages and remote.pages and local.pages == remote.pages)
    version_conflict = bool(local.version and remote.version and local.version != remote.version)
    language_conflict = _languages_conflict(local.language, remote.language)
    pages_conflict = bool(local.pages and remote.pages and local.pages != remote.pages)
    version_unknown = bool(local.version) != bool(remote.version)
    reasons = [
        detail for flag, detail in (
            (same_path, "成品相对路径一致"), (same_group, "作品组一致"),
            (same_title, "标题或别名一致"), (same_link, "作品来源链接一致"),
            (same_series_book, "系列与册号一致"), (credits_match, "作者或绘画者一致"),
            (pages_match, "页数一致"), (version_conflict, "文字版本不同"),
            (language_conflict, "语言不同"), (pages_conflict, "页数不同"),
            (number_conflict, "册号不同"), (volume_conflict, "卷号不同"),
            (credits_conflict, "作者或绘画者不同"), (version_unknown, "一方的文字版本未填写"),
        ) if flag
    ]
    conflicts = (version_conflict or language_conflict or pages_conflict or number_conflict
                 or volume_conflict or credits_conflict or version_unknown)
    strong = same_path or (pages_match and ((credits_match and (same_title or same_group or same_series_book))
                                            or (same_link and (same_title or same_group))))
    status = "existing" if strong and not conflicts else "possible_version"
    if not conflicts and not strong:
        reasons.append("资料不足以确认是同一版本")
    score = (100 if status == "existing" else 0) + sum(
        points for signal, points in ((same_path, 64), (same_link, 32), (same_group, 16), (same_title, 8),
                                      (same_series_book, 4), (credits_match, 2), (pages_match, 1)) if signal
    )
    return score, {"bookId": remote.id, "title": remote.title, "authors": list(remote.authors),
                   "pageCount": remote.pages, "language": remote.language, "textVersion": remote.version,
                   "series": remote.series, "number": remote.number, "status": status, "reasons": reasons}


def compare_uploads(plans: list[ExportPlan], url: str, api_key: str, library_id: str,
                    cancel: Event | None = None, progress: Callable[[str], None] | None = None) -> dict:
    """Compare only current publications, with no dependency on local upload history."""
    client = KomgaClient(url, api_key, cancel=cancel)
    libraries = [value for value in client.libraries() if value["id"] == library_id]
    if len(libraries) != 1:
        raise KomgaError("当前配置的 Komga 书库不存在或无权访问。", "KOMGA_LIBRARY")
    if progress:
        progress("正在读取目标 Komga 书库当前的作品资料")
    books = [book for book in client.books(library_id) if book.get("libraryId") == library_id and not book.get("deleted")]
    if progress and books:
        progress("正在读取目标 Komga 书库当前的系列资料")
    series_by_id = {item["id"]: item for item in client.series(library_id)
                    if item.get("libraryId") == library_id and not item.get("deleted")} if books else {}

    index: dict[tuple[str, object], set[str]] = defaultdict(set)
    remote_by_id = {}
    for book in books:
        check_cancel(cancel)
        parent_id = book.get("seriesId")
        if not isinstance(parent_id, str) or parent_id not in series_by_id:
            raise KomgaError("Komga 作品对应的系列已变化或无法读取，请重新核对。", "KOMGA_RESPONSE")
        series = series_by_id[parent_id]
        if not isinstance(series.get("metadata"), dict):
            raise KomgaError("Komga 返回了不完整的系列资料，请重新核对。", "KOMGA_RESPONSE")
        remote = _remote(book, series)
        remote_by_id[remote.id] = remote
        for key in remote.reference_keys:
            index[key].add(remote.id)
        if remote.remote_path:
            index["filename", remote.remote_path.rsplit("/", 1)[-1].casefold()].add(remote.id)

    items = []
    for plan in plans:
        check_cancel(cancel)
        local = _local(plan)
        destinations = [plan.destination]
        if plan.occupied_destination:
            destinations.append(plan.occupied_destination)
        relative_paths = [path.parent.name + "/" + path.name for path in destinations]
        keys = [*local.reference_keys, *(("filename", path.name.casefold()) for path in destinations)]
        candidates = set().union(*(index.get(key, set()) for key in keys))
        ranked = []
        for identity in candidates:
            check_cancel(cancel)
            remote = remote_by_id[identity]
            if match := _compare(local, remote, any(path_matches(remote.remote_path, path) for path in relative_paths)):
                ranked.append(match)
        ranked.sort(key=lambda pair: (-pair[0], _key(pair[1]["title"]), pair[1]["bookId"]))
        matches = [row for _, row in ranked]
        fingerprint = hashlib.sha256(json.dumps(matches, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        items.append({"id": local.id, "status": matches[0]["status"] if matches else "new",
                      "matches": matches[:MAX_DISPLAY_MATCHES], "matchCount": len(matches), "matchFingerprint": fingerprint})
    check_cancel(cancel)
    return {"libraryId": library_id, "libraryName": libraries[0]["name"], "items": items}
