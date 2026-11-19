"""Search public galleries and return small, text-only candidate records.

Source contracts: https://ehwiki.org/wiki/Gallery_Searching and /wiki/API ;
https://nhentai.net/api/v2/openapi.json .
"""
from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from threading import Lock
import time
import unicodedata
from urllib.parse import urlencode, urlsplit

from .scrape_galleries import _EH_PATH, _LANGUAGES, _clean_title
from .scrape_http import ScrapeError, read_json, request_source
from .sources import check_cancel


_MAX_RESULTS = 12
_MAX_NH_TAGS = 200
_EH_SEARCH = "https://e-hentai.org/"
_EH_DETAIL = "https://api.e-hentai.org/api.php"
_NH_SEARCH = "https://nhentai.net/api/v2/search"
_NH_TAGS = "https://nhentai.net/api/v2/tags/ids"
_PRIVATE_TEXT = re.compile(r"\b(?:https?|smb|ftp|file|data|mailto):|[A-Za-z]:[\\/]|\\\\|^[\\/]|[\w.+-]+@[\w.-]+\.[a-z]{2,24}\b", re.I)
_PACING_LOCK = Lock()
_NEXT_REQUEST: dict[str, float] = {}


def _pace(route: str, interval: float, cancel) -> None:
    """Space requests globally per endpoint, independently from API credentials."""
    while True:
        check_cancel(cancel)
        with _PACING_LOCK:
            now = time.monotonic()
            elapsed = _NEXT_REQUEST.get(route, now) - now
            if elapsed <= 0:
                _NEXT_REQUEST[route] = now + interval
                return
        delay = min(elapsed, 0.2)
        if cancel is None:
            time.sleep(delay)
        else:
            cancel.wait(delay)


def _term(value, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).strip()
    if _PRIVATE_TEXT.search(value):
        return ""
    # Quote only text. Drop characters which could escape the provider's query grammar.
    value = re.sub(r'["\\~:%$*_\x00-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]', " ", value)
    return " ".join(value.split())[:maximum].strip()


def _display(value, maximum: int = 500) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]", " ", value).strip()[:maximum].rstrip()


def _identity(value) -> int | None:
    if (not isinstance(value, bool) and isinstance(value, (str, int))
            and re.fullmatch(r"[1-9][0-9]{0,11}", str(value))):
        return int(value)
    return None


def _aliases(values) -> list[str]:
    return list(dict.fromkeys(text for value in values if (text := _display(value))))


def _candidate(url: str, titles: list[str], tags: list[tuple[str, str]], page_count, complete: bool) -> dict:
    title = _clean_title(titles[0], []) or titles[0]
    normalized = [(_display(kind).casefold(), _display(value)) for kind, value in tags]
    authors = _aliases(value for kind, value in normalized if kind == "artist")
    publisher = _aliases(value for kind, value in normalized if kind == "group")
    languages = {code for kind, value in normalized if kind == "language"
                 and (code := _LANGUAGES.get(value.casefold()))}
    candidate = {"url": url, "title": title, "titles": _aliases([title, *titles]),
                 "authors": authors, "searchComplete": complete}
    if publisher:
        candidate["publisher"] = ", ".join(publisher[:4])
    if len(languages) == 1:
        candidate["language"] = languages.pop()
    if pages := _identity(page_count):
        candidate["pageCount"] = pages
    return candidate


def _gallery_reference(href: str) -> tuple[int, str] | None:
    if not href or len(href) > 2048:
        return None
    try:
        url = urlsplit(href)
    except ValueError:
        return None
    if url.scheme or url.netloc:
        if url.scheme not in ("https", "http") or url.netloc not in ("e-hentai.org", "www.e-hentai.org"):
            return None
    match = _EH_PATH.fullmatch(url.path)
    return (int(match[1]), match[2].lower()) if match else None


