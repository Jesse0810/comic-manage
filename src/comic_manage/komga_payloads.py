"""Komga native metadata patches shared by file and remote working-copy sources."""
from __future__ import annotations

from collections import Counter
import math
from urllib.parse import urlsplit

from .komga import MANAGED_ENTITY_KINDS, komga_book_payload
from .metadata import Metadata, split_values, title_key
from .komga import komga_metadata

SUPPORTED_ROLES = frozenset({"writer", "penciller", "translator"})
AGE_VALUES = {"Everyone": 0, "MA15+": 15, "R18+": 18}
def _records(value, fields: tuple[str, ...]) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) or any(not isinstance(item.get(field), str) or not item[field].strip() for field in fields) for item in value):
        raise ValueError("远端资料列表格式无效，本次已跳过以保留现有值。")
    return value


def _locked(payload: dict, field: str, value) -> None:
    payload[field] = value
    payload[field + "Lock"] = True


def book_payload(metadata: Metadata, current: dict) -> tuple[dict, list[str]]:
    """Replace locally editable values while retaining unsupported author roles and entities."""
    existing = current.get("metadata") or {}
    if not isinstance(existing, dict):
        raise ValueError("远端漫画资料格式无效。")
    managed = {title_key(kind) for kind in MANAGED_ENTITY_KINDS} | {"character"}
    entities = [{"kind": item["kind"], "name": item["name"]} for item in _records(existing.get("notableEntities"), ("kind", "name")) if title_key(item["kind"]) not in managed]
    payload = komga_book_payload(metadata)
    payload["notableEntities"] = entities + payload["notableEntities"]
    payload["notableEntities"].extend({"kind": "character", "name": name} for name in metadata.characters)
    authors = [{"name": item["name"], "role": item["role"]} for item in _records(existing.get("authors"), ("name", "role")) if item["role"].strip().casefold() not in SUPPORTED_ROLES]
    authors.extend({"name": name, "role": "writer"} for name in metadata.authors)
    authors.extend({"name": name, "role": "penciller"} for name in metadata.pencillers)
    authors.extend({"name": name, "role": "translator"} for name in split_values(metadata.translator))
    labels = {item["url"]: item["label"] for item in _records(existing.get("links"), ("label", "url"))}
    links = [{"label": labels.get(url) or urlsplit(url).netloc, "url": url} for url in metadata.web]
    for field, value in (("title", metadata.display_title), ("summary", metadata.summary),
                         ("authors", authors), ("tags", metadata.all_tags), ("links", links)):
        _locked(payload, field, value)
    warnings = []
    if metadata.collection_mode == "series" and metadata.number.strip():
        _locked(payload, "number", metadata.number.strip())
        try:
            number = float(metadata.number)
        except ValueError:
            number = math.nan
        if math.isfinite(number) and abs(number) <= 3.4028235e38:
            _locked(payload, "numberSort", number)
        else:
            warnings.append("册号已同步，非数值或超出范围的册号无法直接重置 Komga 排序值。")
    elif metadata.collection_mode == "series":
        warnings.append("本机未填写 Number，Komga API 不接受空册号，已保留远端编号与排序值。")
    if metadata.year and metadata.month and metadata.day:
        _locked(payload, "releaseDate", f"{int(metadata.year):04d}-{int(metadata.month):02d}-{int(metadata.day):02d}")
    elif not any((metadata.year, metadata.month, metadata.day)):
        _locked(payload, "releaseDate", None)
    else:
        warnings.append("Komga API 只接受完整日期，已保留本机部分日期并跳过远端发行日更新。")
    return payload, warnings


def _majority(values: list[str], label: str) -> tuple[str | None, str | None]:
    counts = Counter(value for value in values if value)
    if not counts:
        return None, None
    highest = max(counts.values())
    winners = [value for value, count in counts.items() if count == highest]
    if len(winners) != 1:
        return None, f"系列的{label}出现平票，本次保留远端该字段，其余确定值继续同步。"
    return winners[0], None


