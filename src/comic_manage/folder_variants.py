"""Conservative, explicit edition hints from parent-work/version directories.

This module reads directory names only. It never reads another edition's pages
or sidecar and never converts a version relation to Series/Number.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from .filename_parser import parse_filename
from .metadata import Metadata, parse_xml, read_variant_fields, split_values


_PAIRS = {"[": "]", "［": "］", "【": "】"}
_VERSIONS = {"有文字": "有文字", "无文字": "无文字", "无字版": "无文字"}
_COLLECTIONS = {"合集", "全集", "作品集", "全作品", "全套", "collection", "collections", "collected works"}
_GENERIC_PARENTS = _COLLECTIONS | {
    "漫画", "漫畫", "漫画库", "漫畫庫", "漫画源", "漫畫源", "图片", "圖片", "源图", "原图",
    "comics", "comic", "books", "book", "library", "images", "image", "source", "sources",
    "有文字", "无文字", "无字版",
}


def recognize_version_folder(name: str) -> str:
    """Recognize only an entire, optionally bracketed, edition directory name."""
    value = name.strip()
    if len(value) >= 2 and value[0] in _PAIRS and value[-1] == _PAIRS[value[0]]:
        value = value[1:-1].strip()
    return _VERSIONS.get(value, "")


def _plain_label(name: str) -> str:
    value = name.strip()
    if len(value) >= 2 and value[0] in _PAIRS and value[-1] == _PAIRS[value[0]]:
        value = value[1:-1].strip()
    return value.casefold()


def _safe_directory(path: Path) -> bool:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return False
    info = path.stat(follow_symlinks=False)
    return not (getattr(info, "st_file_attributes", 0) & 0x400) and path.is_dir()


def _has_namespace(tags: list[str], namespace: str) -> bool:
    return any(tag.replace("：", ":", 1).partition(":")[0].strip() == namespace for tag in tags)


def _collection_author(name: str) -> tuple[list[str], str]:
    # A bare ancestor name is not an author declaration. Only a clearly labelled
    # author/circle collection can provide this one additional level of hints.
    if not name.strip().startswith(("[", "［")):
        return [], ""
    metadata, _ = parse_filename(name)
    if metadata.display_title.casefold() not in _COLLECTIONS:
        return [], ""
    return metadata.authors, metadata.publisher


def read_variant_context(path: Path) -> dict:
    """Capture names used to infer this leaf, with an independent context hash."""
    version = recognize_version_folder(path.name)
    if not version:
        return {}
    parent = path.parent
    metadata, warnings = parse_filename(parent.name)
    meaningful = bool(metadata.display_title and _plain_label(metadata.display_title) not in _GENERIC_PARENTS)
    ancestor_name = ""
    if meaningful and not metadata.authors and not metadata.publisher:
        ancestor_name = parent.parent.name
        authors, publisher = _collection_author(ancestor_name)
        if authors or publisher:
            metadata.authors = list(authors)
            metadata.publisher = publisher
            warnings.append("已从上一层明确的作者／社团合集目录补充作者资料，请核对。")
    sibling_names = []
    sibling_versions = []
    try:
        for child in parent.iterdir():
            sibling = recognize_version_folder(child.name)
            if sibling and _safe_directory(child):
                sibling_names.append(child.name)
                sibling_versions.append(sibling)
    except OSError:
        warnings.append("未能完整读取同级版本目录，版本关联请人工核对。")
        sibling_names.append("<unreadable>")
    sibling_names.sort()
    sibling_versions = sorted(set(sibling_versions))
    work_group = metadata.work_group if meaningful else ""
    declared_group = _has_namespace(metadata.tags, "作品组")
    explicit_work = bool(metadata.authors or metadata.publisher or metadata.year or metadata.translator
                         or metadata.genre or metadata.release_type or metadata.publication_format)
    if meaningful and not work_group and not declared_group and (len(sibling_versions) == 2 or explicit_work):
        owners = list(metadata.authors)
        owners.extend(value for value in split_values(metadata.publisher) if value not in owners)
        if owners:
            work_group = metadata.display_title + "（" + " / ".join(owners) + "）"
        else:
            identity = os.path.normcase(str(parent.resolve()))
            source_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
            work_group = metadata.display_title + f"（来源 {source_key}）"
            warnings.append("父目录没有明确作者／社团，作品组已加入来源短标识以避免同名误合并，请核对。")
        if re.search(r"[,，;；\n]", work_group):
            work_group = ""
            warnings.append("作品名或作者包含标签分隔符，作品组未自动填写，请人工设置稳定名称。")
    elif meaningful and not work_group and not declared_group:
        warnings.append("仅发现单个版本且父目录缺少明确作品标记，作品组留空，请按实际关联需要填写。")
    if not meaningful:
        warnings.append("版本子目录的父目录不是明确作品名，未将合集或书库名称用作作品标题和作品组，请人工填写。")
    if metadata.text_version and metadata.text_version != version:
        warnings.append(f"父目录文字版本为“{metadata.text_version}”，子目录为“{version}”；按子目录识别，请核对。")
    metadata.normalize()
    payload = {"version": version, "parent": parent.name, "ancestor": ancestor_name,
               "parent_metadata": metadata.to_dict(), "work_group": work_group,
               "siblings": sibling_names}
    signature = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"version": version, "parent_title": metadata.display_title if meaningful else "",
            "parent_metadata": metadata.to_dict(), "work_group": work_group,
            "warnings": list(dict.fromkeys(warnings)), "signature": signature,
            "sibling_versions": sibling_versions}


def apply_variant_context(metadata: Metadata, context: dict, *, xml: bytes = b"") -> tuple[Metadata, list[str]]:
    """Use captured names as hints, retaining the edition's own XML authority."""
    warnings = list(context.get("warnings", []))
    version = context.get("version", "")
    if not version:
        return metadata, warnings
    if not xml:
        if context.get("parent_title"):
            metadata = Metadata.from_dict(context["parent_metadata"])
        metadata.text_version = version
        metadata.work_group = context.get("work_group", "")
        metadata.normalize()
        return metadata, warnings
    # Existing sidecars are authoritative, including native empty values. Only
    # missing v1.3 classifications receive explicit directory-structure hints.
    tags = split_values((parse_xml(xml).findtext("Tags") or "").strip())
    if recognize_version_folder(metadata.display_title) and context.get("parent_title"):
        warnings.append(f"本版 ComicInfo 标题仍是版本目录名，已保留原值；父作品名候选为“{context['parent_title']}”，请核对。")
    _, evidence_warnings = read_variant_fields([], tags)
    warnings.extend(evidence_warnings)
    if _has_namespace(tags, "文字版本"):
        if metadata.text_version != version:
            warnings.append(f"本版 ComicInfo 文字版本与目录“{version}”不一致，已保留 ComicInfo 原值，请核对。")
    elif not metadata.text_version:
        metadata.text_version = version
    elif metadata.text_version != version:
        warnings.append(f"本版 ComicInfo 文字版本与目录“{version}”不一致，已保留 ComicInfo 原值，请核对。")
    group = context.get("work_group", "")
    if _has_namespace(tags, "作品组"):
        if group and metadata.work_group != group:
            warnings.append("本版 ComicInfo 作品组与父目录候选不一致，已保留 ComicInfo 原值，请核对。")
    elif not metadata.work_group:
        metadata.work_group = group
    parent = context.get("parent_metadata", {})
    if metadata.authors and parent.get("authors") and metadata.authors != parent["authors"]:
        warnings.append("本版 ComicInfo 作者与父目录提示不同，已保留本版作者；作品组仍按父作品关联，请核对。")
    metadata.normalize()
    return metadata, list(dict.fromkeys(warnings))