class _EHasGalleryResults(HTMLParser):
    """Read canonical gallery links from the result container only."""

    def __init__(self):
        super().__init__()
        self.references: dict[tuple[int, str], None] = {}
        self.search_field = False
        self.empty = False
        self.more = False
        self._container = ""
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = {name: value or "" for name, value in attrs}
        if tag == "input" and attrs.get("name") == "f_search":
            self.search_field = True
        if self._container and tag == self._container:
            self._depth += 1
        elif not self._container and tag in ("table", "div") and "itg" in attrs.get("class", "").split():
            self._container = tag
            self._depth = 1
        if tag != "a":
            return
        href = attrs.get("href", "")
        if attrs.get("id") in ("unext", "dnext") and href and href != "#":
            self.more = True
        if self._container and (reference := _gallery_reference(href)):
            self.references[reference] = None

    def handle_endtag(self, tag):
        if tag == self._container:
            self._depth -= 1
            if not self._depth:
                self._container = ""

    def handle_data(self, data):
        if data.strip().casefold() == "no hits found":
            self.empty = True


def _eh_search(query: str, cancel) -> tuple[list[tuple[int, str]], bool]:
    _pace("ehentai-search", 3.1, cancel)
    raw = request_source(_EH_SEARCH + "?" + urlencode({"f_search": query}), accept="text/html", cancel=cancel)
    parser = _EHasGalleryResults()
    try:
        parser.feed(raw.decode("utf-8"))
        parser.close()
    except (UnicodeError, ValueError):
        raise ScrapeError("E-Hentai 的公开搜索页面无法读取。", "SCRAPE_RESPONSE") from None
    if not parser.search_field or not (parser.references or parser.empty):
        raise ScrapeError("E-Hentai 未返回可识别的搜索结果，请检查公开页面是否可用。", "SCRAPE_RESPONSE")
    return list(parser.references), not parser.more


def _eh_results(title: str, artist: str, cancel) -> list[dict]:
    phrase = f'title:"{title}"'
    records, complete = _eh_search(phrase + f' artist:"{artist}"' if artist else phrase, cancel)
    if not records and artist:
        records, fallback_complete = _eh_search(phrase, cancel)
        complete = complete and fallback_complete
    if not records:
        if not complete:
            raise ScrapeError("E-Hentai 搜索结果不完整，暂时无法确认没有候选。", "SCRAPE_RESPONSE")
        return []
    complete = complete and len(records) <= _MAX_RESULTS
    selected = records[:_MAX_RESULTS]
    data = read_json(request_source(_EH_DETAIL, body={
        "method": "gdata", "namespace": 1, "gidlist": [list(reference) for reference in selected],
    }, cancel=cancel))
    rows = data.get("gmetadata")
    if not isinstance(rows, list) or len(rows) > 25:
        raise ScrapeError("E-Hentai 未返回可确认的候选画廊详情。", "SCRAPE_RESPONSE")
    valid = {}
    for item in rows:
        if not isinstance(item, dict) or item.get("error") or item.get("expunged") is True:
            continue
        reference = (_identity(item.get("gid")), _display(item.get("token")).lower())
        if reference not in selected or reference in valid:
            continue
        titles = _aliases([item.get("title_jpn"), item.get("title")])
        raw_tags = item.get("tags")
        if not titles or not isinstance(raw_tags, list) or len(raw_tags) > 1024:
            continue
        tags = [tuple(value.split(":", 1)) for value in raw_tags if isinstance(value, str) and ":" in value]
        gid, token = reference
        valid[reference] = _candidate(f"https://e-hentai.org/g/{gid}/{token}/", titles, tags, item.get("filecount"), complete)
    if not valid:
        raise ScrapeError("E-Hentai 搜索候选未能与官方画廊详情核对。", "SCRAPE_RESPONSE")
    result = [valid[key] for key in selected if key in valid]
    if len(result) != len(selected):
        for item in result:
            item["searchComplete"] = False
    return result


def _nh_search(query: str, headers: dict, cancel) -> tuple[list, bool]:
    _pace("nhentai-search", 3.1 if headers else 6.1, cancel)
    data = read_json(request_source(_NH_SEARCH + "?" + urlencode({"query": query}), headers=headers, cancel=cancel))
    items, pages = data.get("result"), data.get("num_pages")
    if not isinstance(items, list) or len(items) > 1000 or type(pages) is not int or pages < 0:
        raise ScrapeError("nhentai 返回的搜索资料格式无法识别。", "SCRAPE_RESPONSE")
    return items, pages <= 1


