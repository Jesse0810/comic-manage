"""Text-only model requests to an endpoint explicitly selected by the user."""
from __future__ import annotations

from http.client import HTTPException
import ipaddress
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .sources import check_cancel


PROVIDERS = frozenset({"openai", "openai-responses", "ollama", "anthropic", "gemini"})
MAX_REQUEST_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_CHARS = 64 * 1024
MAX_OUTPUT_TOKENS = 4096
REQUEST_TIMEOUT = 60
RESPONSE_DEADLINE = 60
_LOCAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "fc00::/7", "fe80::/10",
))
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_SAFE_PATH = re.compile(r"[A-Za-z0-9._~/-]*")
_ENDPOINTS = {
    "openai": "/chat/completions",
    "openai-responses": "/responses",
    "ollama": "/api/chat",
    "anthropic": "/messages",
}
_OFFICIAL_PREFIXES = {
    "api.openai.com": {"openai": "/v1", "openai-responses": "/v1"},
    "api.anthropic.com": {"anthropic": "/v1"},
    "generativelanguage.googleapis.com": {"gemini": "/v1beta"},
}


class ModelTransportError(ValueError):
    def __init__(self, message: str, code: str = "MODEL_ERROR"):
        super().__init__(message)
        self.code = code


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _local_host(host: str) -> bool:
    if host == "localhost" or host.endswith((".localhost", ".local", ".lan")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback or any(address in network for network in _LOCAL_NETWORKS)


def is_local_endpoint(base_url: str) -> bool:
    """Recognize hostnames and addresses users commonly select for local models."""
    try:
        return _local_host((urlsplit(base_url).hostname or "").rstrip("."))
    except ValueError:
        return False


def _normalize_url(value: str, provider: str, model: str) -> str:
    invalid = ModelTransportError("请输入有效的模型服务地址。地址不能包含凭据、查询参数或片段。", "MODEL_CONFIG")
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise invalid
    value = value.strip()
    if any(character.isspace() or ord(character) < 32 for character in value) or any(
        character in value for character in ("\\", "%", "?", "#", "@")
    ):
        raise invalid
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").rstrip(".").encode("idna").decode("ascii")
        port = parts.port
    except (ValueError, UnicodeError):
        raise invalid from None
    if parts.scheme not in ("https", "http") or not host or port == 0 or parts.netloc.endswith(":"):
        raise invalid
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or any(not _HOST_LABEL.fullmatch(label) for label in host.split(".")):
            raise invalid from None
        netloc = host
    else:
        netloc = f"[{address.compressed}]" if address.version == 6 else str(address)
    if parts.scheme == "http" and not _local_host(host):
        raise ModelTransportError("公网模型地址需要使用 HTTPS。本机和局域网模型支持 HTTP。", "MODEL_CONFIG")
    if port is not None and port != {"https": 443, "http": 80}[parts.scheme]:
        netloc = f"{netloc}:{port}"

    path = parts.path.rstrip("/")
    suffix = _ENDPOINTS.get(provider)
    if provider == "gemini":
        suffix = f"/models/{quote(model, safe='')}:generateContent"
    if suffix and path.endswith(suffix):
        path = path.removesuffix(suffix)
    if provider == "ollama" and path.endswith("/api"):
        path = path.removesuffix("/api")
    if not _SAFE_PATH.fullmatch(path) or any(segment in (".", "..") for segment in path.split("/")):
        raise invalid
    default_path = _OFFICIAL_PREFIXES.get(host, {}).get(provider, "")
    return urlunsplit((parts.scheme, netloc, path or default_path, "", ""))


def validate_profile(profile: dict) -> dict:
    """Return transport fields only. Profile names and credentials belong to the caller."""
    if not isinstance(profile, dict) or not isinstance(profile.get("provider"), str) or profile["provider"] not in PROVIDERS:
        raise ModelTransportError("请选择支持的模型接口。", "MODEL_CONFIG")
    provider = profile["provider"]
    model = profile.get("model")
    if not isinstance(model, str) or not model.strip() or len(model) > 256 or any(
        ord(character) < 32 or ord(character) == 127 for character in model
    ):
        raise ModelTransportError("请输入有效的模型 ID。", "MODEL_CONFIG")
    model = model.strip()
    if provider == "gemini":
        model = model.removeprefix("models/")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", model) or model in (".", ".."):
            raise ModelTransportError("Gemini 模型 ID 无效。请输入模型名称。", "MODEL_CONFIG")
    return {"provider": provider, "base_url": _normalize_url(profile.get("base_url"), provider, model), "model": model}


def _credential(value: str) -> str:
    if not isinstance(value, str) or len(value) > 4096 or any(
        ord(character) < 32 or ord(character) > 126 for character in value
    ):
        raise ModelTransportError("API Key 格式无效。", "MODEL_CONFIG")
    return value.strip()


def _make_request(profile: dict, api_key: str, system: str, prompt: str) -> Request:
    provider = profile["provider"]
    base_url = profile["base_url"]
    model = profile["model"]
    host = urlsplit(base_url).hostname
    if not api_key and provider in _OFFICIAL_PREFIXES.get(host, {}):
        raise ModelTransportError("此接口需要 API Key。请在模型管理中填写。", "MODEL_AUTH")

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    suffix = _ENDPOINTS.get(provider)
    if provider == "openai":
        token_field = "max_completion_tokens" if host == "api.openai.com" else "max_tokens"
        body = {"model": model, "messages": messages, "stream": False, token_field: MAX_OUTPUT_TOKENS}
    elif provider == "openai-responses":
        body = {"model": model, "instructions": system, "input": prompt, "stream": False,
                "store": False, "max_output_tokens": MAX_OUTPUT_TOKENS}
    elif provider == "ollama":
        body = {"model": model, "messages": messages, "stream": False, "options": {"num_predict": MAX_OUTPUT_TOKENS}}
    elif provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        body = {"model": model, "system": system, "messages": [messages[1]], "max_tokens": MAX_OUTPUT_TOKENS}
    else:
        suffix = f"/models/{quote(model, safe='')}:generateContent"
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS}}

    if api_key:
        if provider == "anthropic":
            headers["x-api-key"] = api_key
        elif provider == "gemini":
            headers["x-goog-api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    try:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    except UnicodeError:
        raise ModelTransportError("发送给模型的文本无法编码。", "MODEL_CONFIG") from None
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ModelTransportError("发送给模型的文本过大。", "MODEL_CONFIG")
    return Request(base_url + suffix, data=encoded, headers=headers, method="POST")


def _http_error(status: int) -> ModelTransportError:
    if status in (401, 403):
        return ModelTransportError("模型服务拒绝访问。请核对 API Key 和模型权限。", "MODEL_AUTH")
    if status == 429:
        return ModelTransportError("模型服务已达到请求频率或用量限制。", "MODEL_RATE_LIMIT")
    if status == 404:
        return ModelTransportError("模型服务找不到此接口或模型。请核对服务地址、接口协议和模型 ID。", "MODEL_NOT_FOUND")
    if 300 <= status < 400:
        return ModelTransportError("模型服务要求跳转。请填写最终服务地址。", "MODEL_CONFIG")
    return ModelTransportError(f"模型服务未接受请求（HTTP {status}）。请核对接口协议和模型 ID。", "MODEL_HTTP")


def _read_response(request: Request, cancelled) -> bytes:
    deadline = time.monotonic() + RESPONSE_DEADLINE
    check_cancel(cancelled)
    try:
        with build_opener(ProxyHandler({}), _NoRedirects()).open(request, timeout=REQUEST_TIMEOUT) as response:
            media_type = response.headers.get_content_type()
            if media_type not in ("application/json", "text/json") and not media_type.endswith("+json"):
                raise ModelTransportError("模型服务未返回可读取的 JSON。请检查接口协议。", "MODEL_RESPONSE")
            result = bytearray()
            while True:
                check_cancel(cancelled)
                if time.monotonic() > deadline:
                    raise ModelTransportError("等待模型回复超时，请稍后再试。", "MODEL_NETWORK")
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - len(result)))
                if not chunk:
                    return bytes(result)
                result.extend(chunk)
                if len(result) > MAX_RESPONSE_BYTES:
                    raise ModelTransportError("模型回复过大，无法读取。", "MODEL_RESPONSE")
    except HTTPError as error:
        status = error.code
        error.close()
        raise _http_error(status) from None
    except (URLError, OSError, HTTPException):
        raise ModelTransportError("无法连接模型服务。请核对服务状态和网络连接。", "MODEL_NETWORK") from None


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def _first(value) -> dict:
    return _object(value[0]) if isinstance(value, list) and value else {}


