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
    settings = {"provider": provider, "base_url": _normalize_url(profile.get("base_url"), provider, model), "model": model}
    if "options" in profile:
        settings["options"] = validate_options(profile["options"], provider, model)
    return settings


REASONING_LEVELS = frozenset({"default", "none", "minimal", "low", "medium", "high", "xhigh", "max"})
RESPONSE_SPEED_TIERS = {"standard": "default", "fast": "priority", "ultrafast": "ultrafast"}
_OPTION_BOUNDS = {
    "temperature": (0, 2), "top_p": (0, 1), "top_k": (1, 1024),
    "output_tokens": (1, 16384), "input_tokens": (1, 1_000_000),
    "concurrency": (0, 8), "rpm": (0, 6000), "timeout_seconds": (10, 600),
}
_SAMPLING = {"temperature", "top_p", "top_k"}
_THINKING_BUDGETS = {"low": 1024, "medium": 2048, "high": 3072}
_GEMINI_LEVELS = (
    ("gemini-4-neon", ("low", "medium", "high", "xhigh")),
    ("gemini-4-hydrogen", ("low", "medium", "high", "xhigh")),
    ("gemini-4-carbon", ("low", "medium", "high")),
    ("gemini-4-argon", ("low", "medium", "high")),
    ("gemini-3.8-flash", ("low", "medium", "high")),
    ("gemini-3.6-flash", ("minimal", "low", "medium", "high")),
    ("gemini-3.5-flash-lite", ("minimal", "low", "medium", "high")),
    ("gemini-3.1-flash-lite-image", ("minimal", "high")),
    ("gemini-3.1-flash-lite", ("minimal", "low", "medium", "high")),
    ("gemini-3.1-pro", ("low", "medium", "high")),
    ("gemini-3-flash", ("minimal", "low", "medium", "high")),
    ("gemini-3-pro", ("low", "high")),
    ("gemini-robotics-er-2", ("minimal", "low", "medium", "high")),
)


def _option_error(message: str) -> ModelTransportError:
    return ModelTransportError(message, "MODEL_CONFIG")


def validate_options(options: dict, provider: str, model: str = "") -> dict:
    """Validate optional profile settings without replacing explicit provider defaults."""
    if not isinstance(options, dict) or set(options) - {*_OPTION_BOUNDS, "reasoning", "speed"}:
        raise _option_error("模型参数包含无法识别的字段。")
    reasoning, speed = options.get("reasoning", "default"), options.get("speed", "default")
    if not isinstance(reasoning, str) or reasoning not in REASONING_LEVELS:
        raise _option_error("请选择有效的思考等级。")
    if not isinstance(speed, str) or speed not in {"default", *RESPONSE_SPEED_TIERS}:
        raise _option_error("请选择有效的速度等级。")
    if speed != "default" and provider not in {"chatgpt-plan", "openai-responses"}:
        raise _option_error("此接口不支持速度等级。请使用默认档位。")
    if provider == "chatgpt-plan" and options.keys() & (_SAMPLING | {"output_tokens"}):
        raise _option_error("ChatGPT 账号授权接口不支持自定义采样参数或输出 Token 上限。")
    if "top_k" in options and provider not in {"ollama", "anthropic", "gemini"}:
        raise _option_error("此接口不支持 Top K。")
    for name, (minimum, maximum) in _OPTION_BOUNDS.items():
        if name not in options:
            continue
        value = options[name]
        if name == "temperature" and provider == "anthropic":
            maximum = 1
        expected = (int, float) if name in {"temperature", "top_p"} else (int,)
        if type(value) not in expected or not minimum <= value <= maximum:
            raise _option_error(f"{name} 必须在 {minimum} 至 {maximum} 之间。")
    if model and reasoning != "default":
        if provider == "ollama":
            _ollama_thinking(model, reasoning)
        elif provider == "gemini":
            _gemini_thinking(model, reasoning)
        elif provider == "anthropic":
            thinking, _ = _anthropic_thinking(model, reasoning, options.get("output_tokens", MAX_OUTPUT_TOKENS))
            if thinking["type"] not in {"disabled", "between_tools"} and options.keys() & _SAMPLING:
                raise _option_error("Claude 开启思考时请清除自定义采样参数。")
    return dict(options)


def _ollama_thinking(model: str, effort: str) -> str | bool:
    model_name = model.casefold().rsplit("/", 1)[-1]
    if model_name == "gpt-oss" or model_name.startswith("gpt-oss:"):
        if effort in {"low", "medium", "high"}:
            return effort
    elif effort in {"none", "low"}:
        return effort == "low"
    raise _option_error("此 Ollama 模型不支持所选思考等级。")


def _gemini_thinking(model: str, effort: str) -> dict:
    model_name = model.casefold().removeprefix("models/")
    if model_name.startswith(("gemini-2.5-pro", "gemini-2.5-flash")):
        if effort in {"low", "medium", "high"} or (effort == "none" and model_name.startswith("gemini-2.5-flash")):
            return {"thinkingBudget": {"none": 0, "low": 1024, "medium": 4096, "high": 8192}[effort]}
    else:
        for prefix, allowed in _GEMINI_LEVELS:
            if model_name == prefix or model_name.startswith(prefix + "-"):
                if effort in allowed:
                    return {"thinkingLevel": effort.upper()}
                break
    raise _option_error("此 Gemini 模型不支持所选思考等级。")


