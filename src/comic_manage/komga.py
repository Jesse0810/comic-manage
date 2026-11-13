"""Portable Komga supplementary metadata, without network access."""
from __future__ import annotations

from .metadata import Metadata, split_values, title_key


MANAGED_ENTITY_KINDS = frozenset({"原作", "发行性质", "出版形态", "发布方式", "发布平台", "作品组", "文字版本", "修正状态", "年龄分级", "色彩", "内容类型"})


def komga_metadata(metadata: Metadata) -> dict:
    """Describe the supplementary book entities and the real series' alternate title."""
    value = Metadata.from_dict(metadata.to_dict())
    entities = [{"kind": "原作", "name": name} for name in value.original_works]
    for kind, name in (("发行性质", value.release_type), ("出版形态", value.publication_format),
                       ("作品组", value.work_group), ("文字版本", value.text_version)):
        if name.strip():
            entities.append({"kind": kind, "name": name.strip()})
    for kind, names in (("发布方式", value.release_methods), ("发布平台", value.release_platforms)):
        entities.extend({"kind": kind, "name": name} for name in names)
    if value.age_rating == "R18+" and value.uncensored:
        entities.append({"kind": "修正状态", "name": "无修正"})
    if value.age_rating in ("Everyone", "MA15+", "R18+"):
        entities.append({"kind": "年龄分级", "name": value.age_rating})
    for genre in split_values(value.genre):
        entities.append({"kind": "内容类型", "name": genre})
    if value.color:
        entities.append({"kind": "色彩", "name": value.color})
    alternate_titles = []
    if value.collection_mode == "series" and value.localized_series.strip():
        alternate_titles.append({"label": value.localized_series_label.strip(), "title": value.localized_series.strip()})
    return {"notableEntities": entities, "alternateTitles": alternate_titles}


def komga_book_payload(metadata: Metadata, existing_entities: list[dict] | None = None) -> dict:
    """Replace managed classifications while preserving imported characters and other kinds."""
    entities = []
    seen = set()
    managed = {title_key(kind) for kind in MANAGED_ENTITY_KINDS}
    for entity in existing_entities or []:
        kind, name = entity["kind"].strip(), entity["name"].strip()
        key = (title_key(kind), title_key(name))
        if kind and name and title_key(kind) not in managed and key not in seen:
            entities.append({"kind": kind, "name": name})
            seen.add(key)
    entities.extend(komga_metadata(metadata)["notableEntities"])
    return {"notableEntities": entities, "notableEntitiesLock": True}


def komga_series_payload(metadata: Metadata, existing_titles: list[dict] | None = None) -> dict | None:
    """Replace this alias label while keeping alternate titles with other labels."""
    if metadata.collection_mode != "series":
        return None
    label = metadata.localized_series_label.strip()
    titles = [{"label": item["label"], "title": item["title"]} for item in existing_titles or []
              if title_key(item["label"]) != title_key(label)]
    titles.extend(komga_metadata(metadata)["alternateTitles"])
    return {"alternateTitles": titles, "alternateTitlesLock": True}


def komga_warnings(metadata: Metadata) -> list[str]:
    warnings = []
    if metadata.collection_mode == "oneshot":
        warnings.append("请在 Komga 书库设置 One-Shots directory 为 /_oneshots，独立作品才能按 One-Shot 识别。")
        if any(value.strip() for value in (metadata.series, metadata.number, metadata.count)):
            warnings.append("独立作品的旧系列和编号仅保存在本机，本次 ComicInfo.xml 不写入这些值。")
    else:
        warnings.append("Komga 将出版社、内容类型、语言和年龄分级汇总到系列层级，逐册分类通过补充实体保存。")
    if metadata.volume.strip():
        warnings.append("Volume 会保留在 ComicInfo.xml；请关闭 Komga 的“将卷号附加到系列标题”，避免拆分系列。Komga API 不支持单独编辑卷号。")
    if metadata.reading_direction in ("VERTICAL", "WEBTOON"):
        warnings.append("纵向 / Webtoon 方向通过 Komga 系列原生 readingDirection 同步；ComicInfo Manga 无法表达此模式，成品中保留独立设置的 Manga 值。")
    if metadata.collection_mode == "series" and metadata.localized_series.strip():
        warnings.append("系列别名通过 Komga 补充清单同步到 alternateTitles，ComicInfo.xml 不包含 LocalizedSeries。")
    return warnings