def series_payload(metadata: list[Metadata], current: dict) -> tuple[dict, list[str]]:
    modes = {item.collection_mode for item in metadata}
    if len(modes) != 1 or (modes == {"oneshot"}) != bool(current.get("oneshot")):
        raise ValueError("本机作品方式与 Komga 目录组织不同，请整理文件路径并重新扫描后再同步系列。")
    oneshot = modes == {"oneshot"}
    if oneshot and len(metadata) != 1:
        raise ValueError("Komga One-Shot 系列包含多本记录，请核对书库目录设置。")
    if not oneshot and len({title_key(item.series) for item in metadata}) != 1:
        raise ValueError("所选记录属于不同的本机真实系列，API 无法移动或拆分 Komga 系列。")
    payload = {}
    warnings = []
    title = metadata[0].display_title if oneshot else metadata[0].series.strip()
    _locked(payload, "title", title)
    if oneshot:
        _locked(payload, "summary", metadata[0].summary)
    _locked(payload, "ageRating", max(AGE_VALUES[item.age_rating] for item in metadata))
    for field, values, label in (
        ("publisher", [item.publisher.strip() for item in metadata], "出版方"),
        ("language", [item.language.strip() for item in metadata], "语言"),
        ("readingDirection", [item.reading_direction for item in metadata], "阅读方向"),
    ):
        value, warning = _majority(values, label)
        if warning:
            warnings.append(warning)
            continue
        _locked(payload, field, value if field == "readingDirection" else value or "")
    genres = list(dict.fromkeys(genre for item in metadata for genre in split_values(item.genre)))
    _locked(payload, "genres", genres)
    known_counts = [int(item.count) for item in metadata if item.count]
    _locked(payload, "totalBookCount", 1 if oneshot else max(known_counts, default=None))
    if not oneshot:
        aliases = {}
        conflicting_aliases = False
        for item in metadata:
            label, alias = item.localized_series_label.strip(), item.localized_series.strip()
            key = label.casefold()
            if key in aliases and aliases[key]["title"] != alias:
                conflicting_aliases = True
                break
            aliases[key] = {"label": label, "title": alias}
        if conflicting_aliases:
            warnings.append("同一系列的同标签别名不一致，本次保留远端别名，其余确定值继续同步。")
        else:
            existing = current.get("metadata") or {}
            if not isinstance(existing, dict):
                raise ValueError("远端系列资料格式无效。")
            titles = [{"label": item["label"], "title": item["title"]} for item in _records(existing.get("alternateTitles"), ("label", "title")) if item["label"].casefold() not in aliases]
            titles.extend(item for item in aliases.values() if item["title"])
            _locked(payload, "alternateTitles", titles)
    if len(set(known_counts)) > 1:
        warnings.append("系列总册数采用各册已知 Count 的最大值。")
    return payload, warnings


ENTITY_FIELDS = {
    "original_works": "原作", "characters": "character", "release_type": "发行性质",
    "publication_format": "出版形态", "uncensored": "修正状态", "age_rating": "年龄分级",
    "genre": "内容类型", "color": "色彩", "release_methods": "发布方式", "release_platforms": "发布平台",
}
TAG_FIELDS = {"原作": "original_works", "角色": "characters", "发行性质": "release_type",
              "出版形态": "publication_format", "修正状态": "uncensored", "内容类型": "genre",
              "年龄分级": "age_rating", "年龄等级": "age_rating", "汉化状态": "translator",
              "发布方式": "release_methods", "发布平台": "release_platforms", "色彩": "color"}


def _tag_field(tag: str) -> str | None:
    prefix, separator, _ = tag.replace("：", ":", 1).partition(":")
    if separator and prefix.strip() in TAG_FIELDS:
        return TAG_FIELDS[prefix.strip()]
    if tag in ("汉化", "生肉"):
        return "translator"
    if tag in ("全彩", "黑白", "部分彩色"):
        return "color"
    if tag in ("无修正", "有修正"):
        return "uncensored"
    if tag in ("全年龄", "R15", "R18", "R18+", "MA15+", "Everyone"):
        return "age_rating"
    return None


def remote_book_payload(metadata: Metadata, current: dict, fields: set[str]) -> tuple[dict, list[str]]:
    """Patch only explicit changes to a remote working copy, preserving other representations."""
    existing = current.get("metadata") or {}
    payload, warnings = {}, []
    for field in ("title", "summary"):
        if field in fields:
            _locked(payload, field, metadata.display_title if field == "title" else metadata.summary)
    roles = {"authors": ("writer", metadata.authors), "pencillers": ("penciller", metadata.pencillers),
             "translator": ("translator", split_values(metadata.translator))}
    changed_roles = {roles[field][0] for field in fields & roles.keys()}
    if changed_roles:
        authors = [{"name": item["name"], "role": item["role"]} for item in _records(existing.get("authors"), ("name", "role"))
                   if item["role"].strip().casefold() not in changed_roles]
        for role, names in roles.values():
            if role in changed_roles:
                authors.extend({"name": name, "role": role} for name in names)
        _locked(payload, "authors", authors)
    if "web" in fields:
        old_links = _records(existing.get("links"), ("label", "url"))
        links = []
        for url in metadata.web:
            matching = [dict(item) for item in old_links if item["url"] == url]
            links.extend(matching or [{"label": urlsplit(url).netloc, "url": url}])
        _locked(payload, "links", links)
    if "number" in fields and metadata.collection_mode == "series":
        _locked(payload, "number", metadata.number)
        try:
            number = float(metadata.number)
        except ValueError:
            number = math.nan
        if math.isfinite(number) and abs(number) <= 3.4028235e38:
            _locked(payload, "numberSort", number)
        else:
            warnings.append("册号已同步，非数值或超出范围的册号保留远端原排序值。")
    if fields & {"year", "month", "day"}:
        if metadata.year and metadata.month and metadata.day:
            _locked(payload, "releaseDate", f"{int(metadata.year):04d}-{int(metadata.month):02d}-{int(metadata.day):02d}")
        elif not any((metadata.year, metadata.month, metadata.day)):
            _locked(payload, "releaseDate", None)
        else:
            warnings.append("Komga API 只接受完整日期，已保留本机部分日期并跳过远端发行日更新。")
    changed_kinds = {ENTITY_FIELDS[field] for field in fields & ENTITY_FIELDS.keys()}
    effective_tag_fields = fields | ({"uncensored"} if "age_rating" in fields else set())
    if "age_rating" in fields:
        changed_kinds.add("修正状态")
    if changed_kinds:
        kinds = {title_key(kind) for kind in changed_kinds}
        entities = [{"kind": item["kind"], "name": item["name"]} for item in _records(existing.get("notableEntities"), ("kind", "name"))
                    if title_key(item["kind"]) not in kinds]
        entities.extend(item for item in komga_metadata(metadata)["notableEntities"] if item["kind"] in changed_kinds)
        if "character" in changed_kinds:
            entities.extend({"kind": "character", "name": name} for name in metadata.characters)
        _locked(payload, "notableEntities", entities)
    tag_owners = set(TAG_FIELDS.values()) | {"color", "age_rating"}
    if effective_tag_fields & (tag_owners | {"tags"}):
        existing_tags = existing.get("tags") or []
        if not isinstance(existing_tags, list) or any(not isinstance(tag, str) for tag in existing_tags):
            raise ValueError("远端标签格式无效，本次已保留原值。")
        tags = [tag for tag in existing_tags if ((_tag_field(tag) is None and "tags" not in effective_tag_fields)
                or (_tag_field(tag) is not None and _tag_field(tag) not in effective_tag_fields))]
        if "tags" in fields:
            tags.extend(metadata.tags)
        tags.extend(tag for tag in metadata.all_tags if _tag_field(tag) in effective_tag_fields)
        _locked(payload, "tags", list(dict.fromkeys(tags)))
    return payload, warnings