def _blocks(value, kind: str | None = None) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [block for block in value if isinstance(block, dict) and (kind is None or block.get("type") == kind)]


def _joined_text(parts: list[dict]) -> str:
    return "\n".join(part["text"] for part in parts if isinstance(part.get("text"), str))


def _extract_text(provider: str, data: bytes) -> str:
    invalid = ModelTransportError("模型没有返回完整的文本回复。请核对模型类型后重试。", "MODEL_RESPONSE")
    try:
        response = json.loads(data)
    except (ValueError, RecursionError):
        raise invalid from None
    if not isinstance(response, dict) or response.get("error"):
        raise invalid

    if provider == "openai":
        choice = _first(response.get("choices"))
        if choice.get("finish_reason") not in (None, "stop"):
            raise invalid
        message = _object(choice.get("message"))
        if message.get("refusal") or message.get("tool_calls"):
            raise invalid
        text = message.get("content")
        if isinstance(text, list):
            text = _joined_text(_blocks(text, "text"))
    elif provider == "openai-responses":
        if response.get("status") not in (None, "completed"):
            raise invalid
        text = response.get("output_text")
        if not isinstance(text, str):
            parts = [part for item in _blocks(response.get("output"), "message")
                     if item.get("role") in (None, "assistant") and item.get("status") in (None, "completed")
                     for part in _blocks(item.get("content"), "output_text")]
            text = _joined_text(parts)
    elif provider == "ollama":
        if response.get("done") is False or response.get("done_reason") not in (None, "stop"):
            raise invalid
        text = _object(response.get("message")).get("content")
    elif provider == "anthropic":
        if response.get("stop_reason") not in (None, "end_turn", "stop_sequence"):
            raise invalid
        text = _joined_text(_blocks(response.get("content"), "text"))
    else:
        candidate = _first(response.get("candidates"))
        if candidate.get("finishReason") not in (None, "STOP"):
            raise invalid
        text = _joined_text([part for part in _blocks(_object(candidate.get("content")).get("parts"))
                             if not part.get("thought")])
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_OUTPUT_CHARS:
        raise invalid
    return text.strip()


def complete(profile: dict, api_key: str, system: str, prompt: str, cancelled=None) -> str:
    """Send a single text request and return assistant text without executing tools."""
    if not isinstance(system, str) or not isinstance(prompt, str) or not prompt.strip():
        raise ModelTransportError("模型请求需要有效的文本。", "MODEL_CONFIG")
    if isinstance(profile, dict) and profile.get("provider") == "chatgpt-plan":
        if profile.get("base_url") != "https://api.openai.com/v1":
            raise ModelTransportError("ChatGPT 套餐仅使用 OpenAI 官方地址。", "MODEL_CONFIG")
        from .chatgpt_plan import complete_chatgpt_plan
        return complete_chatgpt_plan(profile.get("model"), api_key, system, prompt, cancelled=cancelled)
    settings = validate_profile(profile)
    request = _make_request(settings, _credential(api_key), system, prompt)
    data = _read_response(request, cancelled)
    check_cancel(cancelled)
    return _extract_text(settings["provider"], data)
