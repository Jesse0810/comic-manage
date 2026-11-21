"""Text inference using an eligible Sign in with ChatGPT plan grant."""
from __future__ import annotations

from collections import defaultdict
from http.client import HTTPException
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .model_transport import (
    MAX_OUTPUT_CHARS, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, ModelTransportError, _NoRedirects,
    responses_options, validate_options,
)
from .sources import check_cancel


RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_TOKEN_CHARS = 16384
_SOCKET_TIMEOUT = 40
_STREAM_DEADLINE = 75
_READ_SIZE = 8192
_MAX_ERROR_BYTES = 16 * 1024
_MODEL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,256}")
_BEARER = re.compile(r"[A-Za-z0-9_.~+/=-]{1,16384}")
_ERROR_CODES = {
    "subscription_sharing_user_not_eligible": ("当前 ChatGPT 账号或工作区暂不符合套餐调用条件。", "MODEL_CHATGPT_INELIGIBLE"),
    "subscription_sharing_usage_limit_exceeded": ("已达到 ChatGPT 套餐或此应用的用量上限。请在 ChatGPT 设置中查看用量。", "MODEL_CHATGPT_USAGE_LIMIT"),
    "subscription_sharing_usage_unavailable": ("暂时无法查询 ChatGPT 套餐用量，请稍后重试。", "MODEL_NETWORK"),
    "subscription_sharing_unsupported_capability": ("此 ChatGPT 模型或套餐不支持当前请求。请检查思考或速度档位，或更换已授权的模型。", "MODEL_NOT_FOUND"),
    "subscription_sharing_route_not_supported": ("ChatGPT 套餐未对当前调用提供权限。请检查应用授权。", "MODEL_CHATGPT_ACCESS"),
    "subscription_sharing_invalid_user": ("ChatGPT 账号授权已无效，请在模型管理中检查账号连接。", "MODEL_AUTH"),
    "chatpass_v2_scope_not_authorized": ("ChatGPT 授权未包含套餐调用权限，请在模型管理中核对授权。", "MODEL_CHATGPT_ACCESS"),
    "chatpass_v2_invalid_authorization_context": ("ChatGPT 套餐调用上下文未通过授权，请在模型管理中核对账号连接。", "MODEL_CHATGPT_ACCESS"),
    "subscription_sharing_user_unavailable": ("暂时无法读取 ChatGPT 账号权限，请稍后重试。", "MODEL_NETWORK"),
    "model_not_found": ("该模型不在当前 ChatGPT 账号的可用范围内，请重新选择。", "MODEL_NOT_FOUND"),
    "invalid_token": ("ChatGPT 账号授权已失效，请重新登录。", "MODEL_AUTH"),
}


def _malformed() -> ModelTransportError:
    return ModelTransportError("ChatGPT 未返回可读取的完整文本。", "MODEL_RESPONSE")


def _to_object(raw: bytes):
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _error_code(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    code = payload.get("code")
    return code if isinstance(code, str) else ""


def _plan_error(code: str, status: int | None = None) -> ModelTransportError:
    if code in _ERROR_CODES:
        return ModelTransportError(*_ERROR_CODES[code])
    if status == 401:
        return ModelTransportError("ChatGPT 账号授权未通过校验。请在模型管理中检查账号连接。", "MODEL_AUTH")
    if status == 403:
        return ModelTransportError("当前 ChatGPT 账号、工作区或地区未获准使用套餐调用。", "MODEL_CHATGPT_ACCESS")
    if status == 404:
        return ModelTransportError("此 ChatGPT 模型不可用。请选择已授权的其他模型。", "MODEL_NOT_FOUND")
    if status == 429:
        return ModelTransportError("ChatGPT 已达到请求频率或用量限制。", "MODEL_RATE_LIMIT")
    if status is not None and status >= 500:
        return ModelTransportError(f"ChatGPT 服务暂不可用（HTTP {status}）。", "MODEL_NETWORK")
    if status is not None:
        return ModelTransportError(f"ChatGPT 未接受本次请求（HTTP {status}）。", "MODEL_HTTP")
    return ModelTransportError("ChatGPT 未完成本次模型请求。", "MODEL_RESPONSE")


def _make_request(model: str, token: str, system: str, prompt: str, options: dict) -> Request:
    if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
        raise ModelTransportError("请选择当前 ChatGPT 账号的有效模型。", "MODEL_CONFIG")
    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or not _BEARER.fullmatch(token):
        raise ModelTransportError("请先在模型管理中登录并授权使用 ChatGPT 套餐。", "MODEL_AUTH")
    if not isinstance(system, str) or not isinstance(prompt, str) or not prompt.strip():
        raise ModelTransportError("模型请求需要有效的文本。", "MODEL_CONFIG")
    try:
        data = json.dumps({"model": model, "instructions": system, "input": [{"role": "user", "content": prompt}],
                           "store": False, "stream": True, **responses_options(options)}, ensure_ascii=False).encode("utf-8")
    except UnicodeError:
        raise ModelTransportError("发送给模型的文本无法编码。", "MODEL_CONFIG") from None
    if len(data) > MAX_REQUEST_BYTES:
        raise ModelTransportError("发送给模型的文本过大。", "MODEL_CONFIG")
    return Request(RESPONSES_URL, data=data, method="POST",
                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                            "Accept": "text/event-stream"})


