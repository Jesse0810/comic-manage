"""v1.2 portable classifications and differential Komga writeback contracts."""
from copy import deepcopy
import json
import unittest

from comic_manage.komga import komga_metadata, komga_warnings
from comic_manage.komga_payloads import book_payload, remote_book_payload, remote_series_payload, series_payload
from comic_manage.komga_remote import metadata_from_remote, snapshot_fingerprint, validate_changes
from comic_manage.metadata import Metadata
from comic_manage.repository import Repository
from tests import test_komga_remote as remote_fixture


class V12PayloadTests(unittest.TestCase):
    def metadata(self, **changes):
        return Metadata(**{"title": "原题", "authors": ["作者"], "genre": "漫画", "age_rating": "Everyone",
                           "tags": ["日常"], **changes})

    def test_new_classifications_have_portable_tags_and_book_entities(self):
        metadata = self.metadata(color="部分彩色", release_type="个人独立发布", publication_format="合集",
                                 release_methods=["会员订阅", "公开发布"], release_platforms=["Fantia", "pixivFANBOX", "自建站"])
        entities = komga_metadata(metadata)["notableEntities"]
        for kind, names in (("发布方式", metadata.release_methods), ("发布平台", metadata.release_platforms), ("色彩", ["部分彩色"])):
            for name in names:
                self.assertIn({"kind": kind, "name": name}, entities)
                self.assertIn(f"{kind}:{name}", metadata.all_tags)
        self.assertIn({"kind": "内容类型", "name": "漫画"}, entities)
        self.assertIn({"kind": "年龄分级", "name": "Everyone"}, entities)
        self.assertFalse(any(tag.startswith(("内容类型:", "年龄分级:", "汉化状态:")) for tag in metadata.all_tags))

    def test_each_platform_direction_is_native_without_conflating_manga(self):
        for direction in ("LEFT_TO_RIGHT", "RIGHT_TO_LEFT", "VERTICAL", "WEBTOON"):
            with self.subTest(direction=direction):
                metadata = self.metadata(collection_mode="series", series="系列", number="1", manga="Yes", reading_direction=direction)
                payload, _ = series_payload([metadata], {"oneshot": False, "metadata": {}})
                self.assertEqual(payload["readingDirection"], direction)
                self.assertEqual(metadata.manga, "Yes")
                remote, _ = remote_series_payload([(metadata, {"dirty_fields": ["reading_direction"]})], {"oneshot": False})
                self.assertEqual(remote, {"readingDirection": direction, "readingDirectionLock": True})
        metadata.reading_direction = ""
        payload, _ = remote_series_payload([(metadata, {"dirty_fields": ["reading_direction"]})], {"oneshot": False})
        self.assertIsNone(payload["readingDirection"])
        metadata.manga = "YesAndRightToLeft"
        payload, _ = series_payload([metadata], {"oneshot": False, "metadata": {}})
        self.assertIsNone(payload["readingDirection"])

    def test_optional_local_number_does_not_attempt_an_invalid_api_clear(self):
        metadata = self.metadata(collection_mode="series", series="系列", number="")
        payload, warnings = book_payload(metadata, {"metadata": {}})
        self.assertNotIn("number", payload)
        self.assertNotIn("numberSort", payload)
        self.assertTrue(any("不接受空册号" in warning for warning in warnings))
        with self.assertRaisesRegex(ValueError, "空值"):
            validate_changes(metadata, {"number"})

    def test_one_new_field_edit_retains_other_entities_tags_roles_and_locks(self):
        current = {"metadata": {"titleLock": False, "authorsLock": False,
                    "notableEntities": [{"kind": "发布平台", "name": "旧平台"}, {"kind": "team", "name": "保留团队"},
                                        {"kind": "色彩", "name": "黑白"}, {"kind": "character", "name": "角色"}],
                    "tags": ["发布平台:旧平台", "色彩:黑白", "原作:保留IP", "其他:保留"]}}
        payload, _ = remote_book_payload(self.metadata(release_platforms=["Fantia", "自建站"]), current, {"release_platforms"})
        self.assertEqual(set(payload), {"notableEntities", "notableEntitiesLock", "tags", "tagsLock"})
        self.assertIn({"kind": "team", "name": "保留团队"}, payload["notableEntities"])
        self.assertIn({"kind": "色彩", "name": "黑白"}, payload["notableEntities"])
        self.assertNotIn("发布平台:旧平台", payload["tags"])
        self.assertIn("发布平台:Fantia", payload["tags"])
        self.assertIn("原作:保留IP", payload["tags"])
        self.assertEqual(remote_book_payload(self.metadata(), current, set()), ({}, []))

    def test_read_book_facts_and_shared_direction_separately(self):
        book = {"metadata": {"title": "原题", "authors": [{"name": "作者", "role": "writer"}], "tags": ["日常"],
                "notableEntities": [{"kind": "色彩", "name": "部分彩色"}, {"kind": "发布方式", "name": "会员订阅"},
                                    {"kind": "发布平台", "name": "Fantia"}]}}
        series = {"metadata": {"title": "系列", "genres": ["漫画"], "ageRating": 18, "readingDirection": "WEBTOON"}}
        metadata, warnings = metadata_from_remote(book, series)
        self.assertEqual(metadata.color, "部分彩色")
        self.assertEqual(metadata.release_methods, ["会员订阅"])
        self.assertEqual(metadata.release_platforms, ["Fantia"])
        self.assertEqual(metadata.reading_direction, "WEBTOON")
        self.assertEqual(metadata.manga, "Unknown")
        self.assertEqual((metadata.genre, metadata.age_rating), ("", "Unknown"))
        self.assertTrue(warnings)
        for field, value in (("manga", "Yes"), ("volume", "2")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_changes(self.metadata(**{field: value}), {field})

    def test_remote_legacy_vocabulary_is_visible_until_explicitly_replaced(self):
        book = {"metadata": {"title": "旧资料", "authors": [{"name": "作者", "role": "writer"}], "tags": ["日常"],
                "notableEntities": [{"kind": "内容类型", "name": "同人志"}, {"kind": "发行性质", "name": "同人"},
                                    {"kind": "年龄分级", "name": "Everyone"}, {"kind": "色彩", "name": "彩色/非黑白"}]}}
        metadata, warnings = metadata_from_remote(book, {"metadata": {"title": "系列"}})
        self.assertEqual((metadata.genre, metadata.release_type, metadata.color), ("同人志", "同人", ""))
        self.assertTrue(any("旧词" in warning for warning in warnings))
        self.assertTrue(any("全彩与部分彩色" in warning for warning in warnings))
        with self.assertRaises(ValueError):
            metadata.validate_review()
        self.assertEqual(remote_book_payload(metadata, book, set()), ({}, []))
        metadata.genre, metadata.release_type = "漫画", "同人发行"
        payload, _ = remote_book_payload(metadata, book, {"genre", "release_type"})
        self.assertIn({"kind": "内容类型", "name": "漫画"}, payload["notableEntities"])
        self.assertIn({"kind": "发行性质", "name": "同人发行"}, payload["notableEntities"])
        self.assertNotIn({"kind": "内容类型", "name": "同人志"}, payload["notableEntities"])
        self.assertIn({"kind": "色彩", "name": "彩色/非黑白"}, payload["notableEntities"])

    def test_snapshot_matches_server_tag_normalization_and_keeps_actual_changes(self):
        original = {"book": {"metadata": {"title": " 原题 ", "tags": ["发布平台:pixivFANBOX", "NTR"]}}, "series": {"metadata": {"language": "zh-hans", "genres": ["CG集"]}}}
        normalized = {"book": {"metadata": {"title": "原题", "tags": ["ntr", "发布平台:pixivfanbox"]}}, "series": {"metadata": {"language": "zh-Hans", "genres": ["cg集"]}}}
        self.assertEqual(snapshot_fingerprint(original), snapshot_fingerprint(normalized))
        normalized["book"]["metadata"]["title"] = "别人修改的标题"
        self.assertNotEqual(snapshot_fingerprint(original), snapshot_fingerprint(normalized))


class V12RemoteHttpTests(unittest.TestCase):
    setUp = remote_fixture.KomgaRemoteTests.setUp
    tearDown = remote_fixture.KomgaRemoteTests.tearDown
    patches = remote_fixture.KomgaRemoteTests.patches
    seed = remote_fixture.KomgaRemoteTests.seed
    call = remote_fixture.KomgaRemoteTests.call
    records = remote_fixture.KomgaRemoteTests.records
    approve = remote_fixture.KomgaRemoteTests.approve

    def test_missing_required_book_metadata_cannot_be_approved_from_shared_values(self):
        for missing in ("authors", "genre", "age_rating", "tags"):
            with self.subTest(missing=missing):
                self.seed()
                raw = self.remote_books["b1"]["metadata"]
                if missing == "authors":
                    raw["authors"] = []
                elif missing == "genre":
                    raw["notableEntities"] = [item for item in raw["notableEntities"] if item["kind"] != "内容类型"]
                elif missing == "age_rating":
                    raw["notableEntities"] = [item for item in raw["notableEntities"] if item["kind"] != "年龄分级"]
                else:
                    raw["tags"] = []
                    raw["notableEntities"] = [item for item in raw["notableEntities"] if item["kind"] in ("内容类型", "年龄分级")]
                self.call("komga_pull")
                record = self.records()["b1"]
                with self.assertRaises(ValueError):
                    self.approve(record.id)
                self.assertEqual(self.service.repo.get(record.id).status, "draft")
                self.assertEqual(self.patches("books"), [])

    def test_v12_batch_fields_and_direction_roundtrip_without_touching_other_metadata(self):
        self.seed("b1")
        self.seed("b2")
        self.call("komga_pull")
        identities = [comic.id for comic in self.records().values()]
        changes = {"color": "部分彩色", "release_methods": ["会员订阅"], "release_platforms": ["Fantia", "pixivFANBOX"], "reading_direction": "VERTICAL"}
        self.service.call("batch_edit", {"ids": identities, "changes": changes})
        for identity in identities:
            self.approve(identity)
        result = self.call("komga_sync", ids=identities)
        self.assertEqual((result["updated"], result["seriesUpdated"]), (2, 1))
        self.assertEqual(self.patches("series")[0], {"readingDirection": "VERTICAL", "readingDirectionLock": True})
        for record in self.records().values():
            self.assertFalse(record.remote["hasLocalChanges"])
            self.assertEqual(record.metadata.color, "部分彩色")
        for payload in self.patches("books"):
            self.assertNotIn("title", payload)
            self.assertFalse(any(tag.startswith(("内容类型:", "年龄分级:")) for tag in payload["tags"]))
            self.assertIn({"kind": "发布平台", "name": "pixivFANBOX"}, payload["notableEntities"])

    def test_server_native_tag_lowercase_does_not_leave_false_dirty_or_conflict(self):
        self.seed()
        self.call("komga_pull")
        record = self.records()["b1"]
        self.approve(record.id, tags=["NTR"])
        def normalize(path):
            if "/books/" in path:
                raw = self.remote_books["b1"]["metadata"]
                raw["tags"] = [tag.lower() for tag in raw["tags"]]
        self.after_patch = normalize
        self.call("komga_sync", ids=[record.id])
        current = self.service.repo.get(record.id)
        self.assertFalse(current.remote["conflict"])
        self.assertFalse(current.remote["hasLocalChanges"])
        self.approve(record.id)
        before = len(self.patches("books"))
        self.call("komga_sync", ids=[record.id])
        self.assertEqual(len(self.patches("books")), before)

    def test_legacy_remote_migration_keeps_snapshot_dirty_intent_and_exact_platform_direction(self):
        self.seed()
        self.remote_books["b1"]["metadata"]["notableEntities"].extend([
            {"kind": "发布方式", "name": "会员订阅"}, {"kind": "发布平台", "name": "pixivFANBOX"},
            {"kind": "色彩", "name": "部分彩色"}])
        self.call("komga_pull")
        record = self.records()["b1"]
        self.service.call("save", {"id": record.id, "metadata": {"summary": "保留本机编辑"}})
        source = self.service.repo.get_komga_source(record.id)
        previous, base = self.service.repo.get(record.id).metadata.to_dict(), deepcopy(source["base_metadata"])
        for value in (previous, base):
            for field in ("color", "release_methods", "release_platforms", "reading_direction"):
                value.pop(field)
        with self.service.repo.connect() as db:
            raw_snapshot = db.execute("SELECT snapshot FROM komga_sources WHERE comic_id=?", (record.id,)).fetchone()[0]
            db.execute("UPDATE comics SET metadata=?,fingerprint='old-fingerprint-algorithm' WHERE id=?", (json.dumps(previous), record.id))
            db.execute("UPDATE komga_sources SET base_metadata=? WHERE comic_id=?", (json.dumps(base), record.id))
        self.service.repo = Repository(self.service.repo.path)
        current = self.service.repo.get(record.id)
        self.assertEqual(current.metadata.summary, "保留本机编辑")
        self.assertEqual(current.metadata.reading_direction, "WEBTOON")
        self.assertEqual(current.metadata.release_platforms, ["pixivFANBOX"])
        with self.service.repo.connect() as db:
            self.assertEqual(db.execute("SELECT snapshot FROM komga_sources WHERE comic_id=?", (record.id,)).fetchone()[0], raw_snapshot)
        self.assertEqual(self.service.repo.get_komga_source(record.id)["dirty_fields"], ["summary"])
        self.call("komga_pull")
        self.assertFalse(self.service.repo.get(record.id).remote["conflict"])
        self.approve(record.id)
        self.call("komga_sync", ids=[record.id])
        self.assertEqual(self.patches("books"), [{"summary": "保留本机编辑", "summaryLock": True}])
        self.assertEqual(self.patches("series"), [])


if __name__ == "__main__":
    unittest.main()
