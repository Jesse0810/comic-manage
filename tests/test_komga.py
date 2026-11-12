"""Komga XML, supplementary entities, output layout and safe catalog migration."""
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from comic_manage.komga import komga_book_payload, komga_metadata, komga_series_payload
from comic_manage.metadata import Metadata, from_comicinfo, parse_xml, to_comicinfo
from comic_manage.packaging import make_plan
from comic_manage.repository import Repository
from comic_manage.sources import inspect_source


class KomgaMetadataTests(unittest.TestCase):
    def valid(self, **fields):
        return Metadata(title="作品", authors=["作者"], age_rating="Everyone", **fields)

    def test_xml_import_infers_real_series_without_guessing_number_from_volume(self):
        metadata = from_comicinfo('<ComicInfo><Title>卷二</Title><Series>真实系列</Series><Volume>2</Volume></ComicInfo>'.encode(), "fallback")
        self.assertEqual(metadata.collection_mode, "series")
        self.assertEqual(metadata.number, "")
        self.assertEqual(metadata.volume, "2")
        independent = from_comicinfo(b"<ComicInfo><Title>Independent</Title></ComicInfo>", "fallback")
        self.assertEqual(independent.collection_mode, "oneshot")
        self.assertEqual(independent.localized_series_label, "zh-Hans")

    def test_oneshot_ignores_legacy_series_fields_and_removes_organizing_extensions(self):
        old_xml = "<ComicInfo><Series>Old</Series><Number>2</Number><Count>3</Count><Volume>4</Volume><LocalizedSeries>Alias</LocalizedSeries><SeriesSort>Sort</SeriesSort><SeriesGroup>Collection</SeriesGroup><StoryArc>Readlist</StoryArc><StoryArcNumber>1</StoryArcNumber><AlternateSeries>Other</AlternateSeries><AlternateNumber>9</AlternateNumber><Notes>Keep</Notes></ComicInfo>"
        metadata = self.valid(collection_mode="oneshot", series="旧系列", number="2", count="3", volume="4",
                              localized_series="旧别名", characters=["甲", "乙"], original_xml=old_xml)
        before = metadata.to_dict()
        root = parse_xml(to_comicinfo(metadata, 3))
        for element in ("Series", "Number", "Count", "Volume", "LocalizedSeries", "SeriesSort", "SeriesGroup", "StoryArc", "StoryArcNumber", "AlternateSeries", "AlternateNumber"):
            self.assertIsNone(root.find(element), element)
        self.assertEqual(root.findtext("Characters"), "甲, 乙")
        self.assertEqual(root.findtext("Tags"), "角色:甲, 角色:乙")
        self.assertEqual(root.findtext("Notes"), "Keep")
        self.assertEqual(metadata.to_dict(), before)
        imported = from_comicinfo(to_comicinfo(metadata, 3), "fallback")
        self.assertEqual(imported.characters, ["甲", "乙"])
        self.assertEqual(imported.collection_mode, "oneshot")

    def test_true_series_requires_number_and_excludes_volume_and_kavita_alias(self):
        metadata = self.valid(series="真实系列", volume="2", count="3", localized_series="中文系列", localized_series_label="原名")
        with self.assertRaisesRegex(ValueError, "Number"):
            to_comicinfo(metadata, 1)
        metadata.number = "2"
        root = parse_xml(to_comicinfo(metadata, 1))
        self.assertEqual(root.findtext("Series"), "真实系列")
        self.assertEqual(root.findtext("Number"), "2")
        self.assertEqual(root.findtext("Count"), "3")
        self.assertIsNone(root.find("Volume"))
        self.assertIsNone(root.find("LocalizedSeries"))
        self.assertEqual(komga_metadata(metadata)["alternateTitles"], [{"label": "原名", "title": "中文系列"}])

    def test_supplementary_entities_keep_each_books_age_genre_and_color(self):
        metadata = Metadata(title="作品", authors=["作者"], age_rating="R18+", genre="同人志", black_and_white="No",
                            original_works=["东方Project"], release_type="同人", publication_format="独立短篇", uncensored=True)
        entities = komga_metadata(metadata)["notableEntities"]
        for entity in ({"kind": "年龄分级", "name": "R18+"}, {"kind": "内容类型", "name": "同人志"},
                       {"kind": "色彩", "name": "彩色/非黑白"}, {"kind": "修正状态", "name": "无修正"}):
            self.assertIn(entity, entities)
        metadata.age_rating = "Everyone"
        metadata.black_and_white = "Unknown"
        kinds = {entity["kind"] for entity in komga_metadata(metadata)["notableEntities"]}
        self.assertNotIn("色彩", kinds)
        self.assertNotIn("修正状态", kinds)
        self.assertIn("年龄分级", kinds)

    def test_api_merge_replaces_managed_kinds_and_preserves_imported_characters(self):
        existing = [{"kind": "character", "name": "角色甲"}, {"kind": "team", "name": "团队"},
                    {"kind": "年龄分级", "name": "R18+"}, {"kind": "原作", "name": "旧原作"}]
        metadata = self.valid(original_works=["新原作"])
        payload = komga_book_payload(metadata, existing)
        self.assertTrue(payload["notableEntitiesLock"])
        self.assertIn(existing[0], payload["notableEntities"])
        self.assertIn(existing[1], payload["notableEntities"])
        self.assertNotIn(existing[2], payload["notableEntities"])
        self.assertNotIn(existing[3], payload["notableEntities"])
        self.assertIn({"kind": "原作", "name": "新原作"}, payload["notableEntities"])

    def test_series_alias_merges_other_labels_and_is_ignored_for_oneshots(self):
        metadata = self.valid(series="系列", number="1", localized_series="新译名", localized_series_label="zh-Hans")
        existing = [{"label": "zh-Hans", "title": "旧译名"}, {"label": "ja", "title": "日文名"}]
        payload = komga_series_payload(metadata, existing)
        self.assertEqual(payload["alternateTitles"], [existing[1], {"label": "zh-Hans", "title": "新译名"}])
        metadata.localized_series = ""
        self.assertEqual(komga_series_payload(metadata, existing)["alternateTitles"], [existing[1]])
        metadata.collection_mode = "oneshot"
        metadata.localized_series = "本机保留的别名"
        self.assertIsNone(komga_series_payload(metadata, existing))
        self.assertEqual(komga_metadata(metadata)["alternateTitles"], [])


class KomgaCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = Repository(self.root / "data/catalog.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def comic(self, folder, metadata):
        source = self.root / folder
        source.mkdir()
        (source / "1.jpg").write_bytes(b"original-image")
        snapshot = inspect_source(source)
        identity = self.repo.upsert_scan(snapshot)
        self.repo.save(identity, metadata)
        self.repo.approve(identity, snapshot)
        return self.repo.get(identity)

    def test_output_directories_group_real_series_across_writers_without_name_collisions(self):
        first = self.comic("first", Metadata(title="第一卷", authors=["甲"], series="共同系列", number="1", age_rating="Everyone"))
        second = self.comic("second", Metadata(title="第二卷", authors=["乙"], series="共同系列", number="2", age_rating="Everyone"))
        output = self.root / "out"
        first_plan, second_plan = make_plan(first, output), make_plan(second, output)
        self.assertEqual(first_plan.destination.parent, second_plan.destination.parent)
        self.assertTrue(first_plan.destination.parent.name.startswith("共同系列"))
        one = replace(first, metadata=replace(first.metadata, collection_mode="oneshot"))
        self.assertEqual(make_plan(one, output).destination.parent.name, "_oneshots")
        a = replace(first, metadata=replace(first.metadata, series="A:B"))
        b = replace(second, metadata=replace(second.metadata, series="A?B"))
        self.assertNotEqual(make_plan(a, output).destination.parent, make_plan(b, output).destination.parent)
        reserved = replace(first, metadata=replace(first.metadata, series="_Oneshots系列"))
        self.assertTrue(make_plan(reserved, output).destination.parent.name.startswith("series-_oneshots"))
        full_width = replace(first, metadata=replace(first.metadata, series="ＡＢＣ"))
        mixed_case = replace(second, metadata=replace(second.metadata, series="AbC"))
        self.assertEqual(make_plan(full_width, output).destination.parent, make_plan(mixed_case, output).destination.parent)
        self.assertTrue(first_plan.warnings)

    def test_v14_migration_backs_up_raw_data_keeps_removed_records_and_revokes_review(self):
        comic = self.comic("old", Metadata(title="人工标题", authors=["作者"], series="旧系列", number="1", age_rating="Everyone"))
        previous = comic.metadata.to_dict()
        previous.pop("collection_mode")
        previous.pop("localized_series_label")
        previous.update(volume="2", localized_series="手工译名")
        self.repo.remove([comic.id])
        with self.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=?,status='packaged',output='old.cbz' WHERE id=?", (json.dumps(previous), comic.id))
        upgraded = Repository(self.repo.path)
        record = upgraded.get(comic.id, include_removed=True)
        self.assertEqual(record.metadata.collection_mode, "series")
        self.assertEqual(record.metadata.localized_series_label, "zh-Hans")
        self.assertEqual(record.metadata.volume, "2")
        self.assertEqual(record.metadata.localized_series, "手工译名")
        self.assertEqual(record.output, "old.cbz")
        self.assertEqual(record.status, "draft")
        self.assertTrue(upgraded.is_removed(comic.id))
        backups = list(self.repo.path.parent.glob("*.before-komga-v1_1-K-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            raw, status = db.execute("SELECT metadata,status FROM comics WHERE id=?", (comic.id,)).fetchone()
            self.assertEqual(json.loads(raw), previous)
            self.assertEqual(status, "packaged")
        Repository(self.repo.path)
        self.assertEqual(len(list(self.repo.path.parent.glob("*.before-komga-v1_1-K-*.sqlite3"))), 1)


if __name__ == "__main__":
    unittest.main()
