"""Optional work groups and text variants without inferred series or review churn."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from comic_manage.filename_parser import parse_filename
from comic_manage.metadata import Metadata, V13_FIELDS, from_comicinfo, parse_xml, read_variant_fields, to_comicinfo
from comic_manage.repository import Repository
from comic_manage.sources import inspect_source


def valid(**fields):
    return Metadata(**{"title": "原文タイトル", "authors": ["原作者"], "genre": "漫画", "age_rating": "Everyone", "tags": ["日常"], **fields})


class MetadataV13Tests(unittest.TestCase):
    def test_optional_fields_never_derive_from_names_or_other_metadata(self):
        value = valid(title="同名", series="同名", number="2", count="3", volume="4", translator="翻译组", language="zh-Hans", color="全彩")
        before = value.to_dict()
        self.assertEqual((value.work_group, value.text_version), ("", ""))
        value.work_group, value.text_version = "ＡＢＣ 原作 Title", "无文字"
        value.normalize()
        for field, previous in before.items():
            if field not in V13_FIELDS:
                self.assertEqual(value.to_dict()[field], previous, field)
        root = parse_xml(to_comicinfo(value, 1))
        self.assertEqual((root.findtext("Series"), root.findtext("Number"), root.findtext("Count"), root.findtext("Volume")), ("同名", "2", "3", "4"))
        self.assertEqual((root.findtext("Translator"), root.findtext("LanguageISO"), root.findtext("AgeRating")), ("翻译组", "zh-Hans", "Everyone"))
        self.assertIsNone(root.find("WorkGroup"))
        self.assertIsNone(root.find("TextVersion"))
        self.assertEqual(root.findtext("Tags"), "作品组:ＡＢＣ 原作 Title, 文字版本:无文字, 色彩:全彩, 日常")
        restored = from_comicinfo(to_comicinfo(value, 1), "fallback")
        self.assertEqual((restored.work_group, restored.text_version), ("ＡＢＣ 原作 Title", "无文字"))
        self.assertEqual(restored.all_tags, value.all_tags)

    def test_versions_do_not_turn_independent_books_into_numbered_series(self):
        for version in ("", "有文字", "无文字"):
            value = valid(work_group="稳定原名 [作者A]", text_version=version)
            root = parse_xml(to_comicinfo(value, 1))
            for field in ("Series", "Number", "Count", "Volume"):
                self.assertIsNone(root.find(field), field)
            self.assertEqual(value.collection_mode, "oneshot")
            self.assertEqual(value.title, "原文タイトル")

    def test_no_text_alias_is_only_deduplicated_after_explicit_confirmation(self):
        for version in ("", "有文字", "无文字"):
            value = valid(text_version=version, tags=["无字版", "日常"])
            self.assertEqual(value.tags, ["无字版", "日常"])
            self.assertEqual("无字版" in value.all_tags, version != "无文字")
        inferred = Metadata.from_dict({"tags": ["无字版"]})
        self.assertEqual(inferred.text_version, "")

    def test_clearing_fields_never_reappears_from_original_tags_or_xml(self):
        raw = '<ComicInfo><Title>A</Title><Writer>B</Writer><Genre>漫画</Genre><AgeRating>Everyone</AgeRating><Tags>作品组:旧组,文字版本:无文字,日常</Tags></ComicInfo>'
        imported = from_comicinfo(raw.encode(), "fallback")
        payload = imported.to_dict()
        payload.update(work_group="", text_version="")
        for _ in range(3):
            imported = Metadata.from_dict(payload)
            self.assertEqual((imported.work_group, imported.text_version), ("", ""))
            self.assertEqual(imported.all_tags, ["日常"])
            payload = imported.to_dict()
        root = parse_xml(to_comicinfo(imported, 1))
        self.assertEqual(root.findtext("Tags"), "日常")
        self.assertEqual(imported.original_xml, raw)

    def test_explicit_filename_labels_only_and_exact_work_group_names(self):
        value, warnings = parse_filename("[作者] 原名 [作品组:ＡＢＣ 原作] [无字版]")
        self.assertEqual((value.title, value.work_group, value.text_version), ("原名", "ＡＢＣ 原作", "无文字"))
        self.assertEqual((value.series, value.number, value.count, value.collection_mode), ("", "", "", "oneshot"))
        self.assertEqual(value.authors, ["作者"])
        self.assertTrue(all("作品组" not in warning and "文字版本" not in warning for warning in warnings))
        for label, expected in (("有文字", "有文字"), ("无文字", "无文字"), ("文字版本:无文字", "无文字"), ("文字版本：有文字", "有文字")):
            parsed, _ = parse_filename(f"原名 [{label}]")
            self.assertEqual(parsed.text_version, expected)
        parsed, _ = parse_filename("没有版本需求的无字版原名")
        self.assertEqual((parsed.work_group, parsed.text_version), ("", ""))
        conflict, warnings = parse_filename("原名 [作品组:ABC] [作品组:abc] [有文字] [无字版]")
        self.assertEqual((conflict.work_group, conflict.text_version), ("", ""))
        self.assertEqual(set(conflict.tags), {"作品组:ABC", "作品组:abc", "文字版本:有文字", "文字版本:无文字"})
        self.assertTrue(warnings)

    def test_entity_reader_preserves_exact_keys_and_does_not_hide_invalid_entities(self):
        values, warnings = read_variant_fields([{"kind": "作品组", "name": "ABC Ａ"}, {"kind": "文字版本", "name": "无文字"}], ["作品组:abc ａ", "文字版本:无文字"])
        self.assertEqual(values, {"work_group": "ABC Ａ", "text_version": "无文字"})
        self.assertEqual(warnings, [])
        for entities, tags in (([{"kind": "作品组", "name": "ABC"}, {"kind": "作品组", "name": "abc"}], []),
                               ([{"kind": "作品组", "name": "ABC"}, {"kind": "作品组", "name": "ＡＢＣ"}], []),
                               ([{"kind": "文字版本", "name": "未知"}], ["文字版本:有文字"]),
                               ([{"kind": "文字版本", "name": ""}], ["文字版本:有文字"])):
            values, warnings = read_variant_fields(entities, tags)
            self.assertTrue(warnings)
            self.assertEqual(values["work_group"] if entities[0]["kind"] == "作品组" else values["text_version"], "")

    def test_new_field_validation_and_required_v12_facts_still_apply(self):
        trimmed = valid(work_group="  Group  ＡBC  ", text_version=" 有文字 ")
        self.assertEqual((trimmed.work_group, trimmed.text_version), ("Group  ＡBC", "有文字"))
        valid(work_group="组名:保留冒号", text_version="有文字").validate_review()
        for fields in ({"text_version": "无字版"}, {"text_version": "Unknown"}, {"work_group": "A,B"}, {"work_group": "A\x00B"}, {"genre": ""}, {"age_rating": "Unknown"}, {"authors": []}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                valid(**fields).validate_review()


class CatalogV13Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "catalog.sqlite3"
        self.repo = Repository(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def local(self, name="作品"):
        source = self.root / name
        source.mkdir()
        (source / "1.jpg").write_bytes(b"original image")
        return self.repo.upsert_scan(inspect_source(source))

    def remote(self, identity="remote"):
        source = {"source": f"komga://scope/{identity}", "server_url": "http://localhost:25600", "library_id": "lib", "library_name": "书库", "book_id": identity, "series_id": "series", "remote_path": f"/books/series/{identity}.cbz", "scope": "scope"}
        snapshot = {"book": {"id": identity, "libraryId": "lib", "seriesId": "series", "url": source["remote_path"], "oneshot": False, "media": {"pagesCount": 1}, "metadata": {"title": "原文", "tags": ["日常"], "notableEntities": []}},
                    "series": {"id": "series", "libraryId": "lib", "url": "/books/series", "oneshot": False, "booksCount": 1, "metadata": {"title": "真实系列", "readingDirection": "WEBTOON"}}}
        value = valid(series="真实系列", summary="baseline")
        self.repo.upsert_komga(identity, source, value, snapshot, "preserved-fingerprint", 1, "")
        return value, snapshot

    def old_values(self, value):
        raw = value.to_dict()
        for field in V13_FIELDS:
            raw.pop(field)
        return raw

    def test_additive_upgrade_backs_up_once_and_preserves_review_packaging_and_raw_values(self):
        identities = [self.local("已审核"), self.local("已生成")]
        before = {}
        for identity, status in zip(identities, ("approved", "packaged")):
            previous = self.old_values(valid(series="真实系列", number="2", count="3", volume="4"))
            encoded = json.dumps(previous, ensure_ascii=False, indent=1)
            with self.repo.connect() as db:
                db.execute("UPDATE comics SET metadata=?,status=?,output='existing.cbz',warning='原提示',updated_at='2026-01-01',fingerprint='unchanged' WHERE id=?", (encoded, status, identity))
            before[identity] = (previous, encoded, status)
        migrated = Repository(self.path)
        backups = list(self.root.glob("*.before-metadata-v1_3-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        for identity in identities:
            previous, encoded, status = before[identity]
            with migrated.connect() as db:
                row = dict(db.execute("SELECT * FROM comics WHERE id=?", (identity,)).fetchone())
            self.assertEqual(json.loads(row["metadata"]), {**previous, "work_group": "", "text_version": ""})
            self.assertEqual((row["status"], row["output"], row["warning"], row["updated_at"], row["fingerprint"]), (status, "existing.cbz", "原提示", "2026-01-01", "unchanged"))
            with closing(sqlite3.connect(backups[0])) as db:
                self.assertEqual(db.execute("SELECT metadata,status FROM comics WHERE id=?", (identity,)).fetchone(), (encoded, status))
        Repository(self.path)
        self.assertEqual(len(list(self.root.glob("*.before-metadata-v1_3-*.sqlite3"))), 1)

    def test_clear_and_batch_only_change_checked_fields_even_with_legacy_tag_evidence(self):
        identities = [self.local("甲"), self.local("乙")]
        for identity in identities:
            self.repo.save(identity, valid(work_group="旧作品组", text_version="有文字", series="真实系列", number="3", tags=["作品组:旧作品组", "文字版本:有文字", "无字版", "日常"]))
        original = {identity: self.repo.get(identity).metadata.to_dict() for identity in identities}
        self.assertEqual(self.repo.batch_edit(identities, {"text_version": "无文字"}), 2)
        for identity in identities:
            after = self.repo.get(identity).metadata.to_dict()
            self.assertEqual(after, {**original[identity], "text_version": "无文字"})
            self.assertNotIn("无字版", self.repo.get(identity).metadata.all_tags)
        self.repo.batch_edit(identities, {"text_version": "", "work_group": ""})
        for identity in identities:
            after = self.repo.get(identity).metadata
            self.assertEqual((after.work_group, after.text_version), ("", ""))
            self.assertEqual(after.tags, original[identity]["tags"])
            self.assertEqual(after.all_tags, ["无字版", "日常"])
        with self.assertRaises(ValueError):
            self.repo.batch_edit(identities, {"text_version": "无字版"})
        self.assertTrue(all(self.repo.get(identity).metadata.text_version == "" for identity in identities))

    def test_remote_empty_addition_preserves_snapshot_dirty_pending_flags_and_legacy_words(self):
        value, snapshot = self.remote()
        baseline = self.old_values(value)
        baseline.update(genre="同人志", release_type="同人", publication_format="独立短篇")
        edited = {**baseline, "summary": "未同步草稿"}
        literal_snapshot = json.dumps(snapshot, ensure_ascii=False, indent=2)
        with self.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=?,status='approved',output='keep.cbz' WHERE id='remote'", (json.dumps(edited),))
            db.execute("UPDATE komga_sources SET base_metadata=?,snapshot=?,observed_snapshot='{}',dirty_fields='[ \"summary\" ]',pending_sync=1,conflict=1,unavailable=1,synced_at='old-sync' WHERE comic_id='remote'", (json.dumps(baseline), literal_snapshot))
            registry_before = dict(db.execute("SELECT * FROM komga_sources WHERE comic_id='remote'").fetchone())
        migrated = Repository(self.path)
        comic = migrated.get("remote")
        self.assertEqual((comic.status, comic.fingerprint, comic.output), ("approved", "preserved-fingerprint", "keep.cbz"))
        with migrated.connect() as db:
            registry_after = dict(db.execute("SELECT * FROM komga_sources WHERE comic_id='remote'").fetchone())
            stored = json.loads(db.execute("SELECT metadata FROM comics WHERE id='remote'").fetchone()[0])
        self.assertEqual(stored, {**edited, "work_group": "", "text_version": ""})
        self.assertEqual(json.loads(registry_after.pop("base_metadata")), {**baseline, "work_group": "", "text_version": ""})
        registry_before.pop("base_metadata")
        self.assertEqual(registry_after, registry_before)

    def test_confirmed_tags_and_entities_import_without_phantom_dirty_or_unneeded_review(self):
        local_id = self.local()
        previous = self.old_values(valid(tags=["作品组:Exact Ａ Group", "文字版本:有文字", "日常"]))
        with self.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=?,status='approved' WHERE id=?", (json.dumps(previous), local_id))
        value, snapshot = self.remote()
        snapshot["book"]["metadata"]["notableEntities"] = [{"kind": "作品组", "name": "Exact Ａ Group"}, {"kind": "文字版本", "name": "无文字"}]
        old_remote = self.old_values(value)
        with self.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=?,status='packaged' WHERE id='remote'", (json.dumps(old_remote),))
            db.execute("UPDATE komga_sources SET snapshot=?,base_metadata=?,pending_sync=1 WHERE comic_id='remote'", (json.dumps(snapshot), json.dumps(old_remote)))
        migrated = Repository(self.path)
        self.assertEqual((migrated.get(local_id).metadata.work_group, migrated.get(local_id).metadata.text_version, migrated.get(local_id).status), ("Exact Ａ Group", "有文字", "approved"))
        remote = migrated.get("remote")
        self.assertEqual((remote.metadata.work_group, remote.metadata.text_version, remote.status), ("Exact Ａ Group", "无文字", "packaged"))
        registry = migrated.get_komga_source("remote")
        self.assertEqual(registry["dirty_fields"], [])
        self.assertTrue(registry["pending_sync"])
        self.assertEqual(registry["base_metadata"]["work_group"], remote.metadata.work_group)

    def test_conflicting_raw_values_keep_evidence_and_only_revoke_affected_record(self):
        conflicting, untouched = self.local("冲突"), self.local("正常")
        conflict_tags = ["作品组:ABC", "作品组:abc", "文字版本:未知", "日常"]
        with self.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=?,status='approved' WHERE id=?", (json.dumps(self.old_values(valid(tags=conflict_tags))), conflicting))
            db.execute("UPDATE comics SET metadata=?,status='approved' WHERE id=?", (json.dumps(self.old_values(valid())), untouched))
        migrated = Repository(self.path)
        comic = migrated.get(conflicting)
        self.assertEqual((comic.metadata.work_group, comic.metadata.text_version, comic.status), ("", "", "draft"))
        self.assertEqual(comic.metadata.tags, conflict_tags)
        self.assertIn("ABC / abc", comic.warning)
        self.assertIn("未知", comic.warning)
        self.assertEqual(migrated.get(untouched).status, "approved")

    def test_remote_dirty_new_tag_conflict_keeps_draft_and_does_not_invent_new_dirty_fields(self):
        value, snapshot = self.remote()
        snapshot["book"]["metadata"].update(tags=["作品组:A", "日常"], notableEntities=[{"kind": "作品组", "name": "A"}])
        base = self.old_values(value)
        base["tags"] = ["作品组:A", "日常"]
        current = {**base, "tags": ["作品组:B", "日常"]}
        with self.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=?,status='approved' WHERE id='remote'", (json.dumps(current),))
            db.execute("UPDATE komga_sources SET base_metadata=?,snapshot=?,dirty_fields='[\"tags\"]',pending_sync=1 WHERE comic_id='remote'", (json.dumps(base), json.dumps(snapshot)))
        migrated = Repository(self.path)
        comic = migrated.get("remote")
        self.assertEqual((comic.status, comic.metadata.work_group, comic.metadata.tags), ("draft", "A", current["tags"]))
        source = migrated.get_komga_source("remote")
        self.assertEqual(source["dirty_fields"], ["tags"])
        self.assertEqual(source["base_metadata"]["work_group"], "A")
        self.assertEqual(source["snapshot"], snapshot)
        self.assertTrue(source["pending_sync"])
        self.assertIn("本机未同步标签", comic.warning)
        migrated.batch_edit(["remote"], {"work_group": ""})
        self.assertEqual(migrated.get_komga_source("remote")["dirty_fields"], ["tags", "work_group"])
        self.assertEqual(migrated.get("remote").metadata.tags, current["tags"])

    def test_older_substantive_upgrade_still_revokes_review_and_frozen_revision_detects_edits(self):
        identity = self.local()
        previous = self.old_values(valid())
        previous.pop("color")
        previous.update(genre="同人志", release_type="同人")
        with self.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=?,status='packaged' WHERE id=?", (json.dumps(previous), identity))
        migrated = Repository(self.path)
        self.assertEqual(migrated.get(identity).status, "draft")
        self.assertEqual((migrated.get(identity).metadata.genre, migrated.get(identity).metadata.release_type), ("漫画", "同人发行"))
        self.assertEqual(len(list(self.root.glob("*.before-metadata-v1_2-*.sqlite3"))), 1)
        state = migrated.review_state(identity)
        migrated.batch_edit([identity], {"work_group": "新作品组"})
        with self.assertRaisesRegex(ValueError, "预览后发生变化"):
            migrated.approve_checked(identity, state)


if __name__ == "__main__":
    unittest.main()
