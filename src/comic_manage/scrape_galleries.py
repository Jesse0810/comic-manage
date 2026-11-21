"""Metadata-only adapters for exact E-Hentai and nhentai gallery links.

Neither adapter follows image, thumbnail, comment, CDN or uploader URLs.
Returned fields are review candidates, not a complete Metadata object.

Endpoint documentation: https://ehwiki.org/wiki/API and
https://nhentai.net/api/v2/openapi.json (checked 2026-11-18).
"""
from __future__ import annotations

import re
from urllib.parse import SplitResult

from .filename_parser import parse_filename
from .scrape_http import ScrapeError, read_json


_EH_PATH = re.compile(r"/g/([1-9][0-9]{0,11})/([a-fA-F0-9]{10})/?")
_NH_PATH = re.compile(r"/g/([1-9][0-9]{0,11})/?")
_MAX_TAGS = 1024
_MAX_NAME = 1024
_MAX_TITLE = 4096
_EVENT = re.compile(r"^\s*\((?:C[0-9]{1,3}|COMIC1(?:\*|☆)?[0-9]{1,3})\)\s*", re.I)
_LANGUAGES = {
    "japanese": "ja", "english": "en", "chinese": "zh", "korean": "ko",
    "french": "fr", "german": "de", "spanish": "es", "italian": "it",
    "portuguese": "pt", "russian": "ru", "vietnamese": "vi", "thai": "th",
    "indonesian": "id", "polish": "pl", "arabic": "ar", "turkish": "tr",
}
_CATEGORY_FIELDS = {
    "doujinshi": {"genre": "漫画", "release_type": "同人发行"},
    "manga": {"genre": "漫画", "release_type": "商业出版"},
    "gamecg": {"genre": "CG集"},
}
_TITLE_LABELS = frozenset(_LANGUAGES) | {
    "digital", "dl版", "dl", "電子版", "电子版", "掃圖", "扫图", "scan", "raw", "生肉",
    "ongoing", "complete", "completed", "進行中", "进行中", "完結", "完结",
}


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _unique(values: list[str]) -> list[str]:
    result, seen = [], set()
    for value in values:
        value = value.strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _name(value: str) -> str:
    value = value.strip()
    if len(value) > _MAX_NAME:
        raise ScrapeError("来源标签过长，无法作为元数据读取。", code="SCRAPE_RESPONSE")
    if len(value) > 2 and (value[0], value[-1]) in (("[", "]"), ("［", "］")):
        value = value[1:-1].strip()
    return value


def _same_id(value, identity: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, str)) and str(value) == str(identity)


def _clean_title(raw: str, warnings: list[str]) -> str:
    without_event = _EVENT.sub("", raw)
    parsed, _ = parse_filename(without_event)
    cleaned = parsed.title.strip() or raw
    if any(tag.casefold() not in _TITLE_LABELS for tag in parsed.tags):
        # An unknown trailing [note] can be part of the actual work's name.
        # Keep the source spelling rather than silently deleting that text.
        warnings.append("标题附加标记含义不明确，候选保留完整来源标题，请人工核对。")
        return without_event or raw
    if cleaned != raw:
        warnings.append("来源标题中的作者、活动或版本标记已与作品名称分开，请核对提取后的标题。")
    return cleaned


def _map_fields(raw_title: str, namespaced: list[tuple[str, str]], categories: list[str], warnings: list[str]) -> dict:
    if not raw_title:
        raise ScrapeError("站点未返回有效的作品标题，请确认详情链接。")
    if len(raw_title) > _MAX_TITLE or len(namespaced) > _MAX_TAGS:
        raise ScrapeError("来源标题或标签数量异常，未读取此资料。", code="SCRAPE_RESPONSE")
    fields: dict = {"title": _clean_title(raw_title, warnings)}
    by_type: dict[str, list[str]] = {}
    for namespace, value in namespaced:
        namespace, value = namespace.strip().casefold(), _name(value)
        if namespace and value:
            by_type.setdefault(namespace, []).append(value)
    for key in by_type:
        by_type[key] = _unique(by_type[key])

    for namespace, field_name in (("artist", "authors"), ("character", "characters")):
        if by_type.get(namespace):
            fields[field_name] = by_type[namespace]
    groups = by_type.get("group", [])
    if groups:
        fields["publisher"] = ", ".join(groups)
        if len(groups) > 1:
            warnings.append("来源包含多个社团，已保留全部名称，请核对社团／出版社。")

    parodies = by_type.get("parody", [])
    original_marker = any(value.casefold() == "original" for value in parodies)
    original_works = [value for value in parodies if value.casefold() != "original"]
    if len(original_works) == 1 and not original_marker:
        fields["original_works"] = original_works
    elif len(original_works) > 1 or original_works and original_marker:
        warnings.append("来源有多个或相互冲突的原作标记，未自动选择原作，请人工核对。")

    languages = _unique([_LANGUAGES[value.casefold()] for value in by_type.get("language", []) if value.casefold() in _LANGUAGES])
    if len(languages) == 1:
        fields["language"] = languages[0]
        if languages[0] == "zh":
            warnings.append("来源只标注中文，未区分简繁；语言候选使用 zh，请按实际正文核对。")
    elif len(languages) > 1:
        warnings.append("来源标注多种正文语言，未自动选择语言。")

    categories = _unique([_name(value) for value in categories] + by_type.get("category", []))
    if len(categories) == 1:
        category_key = re.sub(r"[\s_-]+", "", categories[0]).casefold()
        if category_key in _CATEGORY_FIELDS:
            fields.update(_CATEGORY_FIELDS[category_key])
        else:
            warnings.append(f"来源分类“{categories[0]}”不能唯一对应当前内容类型，未自动填写内容类型或发行性质。")
    elif len(categories) > 1:
        warnings.append("来源标注多个分类，未自动填写内容类型或发行性质。")

    # Keep source namespaces; they are not ComicManage's business namespaces.
    # Identity tags already have dedicated fields, except ambiguous parodies.
    tags = []
    for namespace, values in by_type.items():
        if namespace in ("artist", "character", "group", "category"):
            continue
        if namespace == "parody" and (not original_works or "original_works" in fields):
            continue
        if namespace == "language":
            values = [value for value in values if value.casefold() not in _LANGUAGES]
        tags.extend(f"{namespace}:{value}" for value in values)
    tags.extend(f"category:{value}" for value in categories if "genre" not in fields)
    if tags:
        fields["tags"] = _unique(tags)
    return fields


