"""Explicit rescans repair old version-label drafts without erasing manual work."""
import json
from pathlib import Path
import tempfile
import unittest

from comic_manage.metadata import Metadata
from comic_manage.repository import Repository
from comic_manage.sources import inspect_source, source_id


class VariantReimportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / "[2011.08] [親作者] Parent Work [親漢化組]"
        self.repo = Repository(self.root / "data" / "catalog.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def book(self, leaf="无文字", **metadata):
        path = self.work / leaf
        path.mkdir(parents=True, exist_ok=True)
        (path / "1.jpg").write_bytes(b"page-one")
        old = Metadata(title=metadata.pop("title", leaf), original_title=metadata.pop("original_title", leaf), **metadata)
        snapshot = inspect_source(path)
        snapshot.metadata_context = {}
        snapshot.initial_metadata = lambda: (Metadata.from_dict(old.to_dict()), "")
        identity = self.repo.upsert_scan(snapshot)
        return path, identity

    def fresh(self, path):
        version = "有文字" if "有文字" in path.name else "无文字"
        hint = Metadata(title="Parent Work", original_title="Parent Work", authors=["親作者"],
                        publisher="親サークル", translator="親漢化組", language="zh-Hans", genre="漫画",
                        tags=["日常"], release_type="同人发行", publication_format="独立作品",
                        year="2011", month="8", color="全彩", text_version=version,
                        work_group="Parent Work（親作者）")
        snapshot = inspect_source(path)
        snapshot.metadata_context = {"version": version, "parent_title": "Parent Work", "signature": "context",
                                     "parent_metadata": hint.to_dict(), "work_group": hint.work_group}
        snapshot.initial_metadata = lambda: (Metadata.from_dict(hint.to_dict()), "父作品目录提供资料线索。")
        return snapshot

    def values(self, identity):
        return self.repo.get(identity, include_removed=True).metadata.to_dict()

    def test_untouched_draft_pair_uses_parent_name_and_common_group_without_merging(self):
        pairs = [self.book("无文字"), self.book("【有文字】")]
        before = {identity: self.repo.get(identity) for _, identity in pairs}
        for path, identity in pairs:
            preview, warning, changed = self.repo.preview_variant_repair(self.fresh(path), before[identity])
            self.assertEqual(preview.title, "Parent Work")
            self.assertIn("title", changed)
            self.assertIn("修正文字版本", warning)
            self.assertEqual(self.values(identity)["title"], path.name, "Preview must be read-only")
            self.repo.upsert_scan(self.fresh(path))
            after = self.repo.get(identity)
            self.assertEqual(after.metadata.to_dict(), preview.to_dict())
            self.assertEqual((after.id, after.source, after.fingerprint, after.page_count),
                             (before[identity].id, before[identity].source, before[identity].fingerprint, 1))
            self.assertEqual(after.status, "draft")
            self.assertEqual(after.metadata.authors, ["親作者"])
            self.assertEqual(after.metadata.translator, "親漢化組")
            self.assertEqual((after.metadata.year, after.metadata.month), ("2011", "8"))
            self.assertEqual(after.metadata.work_group, "Parent Work（親作者）")
            self.assertEqual(after.metadata.collection_mode, "oneshot")
            self.assertEqual((after.metadata.series, after.metadata.number, after.metadata.count), ("", "", ""))
        self.assertNotEqual(pairs[0][1], pairs[1][1])
        self.assertEqual({self.values(identity)["text_version"] for _, identity in pairs}, {"有文字", "无文字"})

    def test_known_author_edit_preserves_author_and_fills_only_unedited_empty_fields(self):
        path, identity = self.book()
        metadata = self.repo.get(identity).metadata
        metadata.authors = ["人工作者"]
        metadata.summary = "人工说明"
        self.repo.save(identity, metadata)
        self.repo.upsert_scan(self.fresh(path))
        after = self.values(identity)
        self.assertEqual(after["authors"], ["人工作者"])
        self.assertEqual(after["summary"], "人工说明")
        self.assertEqual(after["translator"], "親漢化組")
        self.assertEqual(after["work_group"], "Parent Work（親作者）", "Group does not depend on local author edits")

    def test_legacy_unknown_edits_allow_only_narrow_label_and_empty_variant_repair(self):
        path, identity = self.book(authors=["pink-noise (みずいろめがね)"], tags=["人工标签"])
        with self.repo.connect() as db:
            self.repo._event(db, identity, "edit", "人工修改元数据")
        self.repo.upsert_scan(self.fresh(path))
        after = self.repo.get(identity)
        self.assertEqual(after.metadata.title, "Parent Work")
        self.assertEqual(after.metadata.text_version, "无文字")
        self.assertEqual(after.metadata.work_group, "Parent Work（親作者）")
        self.assertEqual(after.metadata.authors, ["pink-noise (みずいろめがね)"])
        self.assertEqual(after.metadata.tags, ["人工标签"])
        for field in ("translator", "publisher", "language", "year", "month", "genre", "color"):
            self.assertEqual(getattr(after.metadata, field), "", field)
        self.assertIn("缺少逐字段编辑历史", after.warning)

    def test_explicit_version_and_group_clear_are_never_filled_back(self):
        path, identity = self.book(text_version="无文字", work_group="用户旧组")
        metadata = self.repo.get(identity).metadata
        metadata.text_version = metadata.work_group = ""
        self.repo.save(identity, metadata)
        self.repo.upsert_scan(self.fresh(path))
        after = self.values(identity)
        self.assertEqual(after["title"], "Parent Work")
        self.assertEqual((after["text_version"], after["work_group"]), ("", ""))
        self.repo.upsert_scan(self.fresh(path))
        self.assertEqual(self.values(identity), after)

    def test_legacy_checked_batch_clear_is_protected(self):
        path, identity = self.book()
        with self.repo.connect() as db:
            self.repo._event(db, identity, "batch_edit", "批量修改 text_version, work_group。需要重新审核。")
        self.repo.upsert_scan(self.fresh(path))
        self.assertEqual((self.values(identity)["text_version"], self.values(identity)["work_group"]), ("", ""))

    def test_old_bracket_labels_with_empty_auto_titles_can_be_repaired_but_clears_stay_empty(self):
        path, identity = self.book("[有文字]", title="", original_title="")
        self.repo.upsert_scan(self.fresh(path))
        self.assertEqual((self.values(identity)["title"], self.values(identity)["original_title"]), ("Parent Work", "Parent Work"))
        metadata = self.repo.get(identity).metadata
        metadata.title = metadata.original_title = ""
        self.repo.save(identity, metadata)
        before = self.values(identity)
        self.repo.upsert_scan(self.fresh(path))
        self.assertEqual(self.values(identity), before)
        other, other_id = self.book("［无文字］", title="", original_title="")
        with self.repo.connect() as db:
            self.repo._event(db, other_id, "edit", "人工修改元数据")
        self.repo.upsert_scan(self.fresh(other))
        self.assertEqual((self.values(other_id)["title"], self.values(other_id)["original_title"]), ("", ""))

    def test_intentional_generic_title_and_meaningful_manual_title_are_preserved(self):
        path, identity = self.book("【无文字】")
        metadata = self.repo.get(identity).metadata
        metadata.title = "无文字"
        self.repo.save(identity, metadata)
        before = self.values(identity)
        preview, warning, changed = self.repo.preview_variant_repair(self.fresh(path), self.repo.get(identity))
        self.assertEqual(preview.to_dict(), before)
        self.assertEqual(changed, [])
        self.assertIn("曾人工修改", warning)
        self.repo.upsert_scan(self.fresh(path))
        self.assertEqual(self.values(identity), before)
        metadata.title = "人工标题"
        self.repo.save(identity, metadata)
        before = self.values(identity)
        self.repo.upsert_scan(self.fresh(path))
        self.assertEqual(self.values(identity), before)

    def test_current_or_historical_review_and_packaged_output_are_protected(self):
        path, identity = self.book(authors=["作者"], age_rating="Everyone", genre="漫画", tags=["日常"])
        self.repo.approve(identity, inspect_source(path))
        before = self.values(identity)
        self.repo.upsert_scan(self.fresh(path))
        self.assertEqual(self.values(identity), before)
        self.assertEqual(self.repo.get(identity).status, "approved")
        metadata = self.repo.get(identity).metadata
        metadata.summary = "新草稿"
        self.repo.save(identity, metadata)
        before = self.values(identity)
        self.repo.upsert_scan(self.fresh(path))
        self.assertEqual(self.values(identity), before, "A previously reviewed draft is not silently retitled")
        self.repo.mark_packaged(identity, self.root / "own.cbz", {})
        self.repo.save(identity, metadata)
        self.repo.upsert_scan(self.fresh(path))
        self.assertEqual(self.values(identity), before)
        self.assertTrue(self.repo.get(identity).output.endswith("own.cbz"))

    def test_removed_restore_metadata_is_unchanged(self):
        path, identity = self.book()
        before = self.values(identity)
        self.repo.remove([identity])
        self.assertIsNone(self.repo.upsert_scan(self.fresh(path)))
        self.assertEqual(self.values(identity), before)
        self.repo.upsert_scan(self.fresh(path), restore=True)
        self.assertEqual(self.values(identity), before)
        self.assertEqual(self.repo.get(identity).status, "draft")

    def test_import_overwrite_defaults_apply_after_parent_repair(self):
        path, identity = self.book()
        self.repo.upsert_scan(self.fresh(path), defaults={"authors": ["批量作者"], "genre": "小说"}, overwrite_existing=True)
        after = self.values(identity)
        self.assertEqual((after["title"], after["original_title"]), ("Parent Work", "Parent Work"))
        self.assertEqual((after["authors"], after["genre"]), (["批量作者"], "小说"))
        self.assertEqual(after["text_version"], "无文字")
        self.assertEqual(after["work_group"], "Parent Work（親作者）")

    def test_xml_or_absent_parent_context_never_rewrites_existing_metadata(self):
        path, identity = self.book()
        before = self.values(identity)
        snapshot = self.fresh(path)
        snapshot.xml = b"<ComicInfo/>"
        self.repo.upsert_scan(snapshot)
        self.assertEqual(self.values(identity), before)
        snapshot.xml = b""
        snapshot.metadata_context = {}
        self.repo.upsert_scan(snapshot)
        self.assertEqual(self.values(identity), before)

    def test_source_changes_and_repair_both_remain_visible(self):
        path, identity = self.book()
        (path / "2.jpg").write_bytes(b"page-two")
        fresh = self.fresh(path)
        self.repo.upsert_scan(fresh)
        comic = self.repo.get(identity)
        self.assertEqual((comic.id, comic.source, comic.fingerprint, comic.page_count),
                         (source_id(path), str(path.resolve()), fresh.fingerprint, 2))
        self.assertIn("源文件已变化", comic.warning)
        self.assertIn("修正文字版本", comic.warning)
        self.assertEqual(comic.metadata.title, "Parent Work")
        self.assertEqual(comic.status, "draft")

    def test_repairs_are_idempotent_and_canonicalization_cannot_touch_pencillers(self):
        path, identity = self.book(pencillers=["親作者"])
        self.repo.upsert_scan(self.fresh(path))
        before = self.repo.get(identity)
        self.assertEqual(before.metadata.authors, [])
        self.assertEqual(before.metadata.pencillers, ["親作者"])
        self.assertEqual(before.metadata.title, "Parent Work")
        self.repo.upsert_scan(self.fresh(path))
        self.assertEqual(self.repo.get(identity).metadata.to_dict(), before.metadata.to_dict())
        self.assertEqual(self.repo.get(identity).updated_at, before.updated_at)
        with self.repo.connect() as db:
            repairs = db.execute("SELECT COUNT(*) FROM events WHERE comic_id=? AND action='version_folder_repair'", (identity,)).fetchone()[0]
        self.assertEqual(repairs, 1)


if __name__ == "__main__":
    unittest.main()
