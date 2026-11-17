"""Synthetic four-book catalog with real migration and historical review events.

This fixture deliberately does not mock Snapshot.initial_metadata or inject
metadata_context. It never opens the user's catalog or NAS folders.
"""
import json
from pathlib import Path
from PIL import Image

from comic_manage.metadata import Metadata, V12_FIELDS, V13_FIELDS
from comic_manage.repository import Repository
from comic_manage.sources import inspect_source


def make_legacy_catalog(root: Path, *, own_xml: bool = False, historical_approval: bool = False) -> dict:
    collection = root / "漫画源" / "[pink-noise (みずいろめがね)] 合集"
    parent = collection / "[2011.08] Cattleya & Lana [穿刺個人漢化]"
    books = [(parent / "有文字", "有文字", ["pink-noise (みずいろめがね)"], 2),
             (parent / "无文字", "无文字", ["pink-noise (みずいろめがね)"], 3),
             (collection / "[2014.01] 普通作品甲", "普通作品甲", ["手动作者甲"], 1),
             (collection / "[2015.02] 普通作品乙", "手工校订标题乙", ["手动作者乙"], 1)]
    path = root / "data" / "catalog.sqlite3"
    repo = Repository(path)
    identities = []
    for index, (folder, title, authors, count) in enumerate(books):
        folder.mkdir(parents=True, exist_ok=True)
        for page in range(count):
            Image.new("RGB", (3, 4), (40 + index * 10, 60 + page * 10, 80)).save(folder / f"{page + 1}.png")
        if own_xml and index < 2:
            (folder / "ComicInfo.xml").write_text(f"<ComicInfo><Title>{title}</Title><Writer>XML作者</Writer><Genre>漫画</Genre></ComicInfo>", encoding="utf8")
        identity = repo.upsert_scan(inspect_source(folder))
        identities.append(identity)
        raw = Metadata(title=title, original_title=title, authors=authors, genre="漫画", tags=["人工保留标签"],
                       age_rating="Everyone", summary=f"人工说明-{index}").to_dict()
        for field in V12_FIELDS | V13_FIELDS:
            raw.pop(field, None)
        raw.update(full_color=index == 1, translated=index == 0)
        with repo.connect() as db:
            db.execute("UPDATE comics SET metadata=?,status=?,warning='' WHERE id=?",
                       (json.dumps(raw, ensure_ascii=False), "approved" if historical_approval and index < 2 else "draft", identity))
            db.execute("DELETE FROM events WHERE comic_id=?", (identity,))
            repo._event(db, identity, "import", str(folder))
            if authors:
                repo._event(db, identity, "batch_edit", "导入时批量更新作者或类型")
            if historical_approval and index < 2:
                repo._event(db, identity, "approve", "historical-source-fingerprint")
    repo.set_setting("metadata_rules", "1.1-K")
    repo.set_setting("roots", "\n".join(str(folder) for folder, _, _, _ in books))
    repo.set_setting("exact_import_roots", "\n".join(str(folder) for folder, _, _, _ in books))
    return {"path": path, "identities": identities, "folders": [folder for folder, _, _, _ in books],
            "parent": parent, "expected_title": "Cattleya & Lana"}


def migrated_restore_fixture(root: Path, **options) -> dict:
    """Mirror the visible legacy records after their last old remove/restore."""
    fixture = make_legacy_catalog(root, **options)
    repo = Repository(fixture["path"])
    for identity in fixture["identities"]:
        with repo.connect() as db:
            db.execute("INSERT INTO removed_comics VALUES (?,?)", (identity, "2026-11-01T00:00:00+00:00"))
            repo._event(db, identity, "remove", "从漫画库移除，保留源文件、成品和人工资料")
            db.execute("DELETE FROM removed_comics WHERE comic_id=?", (identity,))
            repo._event(db, identity, "restore", "synthetic-restore")
            repo._edit_event(db, identity, "batch_edit", "导入时批量更新作者或类型", {"authors", "genre"})
    fixture["repo"] = repo
    return fixture
