"""v1.3 Book-only version classification and guarded differential writeback."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from comic_manage.komga import komga_metadata
from comic_manage.komga_manifest import MANAGED_KINDS, export_manifest
from comic_manage.komga_payloads import remote_book_payload, remote_series_payload
from comic_manage.komga_remote import metadata_from_remote
from comic_manage.metadata import Metadata, parse_xml, read_variant_fields, to_comicinfo
from comic_manage.repository import Repository
from comic_manage.sources import inspect_source
from tests import test_komga_remote as remote_fixture


class KomgaV13PayloadTests(unittest.TestCase):
    def metadata(self, **fields):
        return Metadata(**{"title": "原始标题", "authors": ["原文作者"], "genre": "漫画", "age_rating": "Everyone",
                           "tags": ["日常"], **fields})

    def current(self):
        return {"metadata": {"title": "原始标题", "titleLock": False, "number": "7", "numberSort": 7,
            "authorsLock": False, "authors": [{"name": "未知职责者", "role": "future-role"}],
            "notableEntities": [{"kind": "作品组", "name": "旧组"}, {"kind": "文字版本", "name": "有文字"},
                                {"kind": "team", "name": "保留外部团队"}, {"kind": "色彩", "name": "黑白"}],
            "tags": ["作品组:旧组", "文字版本:有文字", "无字版", "其他:保留", "NTR"]}}

    def test_exact_group_and_text_version_are_portable_tags_and_book_entities_only(self):
        metadata = self.metadata(work_group="Ａ Work  作者Ｂ", text_version="无文字", tags=["日常", "无字版"])
        entities = komga_metadata(metadata)["notableEntities"]
        self.assertIn({"kind": "作品组", "name": "Ａ Work  作者Ｂ"}, entities)
        self.assertIn({"kind": "文字版本", "name": "无文字"}, entities)
        self.assertIn("作品组:Ａ Work  作者Ｂ", metadata.all_tags)
        self.assertIn("文字版本:无文字", metadata.all_tags)
        self.assertIn("无字版", metadata.tags)
        self.assertNotIn("无字版", metadata.all_tags)
        xml = parse_xml(to_comicinfo(metadata, 1))
        self.assertIsNone(xml.find("Series"))
        self.assertIsNone(xml.find("Number"))
        self.assertIsNone(xml.find("WorkGroup"))
        self.assertIsNone(xml.find("TextVersion"))
        self.assertIn("作品组:Ａ Work  作者Ｂ", xml.findtext("Tags"))
        self.assertEqual((metadata.collection_mode, metadata.series, metadata.number), ("oneshot", "", ""))

    def test_single_selected_field_leaves_the_other_version_field_and_ordinary_tags_untouched(self):
        current = self.current()
        metadata = self.metadata(work_group="新组", text_version="无文字")
        payload, _ = remote_book_payload(metadata, current, {"work_group"})
        self.assertEqual(set(payload), {"notableEntities", "notableEntitiesLock", "tags", "tagsLock"})
        self.assertIn({"kind": "作品组", "name": "新组"}, payload["notableEntities"])
        self.assertIn({"kind": "文字版本", "name": "有文字"}, payload["notableEntities"])
        self.assertIn("文字版本:有文字", payload["tags"])
        self.assertIn("无字版", payload["tags"])
        self.assertIn("其他:保留", payload["tags"])
        self.assertIn({"kind": "team", "name": "保留外部团队"}, payload["notableEntities"])
        self.assertNotIn("number", payload)
        self.assertNotIn("authors", payload)
        self.assertEqual(remote_book_payload(metadata, current, set()), ({}, []))

    def test_textless_removes_only_its_derived_plain_duplicate(self):
        current = self.current()
        payload, _ = remote_book_payload(self.metadata(text_version="无文字"), current, {"text_version"})
        self.assertNotIn("无字版", payload["tags"])
        self.assertIn("文字版本:无文字", payload["tags"])
        self.assertIn("作品组:旧组", payload["tags"])
        self.assertIn("其他:保留", payload["tags"])
        payload, _ = remote_book_payload(self.metadata(text_version=""), current, {"text_version"})
        self.assertIn("无字版", payload["tags"])
        self.assertFalse(any(tag.startswith("文字版本:") for tag in payload["tags"]))

    def test_clear_fields_removes_only_their_entities_and_prefixes_without_fallback_resurrection(self):
        metadata = self.metadata(work_group="", text_version="", tags=["作品组:旧组", "文字版本:无文字", "无字版", "日常"])
        self.assertEqual((metadata.work_group, metadata.text_version), ("", ""))
        self.assertFalse(any(tag.startswith(("作品组:", "文字版本:")) for tag in metadata.all_tags))
        payload, _ = remote_book_payload(metadata, self.current(), {"work_group", "text_version"})
        self.assertFalse(any(item["kind"] in ("作品组", "文字版本") for item in payload["notableEntities"]))
        self.assertFalse(any(tag.startswith(("作品组:", "文字版本:")) for tag in payload["tags"]))
        self.assertIn("无字版", payload["tags"])
        self.assertIn("其他:保留", payload["tags"])
        series, _ = remote_series_payload([(metadata, {"dirty_fields": ["work_group", "text_version"]})], {"oneshot": True})
        self.assertEqual(series, {})

    def test_ordinary_tag_edit_does_not_resend_retained_unselected_prefix_evidence(self):
        metadata = self.metadata(work_group="旧组", text_version="无文字",
                                 tags=["作品组:本机旧证据", "文字版本:有文字", "无字版", "新增普通标签"])
        payload, _ = remote_book_payload(metadata, self.current(), {"tags"})
        self.assertEqual(set(payload), {"tags", "tagsLock"})
        self.assertIn("作品组:旧组", payload["tags"])
        self.assertIn("文字版本:有文字", payload["tags"])
        self.assertNotIn("作品组:本机旧证据", payload["tags"])
        self.assertNotIn("文字版本:无文字", payload["tags"])
        self.assertNotIn("无字版", payload["tags"])
        self.assertIn("新增普通标签", payload["tags"])

    def test_reading_uses_exact_entities_then_tags_and_never_unrelated_defaults(self):
        values, warnings = read_variant_fields([{"kind": "作品组", "name": "Ａ Work"}, {"kind": "文字版本", "name": "有文字"}],
                                              ["作品组:ａ work", "文字版本:有文字"])
        self.assertEqual(values, {"work_group": "Ａ Work", "text_version": "有文字"})
        self.assertEqual(warnings, [])
        fallback, _ = read_variant_fields([], ["作品组:Case-Sensitive", "文字版本:无文字"])
        self.assertEqual(fallback, {"work_group": "Case-Sensitive", "text_version": "无文字"})
        raw = {"oneshot": False, "name": "同名无字版.cbz", "metadata": {"title": "同名无字版", "tags": ["无字版"],
               "authors": [{"name": "作者", "role": "writer"}, {"name": "汉化组", "role": "translator"}],
               "notableEntities": [{"kind": "年龄分级", "name": "R18+"}, {"kind": "色彩", "name": "全彩"}]}}
        metadata, _ = metadata_from_remote(raw, {"metadata": {"title": "伪关联系列", "language": "zh-Hans"}})
        self.assertEqual((metadata.work_group, metadata.text_version), ("", ""))
        self.assertIn("无字版", metadata.tags)

    def test_conflicting_exact_names_or_invalid_versions_are_not_guessed(self):
        for entities in ([{"kind": "作品组", "name": "Work"}, {"kind": "作品组", "name": "work"}],
                         [{"kind": "作品组", "name": "A"}, {"kind": "作品组", "name": "Ａ"}],
                         [{"kind": "文字版本", "name": "有文字"}, {"kind": "文字版本", "name": "无文字"}],
                         [{"kind": "文字版本", "name": "未知旧值"}]):
            with self.subTest(entities=entities):
                values, warnings = read_variant_fields(entities, ["作品组:不能掩盖实体冲突", "文字版本:有文字"])
                target = "work_group" if entities[0]["kind"] == "作品组" else "text_version"
                self.assertEqual(values[target], "")
                self.assertTrue(warnings)

    def test_manifest_manages_both_new_kinds_without_changing_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "1.jpg").write_bytes(b"synthetic-page")
            snapshot = inspect_source(source)
            repo = Repository(root / "catalog.sqlite3")
            identity = repo.upsert_scan(snapshot)
            metadata = self.metadata(work_group="组名Ａ", text_version="无文字")
            repo.save(identity, metadata)
            repo.approve(identity, snapshot)
            output = root / "output/_oneshots/book.cbz"
            output.parent.mkdir(parents=True)
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("ComicInfo.xml", to_comicinfo(metadata, 1))
            repo.mark_packaged(identity, output, {})
            destination = root / "manifest.json"
            export_manifest(repo, [identity], destination)
            document = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(document["schemaVersion"], "comic-manage-komga/1.0")
            self.assertTrue({"作品组", "文字版本"}.issubset(MANAGED_KINDS))
            self.assertTrue({"作品组", "文字版本"}.issubset(document["managedEntityKinds"]))
            self.assertIn({"kind": "作品组", "name": "组名Ａ"}, document["books"][0]["notableEntities"])
            self.assertIn({"kind": "文字版本", "name": "无文字"}, document["books"][0]["notableEntities"])
            self.assertEqual(document["series"], [])


class KomgaV13HttpTests(unittest.TestCase):
    setUp = remote_fixture.KomgaRemoteTests.setUp
    tearDown = remote_fixture.KomgaRemoteTests.tearDown
    patches = remote_fixture.KomgaRemoteTests.patches
    seed = remote_fixture.KomgaRemoteTests.seed
    call = remote_fixture.KomgaRemoteTests.call
    records = remote_fixture.KomgaRemoteTests.records
    approve = remote_fixture.KomgaRemoteTests.approve

    def seed_one_shot(self, book_id, series_id, *, textless_tag=False):
        self.seed(book_id, oneshot=True)
        book = self.remote_books[book_id]
        series = self.remote_series.pop("s")
        book["seriesId"] = series_id
        book["metadata"]["tags"] = ["日常"] + (["无字版"] if textless_tag else [])
        series.update(id=series_id, booksCount=1)
        self.remote_series[series_id] = series

    def test_same_group_versions_bulk_review_write_two_books_without_series_merges_or_progress_changes(self):
        self.seed_one_shot("b1", "s1")
        self.seed_one_shot("b2", "s2", textless_tag=True)
        original_books, original_series = deepcopy(self.remote_books), deepcopy(self.remote_series)
        self.call("komga_pull")
        ids = [self.records()[book_id].id for book_id in ("b1", "b2")]
        self.service.call("batch_edit", {"ids": ids, "changes": {"work_group": "Ａ Work  作者Ｂ"}})
        self.service.call("save", {"id": ids[0], "metadata": {"text_version": "有文字"}})
        self.service.call("save", {"id": ids[1], "metadata": {"text_version": "无文字"}})
        preview = self.call("prepare_review_sync", ids=ids)
        self.assertEqual((preview["ready"], preview["blocked"]), (2, 0))
        self.assertEqual(self.patches("books"), [])
        result = self.call("commit_review_sync", token=preview["token"])
        self.assertEqual((result["reviewed"], result["sync"]["updated"], result["sync"]["seriesUpdated"]), (2, 2, 0))
        self.assertEqual(len(self.patches("books")), 2)
        self.assertEqual(self.patches("series"), [])
        self.assertEqual(self.remote_series, original_series)
        for book_id, text in (("b1", "有文字"), ("b2", "无文字")):
            book, before = self.remote_books[book_id], original_books[book_id]
            self.assertEqual((book["id"], book["seriesId"], book["metadata"]["number"], book["metadata"]["numberSort"]),
                             (before["id"], before["seriesId"], before["metadata"]["number"], before["metadata"]["numberSort"]))
            self.assertEqual(book["readProgress"], before["readProgress"])
            self.assertEqual(book["metadata"]["authors"], before["metadata"]["authors"])
            self.assertEqual(book["metadata"]["links"], before["metadata"]["links"])
            self.assertIn({"kind": "作品组", "name": "Ａ Work  作者Ｂ"}, book["metadata"]["notableEntities"])
            self.assertIn({"kind": "文字版本", "name": text}, book["metadata"]["notableEntities"])
            record = self.records()[book_id]
            self.assertEqual((record.metadata.series, record.metadata.number), ("", ""))
            self.assertFalse(record.remote["hasLocalChanges"])
        self.assertNotIn("无字版", self.remote_books["b2"]["metadata"]["tags"])
        self.assertIn("无字版", self.records()["b2"].metadata.tags)
        preview = self.call("prepare_review_sync", ids=ids)
        result = self.call("commit_review_sync", token=preview["token"])
        self.assertEqual(result["sync"]["unchanged"], 2)
        self.assertEqual(len(self.patches("books")), 2)
        self.assertIn("无字版", self.records()["b2"].metadata.tags)

    def test_clear_both_fields_does_not_revive_from_old_tags_or_snapshot(self):
        self.seed_one_shot("b1", "s1", textless_tag=True)
        raw = self.remote_books["b1"]["metadata"]
        raw["notableEntities"].extend([{"kind": "作品组", "name": "原始组"}, {"kind": "文字版本", "name": "无文字"}])
        raw["tags"].extend(["作品组:原始组", "文字版本:无文字"])
        before = deepcopy(raw)
        self.call("komga_pull")
        record = self.records()["b1"]
        self.approve(record.id, work_group="", text_version="")
        result = self.call("komga_sync", ids=[record.id])
        self.assertEqual(result["updated"], 1)
        self.assertFalse(any(item["kind"] in ("作品组", "文字版本") for item in raw["notableEntities"]))
        self.assertFalse(any(tag.startswith(("作品组:", "文字版本:")) for tag in raw["tags"]))
        self.assertIn("无字版", raw["tags"])
        self.assertEqual(raw["number"], before["number"])
        self.assertEqual(self.patches("series"), [])
        self.call("komga_pull")
        current = self.records()["b1"]
        self.assertEqual((current.metadata.work_group, current.metadata.text_version), ("", ""))
        self.approve(record.id)
        result = self.call("komga_sync", ids=[record.id])
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(len(self.patches("books")), 1)

    def test_mixed_case_native_tag_normalization_preserves_exact_entity_group_and_no_dirty_loop(self):
        self.seed_one_shot("b1", "s1", textless_tag=True)
        self.call("komga_pull")
        record = self.records()["b1"]
        self.approve(record.id, work_group="CaseＡ Group", text_version="无文字", tags=["NTR", "无字版"])
        def lower_tags(path):
            if "/books/" in path:
                raw = self.remote_books["b1"]["metadata"]
                raw["tags"] = [tag.lower() for tag in raw["tags"]]
        self.after_patch = lower_tags
        self.call("komga_sync", ids=[record.id])
        current = self.records()["b1"]
        self.assertFalse(current.remote["hasLocalChanges"])
        self.assertFalse(current.remote["conflict"])
        self.assertEqual(current.metadata.work_group, "CaseＡ Group")
        self.assertIn("无字版", current.metadata.tags)
        self.assertNotIn("无字版", self.remote_books["b1"]["metadata"]["tags"])
        self.approve(record.id)
        self.call("komga_sync", ids=[record.id])
        self.assertEqual(len(self.patches("books")), 1)
        self.approve(record.id, text_version="")
        self.call("komga_sync", ids=[record.id])
        current = self.records()["b1"]
        self.assertEqual(current.metadata.text_version, "")
        self.assertIn("无字版", current.metadata.tags)
        self.assertNotIn("无字版", self.remote_books["b1"]["metadata"]["tags"])
        self.approve(record.id)
        self.call("komga_sync", ids=[record.id])
        self.assertEqual(len(self.patches("books")), 2)
        self.assertEqual(self.records()["b1"].metadata.text_version, "")

    def test_v12_remote_unknown_kinds_migrate_both_sides_without_phantom_patches(self):
        self.seed_one_shot("b1", "s1")
        raw = self.remote_books["b1"]["metadata"]
        raw["notableEntities"].extend([{"kind": "作品组", "name": "ExactＡ Group"}, {"kind": "文字版本", "name": "有文字"}])
        raw["tags"].extend(["作品组:exactａ group", "文字版本:有文字"])
        self.call("komga_pull")
        record = self.records()["b1"]
        self.approve(record.id)
        source = self.service.repo.get_komga_source(record.id)
        metadata, base = record.metadata.to_dict(), deepcopy(source["base_metadata"])
        for value in (metadata, base):
            value.pop("work_group")
            value.pop("text_version")
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=? WHERE id=?", (json.dumps(metadata), record.id))
            db.execute("UPDATE komga_sources SET base_metadata=? WHERE comic_id=?", (json.dumps(base), record.id))
            frozen = dict(db.execute("SELECT * FROM komga_sources WHERE comic_id=?", (record.id,)).fetchone())
        self.service.repo = Repository(self.service.repo.path)
        self.service.reviews.repo = self.service.repo
        current = self.records()["b1"]
        after = self.service.repo.get_komga_source(record.id)
        self.assertEqual(current.status, "approved")
        self.assertEqual((current.metadata.work_group, current.metadata.text_version), ("ExactＡ Group", "有文字"))
        self.assertEqual((after["base_metadata"]["work_group"], after["base_metadata"]["text_version"]), ("ExactＡ Group", "有文字"))
        self.assertEqual(after["dirty_fields"], [])
        with self.service.repo.connect() as db:
            stored = dict(db.execute("SELECT * FROM komga_sources WHERE comic_id=?", (record.id,)).fetchone())
        for field in ("snapshot", "observed_snapshot", "dirty_fields", "pending_sync", "conflict", "synced_at"):
            self.assertEqual(stored[field], frozen[field], field)
        result = self.call("komga_sync", ids=[record.id])
        self.assertEqual((result["updated"], result["unchanged"]), (0, 1))
        self.assertEqual((self.patches("books"), self.patches("series")), ([], []))

    def test_existing_tag_draft_retains_variant_evidence_without_resending_it_or_staying_dirty(self):
        self.seed_one_shot("b1", "s1")
        raw = self.remote_books["b1"]["metadata"]
        raw["notableEntities"].append({"kind": "作品组", "name": "RemoteＡ"})
        raw["tags"].append("作品组:remoteａ")
        self.call("komga_pull")
        record = self.records()["b1"]
        source = self.service.repo.get_komga_source(record.id)
        metadata, base = record.metadata.to_dict(), deepcopy(source["base_metadata"])
        for value in (metadata, base):
            value.pop("work_group")
            value.pop("text_version")
        metadata["tags"] = ["新普通标签", "作品组:LocalＢ"]
        base["tags"] = ["日常", "作品组:remoteａ"]
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=? WHERE id=?", (json.dumps(metadata), record.id))
            db.execute("UPDATE komga_sources SET base_metadata=?,dirty_fields='[\"tags\"]' WHERE comic_id=?", (json.dumps(base), record.id))
        self.service.repo = Repository(self.service.repo.path)
        self.service.reviews.repo = self.service.repo
        current = self.records()["b1"]
        self.assertEqual(current.metadata.work_group, "RemoteＡ")
        self.assertIn("作品组:LocalＢ", current.metadata.tags)
        self.assertEqual(current.remote["dirtyFields"], ["tags"])
        self.approve(record.id)
        self.call("komga_sync", ids=[record.id])
        self.assertEqual(set(self.patches("books")[0]), {"tags", "tagsLock"})
        self.assertIn("作品组:remoteａ", raw["tags"])
        self.assertNotIn("作品组:LocalＢ", raw["tags"])
        self.assertIn({"kind": "作品组", "name": "RemoteＡ"}, raw["notableEntities"])
        current = self.records()["b1"]
        self.assertFalse(current.remote["hasLocalChanges"])
        self.assertIn("作品组:LocalＢ", current.metadata.tags)
        self.approve(record.id)
        self.call("komga_sync", ids=[record.id])
        self.assertEqual(len(self.patches("books")), 1)
        self.assertIn("作品组:LocalＢ", self.records()["b1"].metadata.tags)


if __name__ == "__main__":
    unittest.main()
