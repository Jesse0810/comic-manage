"""Selection-based import, catalog removal and bulk editing behavior."""
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import zipfile

from comic_manage.metadata import Metadata
from comic_manage.service import ComicService, PLAN_TTL_SECONDS, ServiceError
from comic_manage.sources import inspect_source, source_id


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / "选择漫画"
        self.first = self.book("作者甲的漫画")
        self.second = self.book("作者乙的漫画")
        self.service = ComicService(self.root / "data")

    def tearDown(self):
        self.temp.cleanup()

    def book(self, name, parent=None):
        book = (parent or self.library) / name
        book.mkdir(parents=True)
        (book / "1.jpg").write_bytes(b"first-page")
        return book

    def preview(self, *paths, **extra):
        return self.service.call("prepare_import", {"paths": [str(p) for p in (paths or (self.library,))], **extra})

    def commit(self, preview, ids=None, **extra):
        return self.service.call("commit_import", {"token": preview["token"],
                                 "ids": ids or [item["id"] for item in preview["items"]], **extra})

    def imported(self, book=None):
        book = book or self.first
        self.commit(self.preview(book))
        return source_id(book)

    def test_preview_is_read_only_and_deduplicates_overlapping_folders(self):
        result = self.preview(self.library, self.first, self.library)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual({item["import_status"] for item in result["items"]}, {"new"})
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.service.repo.all(), [])
        self.assertEqual(self.service.repo.setting("roots"), "")

    def test_only_checked_books_are_imported_and_remembered_for_rescan(self):
        preview = self.preview()
        first_id = source_id(self.first)
        result = self.commit(preview, [first_id], defaults={"authors": ["  已知作者  "], "genre": "漫画"})
        self.assertEqual(result["count"], 1)
        comic = self.service.repo.get(first_id)
        self.assertEqual(comic.metadata.authors, ["已知作者"])
        self.assertEqual(comic.metadata.genre, "漫画")
        self.assertEqual(comic.status, "draft")
        roots = self.service.repo.setting("roots").splitlines()
        self.assertEqual(len(roots), 1)
        self.assertTrue(Path(roots[0]).samefile(self.first))
        self.assertEqual(self.service.call("scan", {})["count"], 1)
        self.assertEqual([c.id for c in self.service.repo.all()], [first_id])

    def test_unchecked_nested_book_is_not_imported_on_rescan(self):
        nested = self.book("未勾选的子漫画", parent=self.first)
        preview = self.preview(self.first)
        self.assertEqual(len(preview["items"]), 2)
        self.commit(preview, [source_id(self.first)])
        self.service.call("scan", {})
        with self.assertRaises(ValueError):
            self.service.repo.get(source_id(nested))

    def test_nonrecursive_preview_inspects_only_selected_folder(self):
        self.book("子漫画", parent=self.first)
        result = self.preview(self.first, recursive=False)
        self.assertEqual([item["id"] for item in result["items"]], [source_id(self.first)])

    def test_existing_manual_metadata_and_approval_survive_default_import(self):
        identity = self.imported()
        metadata = Metadata(age_rating="Everyone", original_title="人工标题", authors=["人工作者"], genre="漫画", release_type="商业出版", summary="人工说明")
        self.service.call("approve", {"id": identity, "metadata": metadata.to_dict()})
        preview = self.preview(self.first)
        item = preview["items"][0]
        self.assertEqual(item["import_status"], "existing")
        self.assertEqual(item["metadata"]["original_title"], "人工标题")
        result = self.commit(preview, defaults={"authors": ["批量作者"], "genre": "漫画"})
        self.assertEqual((result["count"], result["skipped"]), (0, 1))
        comic = self.service.repo.get(identity)
        self.assertEqual(comic.metadata.to_dict(), metadata.to_dict())
        self.assertEqual(comic.status, "approved")

    def test_explicit_overwrite_updates_only_author_and_type_and_revokes_review(self):
        identity = self.imported()
        metadata = Metadata(age_rating="Everyone", original_title="人工标题", authors=["旧作者"], genre="小说", summary="保留说明", tags=["保留标签"])
        self.service.call("approve", {"id": identity, "metadata": metadata.to_dict()})
        self.commit(self.preview(self.first), defaults={"authors": ["新作者"], "genre": "漫画"}, overwriteExisting=True)
        comic = self.service.repo.get(identity)
        self.assertEqual(comic.metadata.authors, ["新作者"])
        self.assertEqual(comic.metadata.genre, "漫画")
        self.assertEqual(comic.metadata.publication_format, "")
        self.assertEqual((comic.metadata.original_title, comic.metadata.summary, comic.metadata.tags),
                         ("人工标题", "保留说明", ["保留标签"]))
        self.assertEqual(comic.status, "draft")

    def test_remove_hides_record_without_touching_source_or_output_and_rescan_skips_it(self):
        identity = self.imported()
        metadata = Metadata(age_rating="Everyone", original_title="人工标题", authors=["作者"])
        self.service.call("save", {"id": identity, "metadata": metadata.to_dict()})
        output = self.root / "成品.cbz"
        output.write_bytes(b"existing-export")
        self.service.repo.mark_packaged(identity, output, {})
        self.assertEqual(self.service.call("remove", {"ids": [identity]})["count"], 1)
        self.assertEqual(self.service.call("bootstrap")["comics"], [])
        with self.assertRaises(ValueError):
            self.service.call("page", {"id": identity})
        self.assertEqual(self.service.call("scan", {})["count"], 0)
        self.assertEqual((self.first / "1.jpg").read_bytes(), b"first-page")
        self.assertEqual(output.read_bytes(), b"existing-export")
        retained = self.service.repo.get(identity, include_removed=True)
        self.assertEqual(retained.metadata.original_title, "人工标题")
        self.assertTrue(Path(retained.output).samefile(output))

    def test_explicit_reimport_restores_removed_book_preserving_manual_data_as_draft(self):
        identity = self.imported()
        self.service.call("approve", {"id": identity, "metadata": {"age_rating": "Everyone", "authors": ["人工作者"], "genre": "漫画", "tags": ["日常"], "original_title": "人工标题"}})
        self.service.call("remove", {"ids": [identity]})
        preview = self.preview(self.first)
        self.assertEqual(preview["items"][0]["import_status"], "removed")
        self.assertEqual(self.commit(preview, defaults={"authors": ["不应覆盖"]})["count"], 1)
        comic = self.service.repo.get(identity)
        self.assertEqual(comic.metadata.authors, ["人工作者"])
        self.assertEqual(comic.metadata.original_title, "人工标题")
        self.assertEqual(comic.status, "draft")

    def test_remove_and_batch_edit_validate_all_ids_before_any_changes(self):
        self.commit(self.preview())
        identity = source_id(self.first)
        before = self.service.repo.get(identity).metadata.to_dict()
        for method, extra in [("remove", {}), ("batch_edit", {"changes": {"authors": ["不应修改"]}})]:
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.service.call(method, {"ids": [identity, "missing-id"], **extra})
            self.assertFalse(self.service.repo.is_removed(identity))
            self.assertEqual(self.service.repo.get(identity).metadata.to_dict(), before)

    def test_batch_edit_revokes_review_and_keeps_other_fields_and_export_reference(self):
        self.commit(self.preview())
        identities = [source_id(self.first), source_id(self.second)]
        for identity in identities:
            self.service.call("approve", {"id": identity, "metadata": {"age_rating": "Everyone", "authors": ["旧作者"], "genre": "漫画", "tags": ["日常"], "summary": "保留说明"}})
            self.service.repo.mark_packaged(identity, self.root / f"{identity}.cbz", {})
        result = self.service.call("batch_edit", {"ids": identities, "changes": {"authors": ["统一作者"], "genre": "漫画"}})
        self.assertEqual(result["count"], 2)
        for identity in identities:
            comic = self.service.repo.get(identity)
            self.assertEqual(comic.metadata.authors, ["统一作者"])
            self.assertEqual(comic.metadata.genre, "漫画")
            self.assertEqual(comic.metadata.release_type, "")
            self.assertEqual(comic.metadata.summary, "保留说明")
            self.assertTrue(comic.output.endswith(f"{identity}.cbz"))
            self.assertEqual(comic.status, "draft")

    def test_batch_series_fields_preserve_book_specific_values_and_can_be_cleared(self):
        self.commit(self.preview())
        identities = [source_id(self.first), source_id(self.second)]
        before = {}
        for index, identity in enumerate(identities, start=1):
            self.service.call("save", {"id": identity, "metadata": {
                "title": f"第 {index} 册", "number": str(index), "series": "旧系列", "collection_mode": "series",
                "authors": [f"作者 {index}"], "age_rating": "R18+", "uncensored": True,
                "genre": "漫画",
                "localized_series": "已有译名", "original_works": ["保留原作"],
            }})
            before[identity] = self.service.repo.get(identity).metadata.to_dict()
        changes = {"series": "新系列", "release_type": "同人发行", "publication_format": "合集", "age_rating": "MA15+"}
        result = self.service.call("batch_edit", {"ids": identities, "changes": changes})
        self.assertEqual(result["count"], 2)
        for comic in result["comics"]:
            self.assertEqual(comic["metadata"], {**before[comic["id"]], **changes})
            xml = self.service.call("xml", {"id": comic["id"]})["xml"]
            self.assertIn("<Series>新系列</Series>", xml)
            self.assertIn("发行性质:同人发行", xml)
            self.assertIn("出版形态:合集", xml)
            self.assertNotIn("修正状态:无修正", xml)
        clear = {"series": "", "localized_series": "", "release_type": "", "publication_format": "", "original_works": []}
        result = self.service.call("batch_edit", {"ids": identities, "changes": clear})
        self.assertEqual(result["count"], 2)
        for comic in result["comics"]:
            self.assertEqual(comic["metadata"], {**before[comic["id"]], **changes, **clear})

    def test_v12_batch_replaces_checked_lists_and_keeps_unchecked_metadata(self):
        self.commit(self.preview())
        identities = [source_id(self.first), source_id(self.second)]
        for index, identity in enumerate(identities):
            self.service.call("save", {"id": identity, "metadata": {
                "authors": [f"作者{index}"], "genre": "漫画", "tags": [f"保留标签{index}"],
                "publisher": f"出版方{index}", "color": "黑白", "release_type": "同人发行",
                "release_methods": ["单次购买"], "release_platforms": ["Fantia"],
            }})
        before = {identity: self.service.repo.get(identity).metadata.to_dict() for identity in identities}
        changes = {"release_methods": ["会员订阅", "公开发布"], "release_platforms": ["BOOTH", "独立平台"],
                   "color": "部分彩色", "reading_direction": "WEBTOON", "volume": "7", "language": ""}
        result = self.service.call("batch_edit", {"ids": identities, "changes": changes})
        self.assertEqual(result["count"], 2)
        for comic in result["comics"]:
            self.assertEqual(comic["metadata"], {**before[comic["id"]], **changes})
        clear = {"release_methods": [], "release_platforms": [], "color": "", "reading_direction": ""}
        cleared = self.service.call("batch_edit", {"ids": identities, "changes": clear})
        for comic in cleared["comics"]:
            self.assertEqual(comic["metadata"], {**before[comic["id"]], **changes, **clear})

    def test_v12_batch_rejects_retired_classification_and_multiple_original_works(self):
        identity = self.imported()
        before = self.service.repo.get(identity).metadata.to_dict()
        for changes in ({"genre": "同人志"}, {"release_type": "同人"}, {"black_and_white": "No"},
                        {"release_methods": ["Fantia"]}, {"original_works": ["原作甲", "原作乙"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.service.call("batch_edit", {"ids": [identity], "changes": changes})
            self.assertEqual(self.service.repo.get(identity).metadata.to_dict(), before)

    def test_v13_work_group_and_text_version_are_independent_checked_fields(self):
        self.commit(self.preview())
        identities = [source_id(self.first), source_id(self.second)]
        for index, identity in enumerate(identities):
            self.service.call("save", {"id": identity, "metadata": {
                "title": "同名作品", "authors": [f"作者{index}"], "genre": "漫画", "tags": ["日常"],
                "collection_mode": "oneshot", "text_version": "有文字" if index == 0 else "无文字",
                "language": "ja" if index == 0 else "", "translator": "译者" if index == 0 else "",
            }})
        before = {identity: self.service.repo.get(identity).metadata.to_dict() for identity in identities}
        group = "My Work (作者甲)"
        result = self.service.call("batch_edit", {"ids": identities, "changes": {"work_group": group}})
        self.assertEqual(result["count"], 2)
        for comic in result["comics"]:
            self.assertEqual(comic["metadata"], {**before[comic["id"]], "work_group": group})
            self.assertEqual(comic["metadata"]["series"], "")
            self.assertEqual(comic["metadata"]["number"], "")
        changed = self.service.call("batch_edit", {"ids": [identities[1]], "changes": {"text_version": "有文字"}})
        self.assertEqual(changed["comics"][0]["metadata"], {**before[identities[1]], "work_group": group, "text_version": "有文字"})
        cleared = self.service.call("batch_edit", {"ids": identities, "changes": {"work_group": "", "text_version": ""}})
        for comic in cleared["comics"]:
            self.assertEqual(comic["metadata"], {**before[comic["id"]], "work_group": "", "text_version": ""})
        for changes in ({"work_group": ["不得多值"]}, {"text_version": "有字版"}, {"text_version": []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.service.call("batch_edit", {"ids": identities, "changes": changes})

    def test_v13_same_title_versions_export_as_two_independent_oneshots(self):
        self.commit(self.preview())
        identities = [source_id(self.first), source_id(self.second)]
        for identity, version in zip(identities, ("有文字", "无文字")):
            self.service.call("approve", {"id": identity, "metadata": {
                "title": "同名作品", "authors": ["同一作者"], "genre": "漫画", "tags": ["日常"],
                "age_rating": "Everyone", "collection_mode": "oneshot", "work_group": "同名作品（同一作者）", "text_version": version,
            }})
        plan = self.service.call("plan", {"ids": identities, "outputRoot": str(self.root / "versions")})
        destinations = [Path(item["destination"]) for item in plan["items"]]
        self.assertEqual(len(set(destinations)), 2)
        self.assertEqual({path.parent.name for path in destinations}, {"_oneshots"})
        result = self.service.call("export", {"token": plan["token"]})
        self.assertEqual(len(result["completed"]), 2)
        self.assertEqual(result["errors"], [])
        versions = set()
        for output in result["completed"]:
            with zipfile.ZipFile(output) as archive:
                xml = archive.read("ComicInfo.xml").decode("utf-8")
                self.assertIn("<Title>同名作品</Title>", xml)
                self.assertIn("作品组:同名作品（同一作者）", xml)
                self.assertNotIn("<Series>", xml)
                self.assertNotIn("<Number>", xml)
                self.assertNotIn("<作品组>", xml)
                versions.add("有文字" if "文字版本:有文字" in xml else "无文字")
        self.assertEqual(versions, {"有文字", "无文字"})

    def test_batch_conflict_rolls_back_earlier_changes_in_same_batch(self):
        self.commit(self.preview())
        identities = [source_id(self.first), source_id(self.second)]
        self.service.call("save", {"id": identities[1], "metadata": {
            "series": "原系列", "localized_series": "已有译名", "collection_mode": "series", "authors": ["原作者"],
            "pencillers": ["绘画者"], "year": "2024", "month": "2", "day": "29",
        }})
        before = [self.service.repo.get(identity) for identity in identities]
        for changes, error in [({"series": "已有译名"}, "系列别名"), ({"series": ""}, "系列别名"),
                               ({"authors": ["绘画者"]}, "已有绘画者"), ({"year": "2025"}, "日历日期")]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, error):
                self.service.call("batch_edit", {"ids": identities, "changes": {"release_type": "商业出版", **changes}})
            self.assertEqual([self.service.repo.get(identity) for identity in identities], before)

    def test_unchanged_batch_keeps_review_and_prepared_export(self):
        identity = self.imported()
        metadata = {"authors": ["作者"], "series": "系列", "number": "1", "collection_mode": "series", "age_rating": "Everyone", "genre": "漫画", "tags": ["日常"]}
        self.service.call("approve", {"id": identity, "metadata": metadata})
        plan = self.service.call("plan", {"ids": [identity], "outputRoot": str(self.root / "output")})
        result = self.service.call("batch_edit", {"ids": [identity], "changes": {key: value for key, value in metadata.items() if key != "collection_mode"}})
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["comics"][0]["status"], "approved")
        self.assertIn(plan["token"], self.service.plans)

    def test_batch_operations_invalidate_prepared_export(self):
        identity = self.imported()
        for operation, args in [("batch_edit", {"changes": {"genre": "小说"}}), ("remove", {})]:
            self.service.call("approve", {"id": identity, "metadata": {"age_rating": "Everyone", "authors": ["作者"], "genre": "漫画", "tags": ["日常"]}})
            plan = self.service.call("plan", {"ids": [identity], "outputRoot": str(self.root / "output")})
            self.service.call(operation, {"ids": [identity], **args})
            with self.assertRaisesRegex(ServiceError, "失效"):
                self.service.call("export", {"token": plan["token"]})

    def test_stale_source_is_reported_without_importing_changed_book(self):
        preview = self.preview()
        (self.first / "2.jpg").write_bytes(b"new-page")
        result = self.commit(preview)
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("变化", result["errors"][0])
        self.assertEqual([c.id for c in self.service.repo.all()], [source_id(self.second)])

    def test_import_token_is_bounded_expiring_and_one_use_and_rejects_injected_id(self):
        preview = self.preview(self.first)
        with self.assertRaises(ServiceError):
            self.commit(preview, [source_id(self.second)])
        self.assertEqual(self.service.repo.all(), [])
        self.commit(preview)
        with self.assertRaisesRegex(ServiceError, "失效"):
            self.commit(preview)
        preview = self.preview(self.second)
        self.service.imports[preview["token"]].created_at -= PLAN_TTL_SECONDS + 1
        with self.assertRaisesRegex(ServiceError, "失效"):
            self.commit(preview)
        for _ in range(10):
            self.preview(self.first)
        self.assertLessEqual(len(self.service.imports), 8)

    def test_cancelled_preview_returns_discovered_selection_without_writing_catalog(self):
        cancel = Event()
        self.service.progress = lambda _: cancel.set()
        result = self.service.call("prepare_import", {"paths": [str(self.library)]}, cancel)
        self.assertTrue(result["cancelled"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(self.service.repo.all(), [])

    def test_cancelled_commit_keeps_only_completed_records_and_roots(self):
        preview = self.preview()
        cancel = Event()
        original = self.service.repo.upsert_scan

        def stop_after_first(*args, **kwargs):
            result = original(*args, **kwargs)
            cancel.set()
            return result

        with patch.object(self.service.repo, "upsert_scan", side_effect=stop_after_first):
            result = self.service.call("commit_import", {"token": preview["token"],
                                       "ids": [item["id"] for item in preview["items"]]}, cancel)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(self.service.repo.all()), 1)
        self.assertEqual(len(self.service.repo.setting("roots").splitlines()), 1)

    def test_cbz_files_can_be_selected_alongside_folders(self):
        archive = self.root / "现有漫画.cbz"
        with zipfile.ZipFile(archive, "w") as target:
            target.writestr("1.jpg", b"archive-image")
        preview = self.preview(self.first, archive)
        self.assertEqual({item["kind"] for item in preview["items"]}, {"folder", "cbz"})
        self.assertEqual(self.commit(preview, defaults={"genre": "漫画"})["count"], 2)
        self.assertEqual({c.metadata.genre for c in self.service.repo.all()}, {"漫画"})
        self.assertEqual({c.metadata.publication_format for c in self.service.repo.all()}, {""})

    def test_folder_browser_skips_system_catalog_and_output_folders(self):
        (self.library / ".git").mkdir()
        self.service = ComicService(self.library / "data")
        output = self.library / "output"
        output.mkdir()
        self.service.call("set_output", {"path": str(output)})
        self.book("child", parent=self.first)
        result = self.service.call("browse_folders", {"path": str(self.library)})
        children = {child["name"]: child for child in result["children"]}
        self.assertEqual(set(children), {self.first.name, self.second.name})
        self.assertTrue(children[self.first.name]["hasChildren"])
        self.assertFalse(children[self.second.name]["hasChildren"])
        preview = self.preview(self.service.data_dir, output)
        self.assertEqual(preview["items"], [])
        self.assertEqual(len(preview["errors"]), 2)

    def test_malformed_bulk_arguments_do_not_mutate_catalog(self):
        identity = self.imported()
        for changes in [{}, {"authors": []}, {"authors": "作者"}, {"genre": " "}, {"genre": 3}, {"genre": "恋爱"},
                        {"original_title": "不允许的批量字段"}, {"age_rating": "R21"}, {"release_type": "不明"},
                        {"uncensored": "false"}, {"uncensored": True}, {"volume": "-1"}, {"language": "中文"},
                        {"characters": ["正常", "无\u0007效"]}, {"web": ["ftp://example.org"]}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.service.call("batch_edit", {"ids": [identity], "changes": changes})
        for params in [{"paths": [str(self.first)], "recursive": 1}, {"paths": []}, {"paths": ["relative"]}]:
            with self.subTest(params=params), self.assertRaises(ServiceError):
                self.service.call("prepare_import", params)
        preview = self.preview(self.first)
        with self.assertRaises(ServiceError):
            self.commit(preview, overwriteExisting="true")
        with self.assertRaises(ServiceError):
            self.commit(preview, defaults={"series": "导入仅支持作者和类型"})
        self.assertEqual(self.service.repo.get(identity).metadata.authors, [])


if __name__ == "__main__":
    unittest.main()