def _anthropic_thinking(model: str, effort: str, output_tokens: int) -> tuple[dict, dict | None]:
    name = model.casefold()
    if name == "claude-mythos-preview":
        adaptive, named_levels, cannot_disable = True, {"low", "medium", "high", "max"}, True
    else:
        family = re.match(r"^claude-(opus|sonnet|haiku|fable|mythos)-(4|5)(?:-([0-9])(?=-|$))?(?:-|$)", name)
        if not family and not name.startswith("claude-3-7-sonnet"):
            raise _option_error("此 Claude 模型没有可配置的思考等级。")
        kind, major, minor = family.groups() if family else ("sonnet", "3", "7")
        adaptive = major == "5" or (kind in {"opus", "sonnet"} and major == "4" and int(minor or "0") >= 6)
        named_levels = {"low", "medium", "high", "max"}
        if major == "5" or (kind == "opus" and major == "4" and int(minor or "0") >= 7):
            named_levels.add("xhigh")
        cannot_disable = kind in {"fable", "mythos"}
    if effort == "none":
        if cannot_disable:
            raise _option_error("此 Claude 模型的思考功能不能关闭。")
        return {"type": "between_tools" if name.startswith("claude-sonnet-5-5") else "disabled"}, None
    if adaptive and effort in named_levels:
        return {"type": "adaptive"}, {"effort": effort}
    if not adaptive and effort in _THINKING_BUDGETS:
        budget = _THINKING_BUDGETS[effort]
        if output_tokens <= budget:
            raise _option_error(f"使用该 Claude 思考等级时，输出 Token 上限须大于 {budget}。")
        return {"type": "enabled", "budget_tokens": budget}, None
    raise _option_error("此 Claude 模型不支持所选思考等级。")


def responses_options(options: dict) -> dict:
    """Map shared options for the OpenAI Responses and ChatGPT plan transports."""
    result = {}
    if options.get("reasoning", "default") != "default":
        result["reasoning"] = {"effort": options["reasoning"]}
    if options.get("speed", "default") != "default":
        result["service_tier"] = RESPONSE_SPEED_TIERS[options["speed"]]
    return result


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

    options = profile.get("options", {})
    output_tokens = options.get("output_tokens", MAX_OUTPUT_TOKENS)
    effort = options.get("reasoning", "default")
    sampling = {key: options[key] for key in _SAMPLING if key in options}
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    suffix = _ENDPOINTS.get(provider)
    if provider == "openai":
        token_field = "max_completion_tokens" if host == "api.openai.com" else "max_tokens"
        body = {"model": model, "messages": messages, "stream": False, token_field: output_tokens, **sampling}
        if effort != "default":
            body["reasoning_effort"] = effort
    elif provider == "openai-responses":
        body = {"model": model, "instructions": system, "input": prompt, "stream": False,
                "store": False, "max_output_tokens": output_tokens, **sampling, **responses_options(options)}
    elif provider == "ollama":
        body = {"model": model, "messages": messages, "stream": False, "options": {"num_predict": output_tokens, **sampling}}
        if effort != "default":
            body["think"] = _ollama_thinking(model, effort)
    elif provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        body = {"model": model, "system": system, "messages": [messages[1]], "max_tokens": output_tokens, **sampling}
        if effort != "default":
            thinking, output_config = _anthropic_thinking(model, effort, output_tokens)
            body["thinking"] = thinking
            if output_config:
                body["output_config"] = output_config
    else:
        suffix = f"/models/{quote(model, safe='')}:generateContent"
        config = {"maxOutputTokens": output_tokens}
        for source, target in (("temperature", "temperature"), ("top_p", "topP"), ("top_k", "topK")):
            if source in sampling:
                config[target] = sampling[source]
        if effort != "default":
            config["thinkingConfig"] = _gemini_thinking(model, effort)
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": config}

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


def _read_response(request: Request, cancelled, timeout: int | None = None) -> bytes:
    deadline = time.monotonic() + (timeout or RESPONSE_DEADLINE)
    check_cancel(cancelled)
    try:
        with build_opener(ProxyHandler({}), _NoRedirects()).open(request, timeout=timeout or REQUEST_TIMEOUT) as response:
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
    from .model_scheduler import check_input_budget, wait_for_model_slot

    if isinstance(profile, dict) and profile.get("provider") == "chatgpt-plan":
        if profile.get("base_url") != "https://api.openai.com/v1":
            raise ModelTransportError("ChatGPT 套餐仅使用 OpenAI 官方地址。", "MODEL_CONFIG")
        from .chatgpt_plan import complete_chatgpt_plan
        options = validate_options(profile.get("options", {}), "chatgpt-plan")
        settings = {**profile, "options": options}
        check_input_budget(settings, system, prompt)
        wait_for_model_slot(settings, cancelled)
        return complete_chatgpt_plan(profile.get("model"), api_key, system, prompt,
                                     cancelled=cancelled, options=options)
    settings = validate_profile(profile)
    request = _make_request(settings, _credential(api_key), system, prompt)
    check_input_budget(settings, system, prompt)
    wait_for_model_slot(settings, cancelled)
    data = _read_response(request, cancelled, settings.get("options", {}).get("timeout_seconds"))
    check_cancel(cancelled)
    return _extract_text(settings["provider"], data)
