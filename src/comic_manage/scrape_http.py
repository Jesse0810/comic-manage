"""Bounded read-only requests to the metadata providers' known endpoints."""
from __future__ import annotations

from http.client import HTTPException
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__
from .sources import check_cancel

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 8
RESPONSE_DEADLINE = 16
API_HOSTS = frozenset({"api.e-hentai.org", "nhentai.net", "www.dlsite.com", "api.dmm.com"})


class ScrapeError(ValueError):
    def __init__(self, message: str, code: str = "SCRAPE_SOURCE"):
        super().__init__(message)
        self.code = code


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_source(url: str, *, body: dict | None = None, headers: dict | None = None,
                   accept: str = "application/json", cancel=None) -> bytes:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc not in API_HOSTS:
        raise ScrapeError("不支持此资料来源。")
    headers = {
        "User-Agent": f"ComicManage/{__version__} (https://github.com/Jesse0810/comic-manage)",
        "Accept": accept, **(headers or {}),
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=json.dumps(body).encode("utf-8") if body is not None else None,
                      headers=headers)
    check_cancel(cancel)
    deadline = time.monotonic() + RESPONSE_DEADLINE
    try:
        with build_opener(_NoRedirects()).open(request, timeout=REQUEST_TIMEOUT) as response:
            media_type = response.headers.get_content_type()
            if accept.startswith("text/html"):
                valid_type = media_type in ("text/html", "application/xhtml+xml")
            else:
                valid_type = media_type in ("application/json", "text/json") or media_type.endswith("+json")
            if not valid_type:
                raise ScrapeError("站点没有返回可读取的资料，可能需要登录或遇到访问限制。", "SCRAPE_RESPONSE")
            data = bytearray()
            while True:
                check_cancel(cancel)
                if time.monotonic() > deadline:
                    raise ScrapeError("读取站点资料超时，请稍后重试。", "SCRAPE_NETWORK")
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - len(data)))
                if not chunk:
                    return bytes(data)
                data.extend(chunk)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise ScrapeError("站点返回的资料过大，无法读取。", "SCRAPE_RESPONSE")
    except HTTPError as error:
        status = error.code
        error.close()
        if status == 429:
            raise ScrapeError("站点限制了请求频率，请稍后再试。", "SCRAPE_RATE_LIMIT") from None
        if status in (401, 403):
            raise ScrapeError("站点拒绝访问。请检查站点权限和 API 凭据。", "SCRAPE_AUTH") from None
        if status in (404, 410):
            raise ScrapeError("站点找不到这条作品，请核对链接。", "SCRAPE_NOT_FOUND") from None
        if 300 <= status < 400 or status == 451:
            raise ScrapeError("站点要求跳转或限制了访问，请在浏览器中确认作品页面可用。", "SCRAPE_RESTRICTED") from None
        raise ScrapeError(f"站点暂未返回作品资料（HTTP {status}）。", "SCRAPE_HTTP") from None
    except (URLError, OSError, HTTPException):
        # A request URL can contain provider credentials. Return our own message.
        raise ScrapeError("无法连接资料站点，请检查网络后重试。", "SCRAPE_NETWORK") from None


def read_json(raw: bytes) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ScrapeError("站点未返回有效的元数据 JSON。", "SCRAPE_RESPONSE") from None
    if not isinstance(value, dict):
        raise ScrapeError("站点元数据格式无法识别。", "SCRAPE_RESPONSE")
    return value
