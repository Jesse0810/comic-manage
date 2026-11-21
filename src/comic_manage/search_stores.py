"""Search public storefront listings for work references, without loading media."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote, urlencode, urljoin, urlsplit

from .scrape_http import ScrapeError, read_json, request_source
from .scrape_storefronts import (
    _BLOCKED, _Page, _date_fields, _dlsite_identity, _dmm_identity, _names,
    _request, _source_url, _supported_text, _text, _unique,
)
from .sources import check_cancel

_DLSITE_ORIGIN = "https://www.dlsite.com"
_DLSITE_AREAS = ("home", "maniax", "girls")
_DMM_ENDPOINT = "https://api.dmm.com/affiliate/v3/ItemList"
_DMM_AREAS = (("FANZA", "doujin"), ("FANZA", "ebook"), ("DMM.com", "ebook"))
_MAX_CANDIDATES = 16
_MAX_QUERY_CHARS = 240


def _key(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum())


def _count(value) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
        return int(value)
    return None


def _dlsite_card(card):
    identity = card.attrs.get("data-list_item_product_id", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{6,12}", identity):
        return None
    categories = [node for node in card.nodes() if "work_category" in node.attrs.get("class", "").split()]
    if not _supported_text(_text(node.text()) for node in categories):
        # These badges help keep games, audio and videos out of the result list.
        # Unknown products can still be read using the existing exact-link flow.
        return None
    name = next((node for node in card.nodes() if "work_name" in node.attrs.get("class", "").split()), None)
    if name is None:
        return None
    links = {}
    for anchor in name.nodes("a"):
        href = anchor.attrs.get("href")
        if not href:
            continue
        try:
            link = urlsplit(urljoin(_DLSITE_ORIGIN, href))
            work_id = _dlsite_identity(link)
        except (ValueError, TypeError):
            continue
        title = _text(anchor.text(), 500) or _text(anchor.attrs.get("title"), 500)
        if title and work_id == identity:
            links[_source_url(link)] = title
    if len(links) != 1:
        return None
    url, title = next(iter(links.items()))
    credits = next((node for node in card.nodes() if "maker_name" in node.attrs.get("class", "").split()), None)
    authors, publishers = [], []
    if credits:
        authors = _unique(anchor.text() for node in credits.nodes()
                          if "author" in node.attrs.get("class", "").split() for anchor in node.nodes("a"))
        publishers = _unique(node.text() for node in credits.nodes("a")
                             if "/circle/profile/" in node.attrs.get("href", ""))
    candidate = {"url": url, "title": title, "authors": authors}
    if publishers:
        candidate["publisher"] = "、".join(publishers)
    return identity, candidate


def _dlsite_results(payload):
    if not isinstance(payload, bytes) or len(payload) > 2 * 1024 * 1024:
        raise ScrapeError("DLsite 搜索结果超过可读取范围。", "SCRAPE_RESPONSE")
    try:
        parser = _Page()
        parser.feed(payload.decode("utf-8-sig"))
        parser.close()
        nodes = list(parser.root.nodes())
    except (UnicodeError, ValueError, RecursionError):
        raise ScrapeError("DLsite 未返回可识别的公开搜索结果。", "SCRAPE_RESPONSE") from None
    headings = " ".join(_text(node.text()) for node in nodes if node.tag in {"title", "h1", "h2"})
    if _BLOCKED.search(headings) or any(
        node.tag == "input" and node.attrs.get("type") == "password" for node in nodes
    ):
        raise ScrapeError("DLsite 当前要求年龄确认、登录或限制访问。", "SCRAPE_BLOCKED")
    listings = [node for node in nodes if node.attrs.get("id") == "search_result_img_box" and node.tag == "ul"]
    counts = [_count(node.attrs.get("data-search-result-count")) for node in nodes
              if node.attrs.get("data-vue-component") == "search-result-tracking"]
    count = counts[0] if len(counts) == 1 else None
    if not listings and count == 0:
        return [], True
    if len(listings) != 1:
        raise ScrapeError("DLsite 未返回可核对的作品列表，可能需要正常访问站点或站点页面已调整。", "SCRAPE_RESPONSE")
    cards = [card for card in listings[0].nodes("li") if "data-list_item_product_id" in card.attrs]
    if not cards and count != 0:
        raise ScrapeError("DLsite 未返回可核对的作品编号。", "SCRAPE_RESPONSE")
    found = [row for card in cards if (row := _dlsite_card(card))]
    complete = count is not None and count == len(cards)
    return found, complete


def _dmm_credentials(credentials):
    key = credentials.get("apiKey", "") if isinstance(credentials, dict) else ""
    affiliate = credentials.get("affiliateId", "") if isinstance(credentials, dict) else ""
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.=+/~-]{1,512}", key):
        raise ScrapeError("请填写您的 DMM/FANZA API ID。", "SCRAPE_CREDENTIALS")
    if not isinstance(affiliate, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,240}-99[0-9]", affiliate):
        raise ScrapeError("请填写 DMM/FANZA API 专用 Affiliate ID（末尾为 990–999）。", "SCRAPE_CREDENTIALS")
    return {"api_id": key, "affiliate_id": affiliate}


def _dmm_results(payload, site, service):
    value = read_json(payload).get("result")
    if not isinstance(value, dict) or str(value.get("status")) != "200":
        raise ScrapeError("DMM/FANZA API 拒绝搜索，请检查 API ID、Affiliate ID 或访问权限。", "SCRAPE_AUTH")
    total = _count(value.get("total_count"))
    entries = value.get("items")
    count = _count(value.get("result_count"))
    if entries is None and (total == 0 or count == 0):
        return [], total == 0
    if not isinstance(entries, list):
        raise ScrapeError("DMM/FANZA 未返回可核对的作品列表。", "SCRAPE_RESPONSE")
    found = []
    for item in entries[:_MAX_CANDIDATES]:
        if not isinstance(item, dict) or item.get("service_code") != service:
            continue
        try:
            link = urlsplit(item.get("URL", ""))
            identity = _dmm_identity(link)
            if item.get("content_id") != identity or link.hostname.endswith(".co.jp") != (site == "FANZA"):
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        title = _text(item.get("title"), 500)
        info = item.get("iteminfo") if isinstance(item.get("iteminfo"), dict) else {}
        if not title or service == "doujin" and not _supported_text(
            [_text(item.get("category_name"))] + _names(info.get("genre")) + _names(info.get("keyword"))
        ):
            continue
        url = _source_url(link, identity)
        candidate = {"url": url, "title": title, "authors": _names(info.get("author"))[:30]}
        if makers := _names(info.get("maker")):
            candidate["publisher"] = "、".join(makers)
        if year := _date_fields(item.get("date")).get("year"):
            candidate["year"] = year
        found.append((url, candidate))
    return found, total == len(entries) and (count is None or count == len(entries)) and len(entries) <= _MAX_CANDIDATES


def search_store(provider: str, title: str, authors: list[str], credentials: dict[str, str], cancel=None) -> list[dict]:
    """Query bounded public listings by title and prefer candidates with verified matching credits."""
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= _MAX_QUERY_CHARS or re.search(
        r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]", title
    ):
        raise ScrapeError("请输入有效的作品原名。", "SCRAPE_INPUT")
    title = title.strip()
    if provider == "dlsite":
        encoded = quote(title, safe="")
        calls = [
            (_DLSITE_ORIGIN + f"/{area}/fsr/=/keyword/{encoded}", "text/html", _dlsite_results, ())
            for area in _DLSITE_AREAS
        ]
    elif provider == "dmm":
        parameters = _dmm_credentials(credentials)
        calls = [
            (_DMM_ENDPOINT + "?" + urlencode({**parameters, "site": site, "service": service, "keyword": title,
                                              "hits": _MAX_CANDIDATES, "output": "json"}),
             "application/json", _dmm_results, (site, service))
            for site, service in _DMM_AREAS
        ]
    else:
        raise ScrapeError("此接口只支持 DLsite 和 DMM/FANZA 搜索。", "SCRAPE_PROVIDER")

    def request(url, *, accept):
        return request_source(url, accept=accept, cancel=cancel)

    unique = {}
    failures = []
    complete = True
    for url, accept, parser, args in calls:
        check_cancel(cancel)
        try:
            results, area_complete = parser(_request(request, url, accept=accept), *args)
        except ScrapeError as error:
            failures.append(error)
            complete = False
            # Hitting the API after being throttled or receiving bad credentials
            # will not make a partially completed search more reliable.
            if error.code == "SCRAPE_RATE_LIMIT" or provider == "dmm" and error.code == "SCRAPE_AUTH":
                break
            continue
        except (ValueError, TypeError, RecursionError):
            failures.append(ScrapeError("站点返回了无法读取的搜索资料。", "SCRAPE_RESPONSE"))
            complete = False
            continue
        complete = complete and area_complete
        for identity, candidate in results:
            old = unique.get(identity)
            if old:
                old["authors"] = _unique(old["authors"] + candidate["authors"])[:30]
                if old["title"] != candidate["title"]:
                    old["titles"] = _unique([*old.get("titles", []), candidate["title"]])[:8]
            else:
                unique[identity] = candidate
    check_cancel(cancel)
    if not unique and failures:
        raise failures[0]
    if not unique and not complete:
        raise ScrapeError("站点只返回了部分搜索结果，暂未找到可核对的候选。", "SCRAPE_RESPONSE")

    query_key = _key(title)
    author_keys = {_key(author) for author in authors[:30] if isinstance(author, str) and author.strip()} if isinstance(authors, list) else set()
    author_keys.discard("")

    def rank(candidate):
        titles = [candidate["title"], *candidate.get("titles", [])]
        credit = author_keys & {_key(name) for name in candidate["authors"]}
        return any(_key(name) == query_key for name in titles), len(credit)

    sorted_candidates = sorted(unique.values(), key=rank, reverse=True)
    complete = complete and len(sorted_candidates) <= _MAX_CANDIDATES
    return [{**candidate, "searchComplete": complete} for candidate in sorted_candidates[:_MAX_CANDIDATES]]