def remote_series_payload(records: list[tuple[Metadata, dict]], current: dict) -> tuple[dict, list[str]]:
    """Shared Series values use explicit edits, never a vote among untouched snapshot copies."""
    payload, warnings = {}, []
    modes = {metadata.collection_mode for metadata, _ in records}
    if len(modes) != 1 or (modes == {"oneshot"}) != bool(current.get("oneshot")):
        raise ValueError("远端目录组织已变化，请重新读取并处理冲突。")
    oneshot = modes == {"oneshot"}
    conversions = {
        "series": ("title", lambda item: item.series),
        "publisher": ("publisher", lambda item: item.publisher),
        "language": ("language", lambda item: item.language),
        "reading_direction": ("readingDirection", lambda item: item.reading_direction or None),
        "count": ("totalBookCount", lambda item: int(item.count) if item.count else None),
    }
    for field, (target, convert) in conversions.items():
        if oneshot and field in ("series", "count"):
            continue
        values = [convert(metadata) for metadata, source in records if field in source["dirty_fields"]]
        if not values:
            continue
        if any(value != values[0] for value in values):
            warnings.append(f"共享字段 {target} 的本机编辑不一致，保留远端该字段。")
        else:
            _locked(payload, target, values[0])
    if oneshot:
        metadata, source = records[0]
        for field in ("title", "summary"):
            if field in source["dirty_fields"]:
                _locked(payload, field, getattr(metadata, field))
    if any("age_rating" in source["dirty_fields"] for _, source in records):
        if all(metadata.age_rating in AGE_VALUES for metadata, _ in records):
            _locked(payload, "ageRating", max(AGE_VALUES[metadata.age_rating] for metadata, _ in records))
        else:
            warnings.append("部分册尚无逐册年龄分级，已保留原系列分级，仅同步本次明确修改的逐册实体。")
    if any("genre" in source["dirty_fields"] for _, source in records):
        if all(metadata.genre or "genre" in source["dirty_fields"] for metadata, source in records):
            _locked(payload, "genres", list(dict.fromkeys(genre for metadata, _ in records for genre in split_values(metadata.genre))))
        else:
            warnings.append("部分册尚无逐册内容类型，已保留原系列类型，仅同步本次明确修改的逐册实体。")
    alias_records = [(metadata, source) for metadata, source in records
                     if set(source["dirty_fields"]) & {"localized_series", "localized_series_label"}]
    if alias_records and not oneshot:
        desired = {}
        conflict = False
        for metadata, _ in alias_records:
            label = metadata.localized_series_label.strip()
            alias = metadata.localized_series.strip()
            if label.casefold() in desired and desired[label.casefold()]["title"] != alias:
                conflict = True
            desired[label.casefold()] = {"label": label, "title": alias}
        if conflict:
            warnings.append("同标签系列别名的本机编辑不一致，已保留远端别名。")
        else:
            old = (current.get("metadata") or {}).get("alternateTitles")
            titles = [{"label": item["label"], "title": item["title"]} for item in _records(old, ("label", "title")) if item["label"].casefold() not in desired]
            titles.extend(item for item in desired.values() if item["title"])
            _locked(payload, "alternateTitles", titles)
    return payload, warnings