def _nh_tags(ids: list[int], cancel) -> dict[int, tuple[str, str]]:
    tags = {}
    for start in range(0, len(ids), 100):
        check_cancel(cancel)
        chunk = ids[start:start + 100]
        requested = set(chunk)
        _pace("nhentai-tags", 4.1, cancel)
        raw = request_source(_NH_TAGS + "?" + urlencode({"ids": ",".join(map(str, chunk))}), cancel=cancel)
        try:
            rows = json.loads(raw)
        except (ValueError, RecursionError):
            raise ScrapeError("nhentai 返回的标签资料格式无法识别。", "SCRAPE_RESPONSE") from None
        if not isinstance(rows, list) or len(rows) > 100:
            raise ScrapeError("nhentai 返回的标签资料格式无法识别。", "SCRAPE_RESPONSE")
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = _identity(row.get("id"))
            kind, name = _display(row.get("type")), _display(row.get("name"))
            if key in requested and kind and name:
                tags[key] = (kind, name)
    return tags


def _nh_results(title: str, artist: str, credentials: dict, cancel) -> list[dict]:
    api_key = credentials.get("apiKey")
    if api_key is not None and (not isinstance(api_key, str) or not re.fullmatch(r"[A-Za-z0-9_.=+/~-]{1,512}", api_key)):
        raise ScrapeError("nhentai API Key 格式无效，请重新填写。", "SCRAPE_CREDENTIALS")
    headers = {"Authorization": "Key " + api_key} if api_key else {}
    phrase = f'"{title}"'
    items, complete = _nh_search(phrase + f' artist:"{artist}"' if artist else phrase, headers, cancel)
    if not items and artist:
        items, fallback_complete = _nh_search(phrase, headers, cancel)
        complete = complete and fallback_complete
    if not items:
        if not complete:
            raise ScrapeError("nhentai 搜索结果不完整，暂时无法确认没有候选。", "SCRAPE_RESPONSE")
        return []
    complete = complete and len(items) <= _MAX_RESULTS
    choices = {}
    for item in items:
        if not isinstance(item, dict) or item.get("blacklisted") is True:
            continue
        gid = _identity(item.get("id"))
        titles = _aliases([item.get("japanese_title"), item.get("english_title")])
        if not gid or not titles:
            continue
        raw_tag_ids = item.get("tag_ids")
        tag_ids = list(dict.fromkeys(key for value in raw_tag_ids[:1024] if (key := _identity(value)))) if isinstance(raw_tag_ids, list) else []
        choices.setdefault(gid, (titles, tag_ids, item.get("num_pages")))
    if not choices and complete and all(isinstance(item, dict) and item.get("blacklisted") is True for item in items):
        return []
    if not choices:
        raise ScrapeError("nhentai 搜索结果未能返回可核对的画廊。", "SCRAPE_RESPONSE")
    complete = complete and len(choices) == len(items)
    choices = dict(list(choices.items())[:_MAX_RESULTS])
    needed = list(dict.fromkeys(key for _, ids, _ in choices.values() for key in ids))
    tags = _nh_tags(needed[:_MAX_NH_TAGS], cancel)
    return [_candidate(f"https://nhentai.net/g/{gid}/", titles, [tags[key] for key in ids if key in tags],
                       pages, complete and all(key in tags for key in ids))
            for gid, (titles, ids, pages) in choices.items()]


def search_gallery(provider: str, title: str, authors: list[str], credentials: dict, cancel=None) -> list[dict]:
    """Search a known provider for canonical gallery links. Do not follow URLs from metadata."""
    if provider not in ("ehentai", "nhentai"):
        raise ScrapeError("当前资料来源不是已支持的画廊站点。", "SCRAPE_URL")
    if not isinstance(credentials, dict) or set(credentials) - ({"apiKey"} if provider == "nhentai" else set()):
        raise ScrapeError("请只填写当前站点需要的 API 凭据。", "SCRAPE_CREDENTIALS")
    keyword = _term(title, 140)
    if not keyword:
        raise ScrapeError("作品原名缺失或无法用作公开搜索关键词。", "SCRAPE_QUERY")
    artist = next((name for value in authors[:10] if (name := _term(value, 35))), "") if isinstance(authors, list) else ""
    if provider == "ehentai":
        return _eh_results(keyword, artist, cancel)
    return _nh_results(keyword, artist, credentials, cancel)
