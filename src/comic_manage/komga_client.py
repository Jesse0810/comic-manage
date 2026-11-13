"""Bounded, authenticated Komga requests with no redirects or credential storage."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import socket
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .sources import check_cancel

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class KomgaError(ValueError):
    def __init__(self, message: str, code: str = "KOMGA_ERROR", *, http_status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retry_after = retry_after


def normalize_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise KomgaError("请填写有效的 Komga HTTP(S) 地址。")
    value = value.strip()
    if any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        raise KomgaError("Komga 地址包含无效字符，请使用完整 HTTP(S) 地址。")
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError
        port = parsed.port
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        if port is not None and port != (443 if parsed.scheme.lower() == "https" else 80):
            host += f":{port}"
        if any(unquote(part) in (".", "..") for part in parsed.path.split("/")):
            raise ValueError
    except (ValueError, UnicodeError):
        raise KomgaError("Komga 地址须为 HTTP(S)，且不包含用户名、密码、查询参数或片段。") from None
    path = quote(parsed.path.rstrip("/"), safe="/%:@!$&'()*+,;=-._~")
    return urlunsplit((parsed.scheme.lower(), host, path, "", ""))


def _retry_after(value: str | None) -> float | None:
    """Read only the safe scheduling hint; never expose remote error bodies."""
    if not value:
        return None
    try:
        seconds = float(value)
        if seconds >= 0 and seconds < float("inf"):
            return seconds
    except (ValueError, TypeError):
        pass
    try:
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class KomgaClient:
    def __init__(self, url: str, api_key: str, *, cancel: Event | None = None, timeout: float = 10):
        self.url = normalize_url(url)
        if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096 or any(ord(character) < 33 or ord(character) > 126 for character in api_key.strip()):
            raise KomgaError("请填写有效的单行 Komga API Key。")
        self._api_key = api_key.strip()
        self.cancel = cancel
        self.timeout = timeout
        # A configured NAS address is contacted directly. Never forward the key through an environment proxy.
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def request(self, method: str, path: str, body: dict | None = None):
        raw, _ = self._request(method, path, body, max_bytes=MAX_RESPONSE_BYTES)
        if not raw and method == "PATCH":
            return {}
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError):
            raise KomgaError("Komga 返回的内容不是有效 JSON，请检查地址和部署前缀。", "KOMGA_RESPONSE") from None

    def request_bytes(self, path: str, max_bytes: int) -> tuple[bytes, str]:
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("响应大小上限必须是正整数。")
        result = self._request("GET", path, max_bytes=max_bytes,
                               accept="image/jpeg, image/png, image/webp, image/gif")
        check_cancel(self.cancel)
        return result

    def _request(self, method: str, path: str, body: dict | None = None, *,
                 max_bytes: int, accept: str = "application/json") -> tuple[bytes, str]:
        check_cancel(self.cancel)
        data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = Request(self.url + "/" + path.lstrip("/"), data=data, method=method,
                          headers={"X-API-Key": self._api_key, "Accept": accept, "Content-Type": "application/json; charset=utf-8"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise KomgaError("Komga 响应过大，请缩小书库范围后重试。", "KOMGA_RESPONSE")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                return raw, content_type
        except HTTPError as error:
            status = error.code
            retry_after = _retry_after(error.headers.get("Retry-After")) if error.headers else None
            error.close()
            if error.code == 401:
                raise KomgaError("Komga API Key 无效或已失效，请重新配置。", "KOMGA_AUTH", http_status=status) from None
            if error.code == 403:
                raise KomgaError("当前 Komga API Key 缺少操作权限，请使用管理员凭据。", "KOMGA_FORBIDDEN", http_status=status) from None
            if 300 <= error.code < 400:
                raise KomgaError("Komga 返回了重定向，请在设置中填写最终地址。", "KOMGA_REDIRECT", http_status=status) from None
            if error.code == 404:
                raise KomgaError("Komga API 路径不存在，请检查服务器地址和部署前缀。", "KOMGA_NOT_FOUND", http_status=status) from None
            if status == 429:
                raise KomgaError("Komga 请求过于频繁，将按服务器提示稍后重试。", "KOMGA_RATE_LIMIT", http_status=status, retry_after=retry_after) from None
            if status >= 500:
                raise KomgaError(f"Komga 服务暂时不可用（HTTP {status}）。", "KOMGA_HTTP", http_status=status, retry_after=retry_after) from None
            raise KomgaError(f"Komga 请求失败（HTTP {status}）。", "KOMGA_HTTP", http_status=status) from None
        except (TimeoutError, socket.timeout):
            raise KomgaError("连接 Komga 超时，请检查地址和网络后重试。", "KOMGA_NETWORK") from None
        except (URLError, OSError, HTTPException):
            raise KomgaError("无法连接 Komga，请检查地址、网络及 HTTPS 证书。", "KOMGA_NETWORK") from None

    def libraries(self) -> list[dict]:
        response = self.request("GET", "api/v1/libraries")
        if not isinstance(response, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or not isinstance(item.get("name"), str) for item in response):
            raise KomgaError("Komga 未返回有效的书库列表。", "KOMGA_RESPONSE")
        return [{"id": item["id"], "name": item["name"]} for item in response]

    def books(self, library_id: str) -> list[dict]:
        books = {}
        for page_number in range(10000):
            response = self.request("POST", f"api/v1/books/list?page={page_number}&size=200&sort=url,asc", {
                "condition": {"libraryId": {"operator": "is", "value": library_id}},
            })
            if not isinstance(response, dict) or not isinstance(response.get("content"), list) or not isinstance(response.get("last"), bool):
                raise KomgaError("Komga 未返回有效的分页漫画列表。", "KOMGA_RESPONSE")
            for book in response["content"]:
                if not isinstance(book, dict) or not isinstance(book.get("id"), str) or not book["id"]:
                    raise KomgaError("Komga 漫画列表包含无效记录。", "KOMGA_RESPONSE")
                previous = books.get(book["id"])
                if previous is not None and previous != book:
                    raise KomgaError("Komga 书库在分页时发生变化，请重新同步。", "KOMGA_RESPONSE")
                books[book["id"]] = book
            if response["last"]:
                return list(books.values())
            if not response["content"]:
                raise KomgaError("Komga 分页结果不完整，请重新同步。", "KOMGA_RESPONSE")
        raise KomgaError("Komga 书库分页过多，请缩小书库范围后重试。", "KOMGA_RESPONSE")


def server_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        return ""
    if value.startswith("file:"):
        uri = urlsplit(value)
        value = unquote(uri.path)
        if uri.netloc:
            value = "//" + uri.netloc + value
        elif len(value) > 3 and value[0] == "/" and value[2] == ":":
            value = value[1:]
    return value.replace("\\", "/").rstrip("/")


def path_matches(remote_path: str, relative_path: str) -> bool:
    remote = server_path(remote_path)
    suffix = "/" + relative_path.replace("\\", "/").strip("/")
    if remote.startswith("//") or (len(remote) > 2 and remote[1:3] == ":/"):
        return remote.casefold().endswith(suffix.casefold())
    return remote.endswith(suffix)


def test_connection(url: str, api_key: str, cancel: Event | None = None) -> dict:
    client = KomgaClient(url, api_key, cancel=cancel)
    return {"url": client.url, "server": {"name": "Komga"}, "libraries": client.libraries()}
