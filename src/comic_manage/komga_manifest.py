"""Export API supplements for verified Komga CBZ outputs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import zipfile

from .komga import komga_metadata
from .metadata import to_comicinfo
from .sources import is_inside

MANAGED_KINDS = ["原作", "发行性质", "出版形态", "发布方式", "发布平台", "作品组", "文字版本", "修正状态", "年龄分级", "色彩", "内容类型"]


def export_manifest(repo, comic_ids: list[str], destination: Path) -> dict:
    if destination.suffix.lower() != ".json":
        raise ValueError("Komga 同步清单请选择 JSON 文件。")
    books = []
    series = {}
    for identity in comic_ids:
        comic = repo.get(identity)
        if comic.status != "packaged" or not comic.output:
            raise ValueError(f"《{comic.metadata.display_title}》请先审核并生成新版 CBZ。")
        source = Path(comic.source)
        if comic.kind == "folder" and is_inside(destination, source):
            raise ValueError("同步清单请选择漫画源目录之外的位置。")
        output = Path(comic.output)
        with zipfile.ZipFile(output) as archive:
            info = archive.getinfo("ComicInfo.xml")
            if info.file_size > 2 * 1024 * 1024:
                raise ValueError("成品 ComicInfo.xml 过大，请重新生成。")
            if archive.read(info) != to_comicinfo(comic.metadata, comic.page_count):
                raise ValueError(f"《{comic.metadata.display_title}》成品资料与本机不一致，请重新生成。")
        payload = komga_metadata(comic.metadata)
        relative = f"{output.parent.name}/{output.name}"
        books.append({
            "relativePath": relative, "fileName": output.name, "title": comic.metadata.display_title,
            "notableEntities": payload["notableEntities"],
        })
        if comic.metadata.collection_mode == "series" and payload["alternateTitles"]:
            directory = output.parent.name
            entry = series.setdefault(directory, {
                "relativePath": directory, "seriesTitle": comic.metadata.series, "alternateTitles": [],
            })
            for alias in payload["alternateTitles"]:
                if any(old["label"] == alias["label"] and old["title"] != alias["title"]
                       for old in entry["alternateTitles"]):
                    raise ValueError(f"系列《{comic.metadata.series}》的同标签别名不一致，请先核对。")
                if alias not in entry["alternateTitles"]:
                    entry["alternateTitles"].append(alias)
    document = {
        "schemaVersion": "comic-manage-komga/1.0",
        "minimumKomgaVersionForNotableEntities": "1.29.0",
        "recommendedVersion": "1.30.1",
        "managedEntityKinds": MANAGED_KINDS,
        "books": books, "series": list(series.values()),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=".komga-", suffix=".partial", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"count": len(books), "seriesCount": len(series), "path": str(destination)}