def _ehentai(url: SplitResult, request) -> dict:
    match = _EH_PATH.fullmatch(url.path)
    if not match:
        raise ScrapeError("请输入 E-Hentai 画廊详情链接，格式为 /g/编号/令牌/。", code="SCRAPE_URL")
    identity, token = int(match[1]), match[2].lower()
    data = read_json(request("https://api.e-hentai.org/api.php", body={
        "method": "gdata", "gidlist": [[identity, token]], "namespace": 1,
    }))
    items = data.get("gmetadata")
    item = next((item for item in items if isinstance(item, dict) and _same_id(item.get("gid"), identity)), None) if isinstance(items, list) else None
    if not item or item.get("error"):
        raise ScrapeError("E-Hentai 未返回该画廊资料，请确认链接令牌及访问权限。")
    raw_tags = item.get("tags", [])
    if not isinstance(raw_tags, list) or len(raw_tags) > _MAX_TAGS:
        raise ScrapeError("E-Hentai 返回的标签格式无法识别。")
    tags = []
    for raw in raw_tags:
        if not isinstance(raw, str):
            continue
        namespace, separator, value = raw.partition(":")
        tags.append((namespace, value) if separator else ("tag", raw))
    warnings = []
    raw_title = _text(item.get("title_jpn")) or _text(item.get("title"))
    fields = _map_fields(raw_title, tags, [_text(item.get("category"))], warnings)
    if item.get("posted") is not None:
        warnings.append("站点发布时间是画廊上传时间，未填入作品发售日期。")
    if item.get("expunged") is True:
        warnings.append("站点标记该画廊已被移除，请核对来源是否仍适用于当前作品。")
    return {"provider": "ehentai", "sourceUrl": f"https://e-hentai.org/g/{identity}/{token}/", "fields": fields, "warnings": _unique(warnings)}


def _nhentai(url: SplitResult, credentials: dict[str, str], request) -> dict:
    match = _NH_PATH.fullmatch(url.path)
    if not match:
        raise ScrapeError("请输入 nhentai 画廊详情链接，格式为 /g/编号/。", code="SCRAPE_URL")
    identity = int(match[1])
    headers = {}
    api_key = _text(credentials.get("apiKey"))
    if "\r" in api_key or "\n" in api_key:
        raise ScrapeError("nhentai API Key 格式无效，请重新填写。", code="SCRAPE_AUTH")
    if api_key:
        headers["Authorization"] = "Key " + api_key
    data = read_json(request(f"https://nhentai.net/api/v2/galleries/{identity}", headers=headers))
    if not _same_id(data.get("id"), identity):
        raise ScrapeError("nhentai 未返回对应画廊资料，请确认详情链接。")
    title = data.get("title")
    raw_tags = data.get("tags")
    if not isinstance(title, dict) or not isinstance(raw_tags, list) or len(raw_tags) > _MAX_TAGS:
        raise ScrapeError("nhentai 返回的元数据格式无法识别，请稍后重试。")
    tags = [(_text(item.get("type")), _text(item.get("name"))) for item in raw_tags if isinstance(item, dict)]
    warnings = []
    raw_title = _text(title.get("japanese")) or _text(title.get("pretty")) or _text(title.get("english"))
    fields = _map_fields(raw_title, tags, [], warnings)
    if data.get("upload_date") is not None:
        warnings.append("站点发布时间是画廊上传时间，未填入作品发售日期。")
    return {"provider": "nhentai", "sourceUrl": f"https://nhentai.net/g/{identity}/", "fields": fields, "warnings": _unique(warnings)}


def fetch_gallery(provider: str, url: SplitResult, credentials: dict[str, str], request) -> dict:
    """Fetch one canonical gallery via its fixed JSON API; no image requests."""
    if provider == "ehentai":
        return _ehentai(url, request)
    if provider == "nhentai":
        return _nhentai(url, credentials, request)
    raise ScrapeError("此来源不是受支持的画廊站点。", code="SCRAPE_URL")