def _events(response, cancelled, deadline):
    pending = bytearray()
    data = []
    event_name = b""
    total = 0
    while True:
        check_cancel(cancelled)
        if time.monotonic() >= deadline:
            raise ModelTransportError("等待 ChatGPT 回复超时，请稍后重试。", "MODEL_NETWORK")
        chunk = response.read1(min(_READ_SIZE, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            return
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ModelTransportError("ChatGPT 回复过大，无法读取。", "MODEL_RESPONSE")
        pending.extend(chunk)
        while (position := pending.find(b"\n")) >= 0:
            line = bytes(pending[:position]).removesuffix(b"\r")
            del pending[:position + 1]
            if line:
                field, _, value = line.partition(b":")
                value = value.removeprefix(b" ")
                if field == b"data":
                    data.append(value)
                elif field == b"event":
                    event_name = value
                continue
            if data:
                raw = b"\n".join(data)
                if raw == b"[DONE]":
                    return
                event = _to_object(raw)
                if event is None or not isinstance(event.get("type"), str):
                    raise _malformed()
                if event_name and event_name not in (b"message", event["type"].encode("ascii", "replace")):
                    raise _malformed()
                check_cancel(cancelled)
                yield event
                data.clear()
            event_name = b""


def _part_position(event) -> tuple[int, int]:
    output, content = event.get("output_index", 0), event.get("content_index", 0)
    if type(output) is not int or type(content) is not int or min(output, content) < 0:
        raise _malformed()
    return output, content


def _completed_text(response: dict, fragments: dict[tuple[int, int], list[str]]) -> str:
    if not isinstance(response, dict) or response.get("status") != "completed" or response.get("error") is not None:
        raise _malformed()
    joined = ["".join(fragments[key]) for key in sorted(fragments)]
    if "output" not in response:
        return "\n".join(joined).strip()
    output = response["output"]
    if not isinstance(output, list):
        raise _malformed()
    finished = []
    for item in output:
        if not isinstance(item, dict):
            raise _malformed()
        if item.get("type") == "reasoning":
            continue
        if (item.get("type") != "message" or item.get("role") != "assistant"
                or item.get("status") not in (None, "completed") or not isinstance(item.get("content"), list)):
            raise _malformed()
        for part in item["content"]:
            if not isinstance(part, dict) or part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                raise _malformed()
            finished.append(part["text"])
    if joined and joined != finished:
        raise _malformed()
    return "\n".join(finished).strip()


def _stream_text(response, cancelled, deadline) -> str:
    fragments = defaultdict(list)
    finalized = set()
    length = 0
    for event in _events(response, cancelled, deadline):
        kind = event["type"]
        if kind in ("response.output_text.delta", "response.output_text.done"):
            position = _part_position(event)
            done = kind.endswith(".done")
            value = event.get("text" if done else "delta")
            if not isinstance(value, str) or position in finalized:
                raise _malformed()
            if done and fragments.get(position):
                if value != "".join(fragments[position]):
                    raise _malformed()
            else:
                fragments[position].append(value)
                length += len(value)
            if done:
                finalized.add(position)
            if length > MAX_OUTPUT_CHARS:
                raise ModelTransportError("ChatGPT 输出文本过长，无法作为资料候选处理。", "MODEL_RESPONSE")
        elif kind == "response.completed":
            text = _completed_text(event.get("response"), fragments)
            if not text or len(text) > MAX_OUTPUT_CHARS:
                raise _malformed()
            return text
        elif kind == "response.failed":
            failed = event.get("response")
            raise _plan_error(_error_code(failed.get("error") if isinstance(failed, dict) else None))
        elif kind == "error":
            raise _plan_error(_error_code(event.get("error")) or _error_code(event))
        elif kind in ("response.incomplete", "response.cancelled"):
            raise ModelTransportError("ChatGPT 回复未完成，请稍后重试。", "MODEL_RESPONSE")
        elif kind.startswith("response.refusal."):
            raise ModelTransportError("ChatGPT 未能生成此作品的资料候选。", "MODEL_RESPONSE")
    check_cancel(cancelled)
    raise ModelTransportError("ChatGPT 回复在完成前中断，请重试。", "MODEL_RESPONSE")


def complete_chatgpt_plan(model: str, token: str, system: str, prompt: str, cancelled=None, *, options=None) -> str:
    """Return text only after an official ChatGPT plan stream completes."""
    check_cancel(cancelled)
    options = validate_options({} if options is None else options, "chatgpt-plan")
    request = _make_request(model, token, system, prompt, options)
    timeout = options.get("timeout_seconds")
    deadline = time.monotonic() + (timeout or _STREAM_DEADLINE)
    try:
        with build_opener(ProxyHandler({}), _NoRedirects()).open(request, timeout=timeout or _SOCKET_TIMEOUT) as response:
            # The direct ChatGPT route can omit Content-Type even for a valid SSE stream.
            if response.headers.get("Content-Type") and response.headers.get_content_type() != "text/event-stream":
                raise _malformed()
            text = _stream_text(response, cancelled, deadline)
        if token in text:
            raise _malformed()
        check_cancel(cancelled)
        return text
    except HTTPError as error:
        try:
            raw = error.read(_MAX_ERROR_BYTES + 1)
        except (OSError, HTTPException):
            raw = b""
        finally:
            error.close()
        parsed = _to_object(raw) if len(raw) <= _MAX_ERROR_BYTES else None
        raise _plan_error(_error_code((parsed or {}).get("error")), error.code) from None
    except (URLError, OSError, HTTPException):
        raise ModelTransportError("无法连接 ChatGPT 服务，请检查网络连接。", "MODEL_NETWORK") from None
