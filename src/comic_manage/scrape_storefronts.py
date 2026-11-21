"""Text-only storefront adapters. No images, cookies, logins or redirect following.

DMM v3 field names are documented in the historical DMMcomLabo SDK:
https://github.com/dmmlabo/dmm-go-sdk/blob/master/api/product.go
DLsite's private JSON endpoints are deliberately not used. HTML fallbacks accept
explicit product-page labels, not arbitrary body text or a guessed title.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import SplitResult, parse_qs, unquote, urlencode, urlsplit, urlunsplit

from .scrape_http import ScrapeError, read_json
from .sources import Cancelled


DMM_HOSTS = frozenset({"dmm.com", "dmm.co.jp", "www.dmm.com", "www.dmm.co.jp", "book.dmm.com", "book.dmm.co.jp"})
DLSITE_HOSTS = frozenset({"www.dlsite.com", "dlsite.com"})
_GENRES = {
    "漫画": "漫画", "マンガ": "漫画", "コミック": "漫画", "comic": "漫画", "manga": "漫画",
    "小説": "小说", "ノベル": "小说", "小说": "小说", "novel": "小说",
    "CG集": "CG集", "cg": "CG集",
    "画集": "画集", "設定資料集": "设定集", "設定集": "设定集", "设定集": "设定集",
    "イラスト集": "插画集", "插画集": "插画集",
}
_DLSITE_PATH = re.compile(r"^/[a-z][a-z0-9-]*/work/=/product_id/([A-Z]{2}\d{6,12})\.html/?$", re.I)
_CID = re.compile(r"^[A-Za-z0-9_]{1,128}$")
_BOOK_TYPES = {"Book", "ComicIssue", "ComicSeries", "ComicStory"}
_BLOCKED = re.compile(r"年齢(?:確認|認証)|あなたは\s*18\s*歳以上|18\s*歳以上ですか|"
                      r"access denied|just a moment|attention required|verify (?:that )?you are human|"
                      r"not available in your region|お住まいの地域|アクセスが制限|アクセスを制限", re.I)


def _text(value, limit=4096):
    if not isinstance(value, str):
        return ""
    result = " ".join(unescape(value).split())
    if not result or len(result) > limit or any(ord(char) < 32 for char in result):
        return ""
    # Keep returned candidates as plain text; never pass source markup to the UI.
    return re.sub(r"<[^>]*>", "", result).strip()


def _unique(values):
    result = []
    for value in values:
        item = _text(value)
        if item and item not in result:
            result.append(item)
    return result[:100]


def _names(value):
    if isinstance(value, str):
        return [_text(value)] if _text(value) else []
    if isinstance(value, dict):
        return _names(value.get("name"))
    if isinstance(value, list):
        return _unique(name for item in value for name in _names(item))
    return []


def _date_fields(value):
    if not isinstance(value, str):
        return {}
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ][0-9:.+Z-]+)?", value.strip())
    if not match:
        match = re.fullmatch(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", value.strip())
    if not match:
        return {}
    try:
        stamp = date(*(int(part) for part in match.groups()))
    except ValueError:
        return {}
    return {"year": str(stamp.year), "month": str(stamp.month), "day": str(stamp.day)}


def _genre(values):
    genres = _unique(_GENRES.get(value, _GENRES.get(value.lower(), "")) for value in _unique(values))
    return genres[0] if len(genres) == 1 else ""


def _supported_text(values):
    return any(value in {"CG・イラスト", "CG・イラスト集"} or value in _GENRES or value.lower() in _GENRES
               for value in _unique(values))


def _validate_url(url, hosts):
    try:
        valid = (url.scheme == "https" and url.hostname in hosts and url.port in (None, 443)
                 and url.username is None and url.password is None and "\\" not in url.netloc)
    except ValueError:
        valid = False
    if not valid:
        raise ScrapeError("请使用支持站点的 HTTPS 作品详情地址。", "SCRAPE_URL")


def _dlsite_identity(url):
    _validate_url(url, DLSITE_HOSTS)
    match = _DLSITE_PATH.fullmatch(unquote(url.path))
    if not match:
        raise ScrapeError("DLsite 请填写具体作品的 product_id 详情页地址。", "SCRAPE_URL")
    return match.group(1).upper()


def _dmm_identity(url):
    _validate_url(url, DMM_HOSTS)
    path = unquote(url.path)
    host = url.hostname
    if host.startswith("book."):
        allowed = path.startswith("/detail/") or path.startswith("/product/")
    else:
        allowed = bool(re.match(r"^/dc/(?:doujin|book)/-/detail/", path))
    if not allowed:
        raise ScrapeError("DMM/FANZA 仅支持电子书或同人作品详情页，不支持视频、搜索及作者页。", "SCRAPE_URL")
    matches = re.findall(r"(?:^|/)cid=([A-Za-z0-9_]+)(?:/|$)", path)
    query = parse_qs(url.query, keep_blank_values=True)
    matches.extend(query.get("cid", []))
    if host.startswith("book.") and not matches:
        match = re.fullmatch(r"/detail/([A-Za-z0-9_]+)/?", path)
        if not match:
            match = re.fullmatch(r"/product/\d+/([A-Za-z0-9_]+)/?", path)
        if match:
            matches.append(match.group(1))
    if not matches or len(set(matches)) != 1 or not _CID.fullmatch(matches[0]):
        raise ScrapeError("作品地址未包含唯一、可确认的 DMM content ID。", "SCRAPE_URL")
    return matches[0]


def _source_url(url, identity=""):
    query = ""
    if identity and "cid" in parse_qs(url.query) and not re.search(r"/cid=", unquote(url.path)):
        query = urlencode({"cid": identity})
    host = "www.dlsite.com" if url.hostname == "dlsite.com" else url.hostname
    return urlunsplit(("https", host, url.path, query, ""))


def _request(request, url, *, accept):
    try:
        return request(url, accept=accept)
    except Cancelled:
        raise
    except ScrapeError as error:
        messages = {
            "SCRAPE_RATE_LIMIT": "站点限制了读取频率，请稍后重试。",
            "SCRAPE_AUTH": "站点拒绝访问，请检查站点权限或 API 凭据。",
            "SCRAPE_RESTRICTED": "站点要求跳转或限制了访问，请正常访问站点后重试。",
            "SCRAPE_NOT_FOUND": "站点找不到这条作品，请核对作品链接。",
            "SCRAPE_RESPONSE": "站点未返回可识别的公开资料。",
            "SCRAPE_NETWORK": "无法连接资料站点，请检查网络后重试。",
        }
        code = error.code if error.code in messages else "SCRAPE_SOURCE"
        raise ScrapeError(messages.get(code, "来源暂时不可读取，请稍后重试。"), code) from None
    except Exception:
        # API URLs can contain credentials; never echo errors/URLs from transports.
        raise ScrapeError("来源暂时不可读取，请检查网络、站点限制或 API 配置后重试。", "SCRAPE_SOURCE") from None


def _dmm(url, credentials, request):
    identity = _dmm_identity(url)
    api_key = credentials.get("apiKey", "")
    affiliate_id = credentials.get("affiliateId", "")
    if (not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 1024
            or re.search(r"[\s\x00]", api_key)):
        raise ScrapeError("DMM/FANZA 需要填写您自己的 API ID。", "SCRAPE_CREDENTIALS")
    if (not isinstance(affiliate_id, str) or len(affiliate_id) > 256
            or not re.fullmatch(r"[A-Za-z0-9_-]+-99[0-9]", affiliate_id)):
        raise ScrapeError("请填写 API 专用 Affiliate ID（末尾为 990–999）。", "SCRAPE_CREDENTIALS")
    site = "FANZA" if url.hostname.endswith(".co.jp") else "DMM.com"
    query = urlencode({"api_id": api_key, "affiliate_id": affiliate_id, "site": site,
                       "cid": identity, "hits": 1, "output": "json"})
    payload = _request(request, "https://api.dmm.com/affiliate/v3/ItemList?" + query, accept="application/json")
    try:
        value = read_json(payload)
    except Exception:
        raise ScrapeError("DMM/FANZA 未返回可识别的 API 数据；请检查接口配置或访问限制。", "SCRAPE_SOURCE") from None
    result = value.get("result") if isinstance(value, dict) else None
    if not isinstance(result, dict) or str(result.get("status")) != "200":
        raise ScrapeError("DMM/FANZA API 拒绝了请求，请检查 API ID、Affiliate ID 或稍后重试。", "SCRAPE_SOURCE")
    items = result.get("items")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ScrapeError("未找到唯一匹配的 DMM/FANZA 作品；未自动选择相近结果。", "SCRAPE_MATCH")
    item = items[0]
    if item.get("content_id") != identity:
        raise ScrapeError("DMM/FANZA 返回作品与所填 content ID 不一致。", "SCRAPE_MATCH")
    try:
        link = urlsplit(item.get("URL", ""))
        matched = _dmm_identity(link)
        same_site = link.hostname.endswith(".co.jp") == (site == "FANZA")
    except (ValueError, TypeError, AttributeError):
        raise ScrapeError("DMM/FANZA 返回的作品链接无法核对。", "SCRAPE_MATCH") from None
    if matched != identity or not same_site:
        raise ScrapeError("DMM/FANZA 返回的作品链接与目标不一致。", "SCRAPE_MATCH")
    info = item.get("iteminfo") if isinstance(item.get("iteminfo"), dict) else {}
    tags = _unique(_names(info.get("genre")) + _names(info.get("keyword")))
    type_values = [_text(item.get("category_name")), *tags]
    content_genre = _genre(type_values)
    service = _text(item.get("service_code"))
    if service not in {"ebook", "doujin"} or (service == "doujin" and not _supported_text(type_values)):
        raise ScrapeError("仅可确认电子书或漫画、小说、画集等同人图文作品；未读取音声、游戏或视频。", "SCRAPE_UNSUPPORTED")
    title = _text(item.get("title"))
    if not title:
        raise ScrapeError("来源未提供可确认的作品标题。", "SCRAPE_SOURCE")
    fields = {"title": title}
    authors = _names(info.get("author"))
    makers = _names(info.get("maker"))
    if authors:
        fields["authors"] = authors
    if makers:
        fields["publisher"] = "、".join(makers)
    if tags:
        fields["tags"] = tags
    if content_genre:
        fields["genre"] = content_genre
    fields.update(_date_fields(item.get("date")))
    if service == "doujin" and site == "FANZA":
        fields["release_platforms"] = ["FANZA同人"]
    return {"provider": "dmm", "sourceUrl": _source_url(link, identity), "fields": fields, "warnings": []}


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)

    def nodes(self, tag=None):
        for child in self.children:
            if isinstance(child, _Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.nodes(tag)

    def text(self):
        return " ".join(child if isinstance(child, str) else child.text()
                        for child in self.children if not isinstance(child, _Node) or child.tag not in {"script", "style"})


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if len(self.stack) > 64 or self.count > 30_000:
            raise ValueError("Page structure exceeds parser bounds")
        node = _Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _schema_nodes(value, depth=0):
    if depth > 16:
        return
    if isinstance(value, list):
        for item in value:
            yield from _schema_nodes(item, depth + 1)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from _schema_nodes(value["@graph"], depth + 1)
        for key in ("mainEntity", "item"):
            if key in value:
                yield from _schema_nodes(value[key], depth + 1)


def _schema_types(node):
    values = node.get("@type", [])
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return set()
    return {item.rsplit("/", 1)[-1] for item in values if isinstance(item, str)}


def _dlsite(url, request):
    identity = _dlsite_identity(url)
    source = _source_url(url)
    payload = _request(request, source, accept="text/html")
    if not isinstance(payload, bytes) or len(payload) > 4 * 1024 * 1024:
        raise ScrapeError("来源页面超过可解析范围。", "SCRAPE_SOURCE")
    try:
        text = payload.decode("utf-8-sig")
        parser = _Page()
        parser.feed(text)
    except (UnicodeError, ValueError, RecursionError):
        raise ScrapeError("DLsite 未返回可识别的公开作品页面。", "SCRAPE_SOURCE") from None
    nodes = list(parser.root.nodes())
    headings = " ".join(_text(node.text()) for node in nodes if node.tag in {"title", "h1", "h2"})
    if _BLOCKED.search(headings) or re.search(r"ログイン|sign in|log in", headings, re.I) and any(
            node.tag == "input" and node.attrs.get("type") == "password" for node in nodes):
        raise ScrapeError("DLsite 返回年龄确认、登录或访问受限页面；请在站点正常访问后重试。", "SCRAPE_BLOCKED")
    # A canonical link is evidence of another work, not a reason to follow it.
    for node in nodes:
        if node.tag == "link" and "canonical" in node.attrs.get("rel", "").split():
            try:
                canonical_id = _dlsite_identity(urlsplit(node.attrs.get("href", "")))
            except (ValueError, TypeError):
                raise ScrapeError("DLsite 作品页的规范地址无法核对。", "SCRAPE_MATCH") from None
            if canonical_id != identity:
                raise ScrapeError("DLsite 返回了另一作品的页面。", "SCRAPE_MATCH")
    title_node = next((node for node in nodes if node.tag == "h1" and node.attrs.get("id") == "work_name"), None)
    page_title = _text(title_node.text()) if title_node else ""
    rows = {}
    # Current public DLsite pages use definition-list productInfo rows. The
    # product's JSON-LD sku identifies the work; these visible labels add credits,
    # date, format and tags. Older table markup is still supported below.
    for item in (node for node in nodes if "c-productInfo__item" in node.attrs.get("class", "").split()):
        heading = next(item.nodes("dt"), None)
        value = next(item.nodes("dd"), None)
        if heading and value:
            rows[_text(heading.text())] = value
    for table in (node for node in nodes if node.tag == "table" and node.attrs.get("id") == "work_outline"):
        for row in table.nodes("tr"):
            heading = next(row.nodes("th"), None)
            value = next(row.nodes("td"), None)
            if heading and value:
                rows[_text(heading.text())] = value
    schema = []
    for script in (node for node in nodes if node.tag == "script" and node.attrs.get("type", "").lower() == "application/ld+json"):
        try:
            raw = json.loads("".join(item for item in script.children if isinstance(item, str)))
        except (ValueError, RecursionError):
            continue
        for node in _schema_nodes(raw):
            if not (_schema_types(node) & (_BOOK_TYPES | {"Product"})):
                continue
            identifiers = [node.get("sku"), node.get("productID"), node.get("identifier")]
            work_ids = [value.upper() for value in identifiers if isinstance(value, str) and re.fullmatch(r"[A-Z]{2}\d{6,12}", value, re.I)]
            node_urls = [node.get("url"), node.get("@id")]
            ids = []
            invalid_link = False
            for value in node_urls:
                if isinstance(value, str) and value.startswith("https://"):
                    try:
                        ids.append(_dlsite_identity(urlsplit(value.split("#", 1)[0])))
                    except ValueError:
                        invalid_link = True
            exact = identity in ids or identity in work_ids
            if exact and (invalid_link or any(value != identity for value in ids + work_ids)):
                raise ScrapeError("DLsite 页面中的作品编号与详情链接不一致。", "SCRAPE_MATCH")
            if exact or not ids and not any(identifiers) and page_title and _text(node.get("name")) == page_title:
                schema.append(node)
    if len(schema) > 1:
        raise ScrapeError("DLsite 页面有多个作品资料，无法唯一核对。", "SCRAPE_MATCH")
    selected = schema[0] if schema else {}
    # Explicit labels and schema type are the only content-type evidence.
    format_node = rows.get("作品形式")
    format_values = [_text(format_node.text())] if format_node else []
    if format_node:
        format_values += [_text(node.text()) for node in format_node.nodes() if node.tag in {"a", "span"}]
    schema_genres = _names(selected.get("genre")) + _names(selected.get("category"))
    visible_genres = [_text(node.text()) for node in rows["ジャンル"].nodes("a")] if "ジャンル" in rows else []
    type_values = format_values + schema_genres + visible_genres
    content_genre = _genre(type_values)
    if not content_genre and "ComicIssue" in _schema_types(selected):
        content_genre = "漫画"
    if not _supported_text(type_values) and not (_schema_types(selected) & _BOOK_TYPES):
        if page_title or selected:
            raise ScrapeError("DLsite 页面未明确标注可支持的图文作品类型。", "SCRAPE_UNSUPPORTED")
        raise ScrapeError("DLsite 未提供可识别的公开作品文字结构；请保留手工资料。", "SCRAPE_SOURCE")
    title = _text(selected.get("name")) or page_title
    if not title or not selected and not rows:
        raise ScrapeError("DLsite 未提供可核对的作品标题及资料结构。", "SCRAPE_SOURCE")
    fields = {"title": title, "release_platforms": ["DLsite"]}
    authors = _names(selected.get("author"))
    if not authors:
        for label in ("作者", "著者"):
            if label in rows:
                authors = _unique(_text(node.text()) for node in rows[label].nodes("a")) or _names(_text(rows[label].text()))
                break
    publisher = _names(selected.get("publisher"))
    if not publisher:
        for label in ("サークル名", "出版社", "ブランド名"):
            if label in rows:
                publisher = _names(_text(rows[label].text()))
                break
    if authors:
        fields["authors"] = authors
    if publisher:
        fields["publisher"] = "、".join(publisher)
    tags = _names(selected.get("keywords"))
    # Schema.org permits comma-separated keywords; do not split personal names.
    if isinstance(selected.get("keywords"), str):
        tags = _unique(selected["keywords"].split(","))
    tags += visible_genres
    tags = _unique(tags + schema_genres)
    if tags:
        fields["tags"] = tags
    if content_genre:
        fields["genre"] = content_genre
    dates = _date_fields(selected.get("datePublished"))
    if not dates and "販売日" in rows:
        dates = _date_fields(_text(rows["販売日"].text()))
    fields.update(dates)
    return {"provider": "dlsite", "sourceUrl": source, "fields": fields, "warnings": []}


def fetch_storefront(provider: str, url: SplitResult, credentials: dict[str, str], request) -> dict:
    if provider == "dlsite":
        return _dlsite(url, request)
    if provider == "dmm":
        return _dmm(url, credentials, request)
    raise ScrapeError("不支持的站点。", "SCRAPE_PROVIDER")
