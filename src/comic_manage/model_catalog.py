"""Read available text model IDs from the selected provider."""
import json
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

from .model_transport import ModelTransportError, _credential, _read_response, validate_profile
from .sources import check_cancel

_MAX_MODELS = 500


def _text(value, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return ""
    return value.strip()


def discover_models(profile, api_key: str, cancelled=None) -> dict:
    candidate = {key: profile.get(key) for key in ("provider", "base_url", "model")}
    settings = validate_profile(candidate)
    provider = settings["provider"]
    base_url = settings["base_url"]
    api_key = _credential(api_key)
    host = urlsplit(base_url).hostname
    if not api_key and host in {"api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com"}:
        raise ModelTransportError("此模型列表需要 API Key。请先填写模型密钥。", "MODEL_AUTH")
    headers = {"Accept": "application/json"}
    query = {}
    if provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if api_key:
            headers["x-api-key"] = api_key
        query["limit"] = 100
    elif provider == "gemini":
        if api_key:
            headers["x-goog-api-key"] = api_key
        query["pageSize"] = 100
    elif api_key:
        headers["Authorization"] = "Bearer " + api_key
    url = base_url + ("/api/tags" if provider == "ollama" else "/models")
    models, seen_cursors = {}, set()
    for _ in range(5):
        check_cancel(cancelled)
        request = Request(url + ("?" + urlencode(query) if query else ""), headers=headers)
        try:
            reply = json.loads(_read_response(request, cancelled))
        except (ValueError, RecursionError) as error:
            if isinstance(error, ModelTransportError):
                raise
            raise ModelTransportError("服务返回了无法读取的模型列表。", "MODEL_RESPONSE") from None
        entries = reply.get("models" if provider in {"ollama", "gemini"} else "data") if isinstance(reply, dict) else None
        if not isinstance(entries, list):
            raise ModelTransportError("服务没有返回可读取的模型列表。", "MODEL_RESPONSE")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if provider == "gemini" and isinstance(entry.get("supportedGenerationMethods"), list) and "generateContent" not in entry["supportedGenerationMethods"]:
                continue
            model = _text(entry.get("id") if provider not in {"ollama", "gemini"} else (entry.get("model") or entry.get("name")), 256)
            if provider == "gemini":
                model = model.removeprefix("models/")
            name = _text(entry.get("display_name") or entry.get("displayName"), 256) or model
            if model and (not api_key or (api_key not in model and api_key not in name)):
                models.setdefault(model, {"slug": model, "name": name})
            if len(models) >= _MAX_MODELS:
                return {"models": list(models.values())}
        if provider == "gemini":
            cursor_name, cursor = "pageToken", _text(reply.get("nextPageToken"), 1024)
        elif provider == "anthropic" and reply.get("has_more") is True:
            cursor_name, cursor = "after_id", _text(reply.get("last_id"), 256)
        else:
            break
        if not cursor or cursor in seen_cursors or (api_key and api_key in cursor):
            break
        seen_cursors.add(cursor)
        query[cursor_name] = cursor
    return {"models": list(models.values())}
