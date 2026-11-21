"""v1.2 portable metadata, conservative migration and checked-field boundaries."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from comic_manage.filename_parser import parse_filename
from comic_manage.komga_remote import snapshot_fingerprint
from comic_manage.metadata import Metadata, V12_FIELDS, from_comicinfo, parse_xml, to_comicinfo
from comic_manage.repository import Repository
from comic_manage.sources import inspect_source


def valid(**fields):
    return Metadata(**{"title": "原作タイトル", "authors": ["作者"], "genre": "漫画", "age_rating": "Everyone", "tags": ["日常"], **fields})


class MetadataV12Tests(unittest.TestCase):
    def test_custom_fields_round_trip_and_removed_xml_authorities(self):
        value = valid(color="部分彩色", release_type="个人独立发布", publication_format="自定义形态",
                      release_methods=["会员订阅", "公开发布"], release_platforms=["pixivfanbox", "FANTIA", "自定义平台"],
                      reading_direction="WEBTOON", manga="Yes", volume="2", original_works=["原作、原文"],
                      characters=["甲", "乙"], uncensored=True)
        xml = to_comicinfo(value, 2)
        root = parse_xml(xml)
        self.assertIsNone(root.find("BlackAndWhite"))
        self.assertIsNone(root.find("ReadingDirection"))
        self.assertEqual(root.findtext("Manga"), "Yes")
        self.assertEqual(root.findtext("Volume"), "2")
        self.assertIsNone(root.find("Series"))
        self.assertIsNone(root.find("LanguageISO"))
        self.assertNotIn("修正状态", root.findtext("Tags"))
        for field in ("color", "release_type", "publication_format", "release_methods", "release_platforms", "original_works", "characters", "volume"):
            self.assertEqual(getattr(from_comicinfo(xml, "fallback"), field), getattr(value, field), field)
        self.assertEqual(value.release_platforms, ["pixivFANBOX", "Fantia", "自定义平台"])

    def test_series_number_is_optional_and_never_copied_to_volume(self):
        root = parse_xml(to_comicinfo(valid(series="真实系列", number="", volume="3"), 1))
        self.assertEqual(root.findtext("Series"), "真实系列")
        self.assertIsNone(root.find("Number"))
        self.assertEqual(root.findtext("Volume"), "3")
        self.assertIsNone(parse_xml(to_comicinfo(valid(series="真实系列", number="1"), 1)).find("Volume"))

    def test_review_requires_facts_and_valid_supplied_fixed_values(self):
        for change in ({"title": ""}, {"authors": []}, {"genre": "同人志"}, {"genre": ""}, {"age_rating": "Unknown"},
                       {"tags": []}, {"release_type": "同人"}, {"color": "彩色"}, {"release_methods": ["订阅"]},
                       {"original_works": ["A", "B"]}, {"translator": "未知汉化"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                valid(**change).validate_review()
        for genre in ("漫画", "小说", "CG集", "画集", "设定集", "插画集"):
            valid(genre=genre).validate_review()
        valid(tags=[], color="黑白").validate_review()
        self.assertEqual(valid(language="").language, "")

    def test_explicit_empty_color_and_direction_stay_independent_of_legacy_flags(self):
        value = valid(color="", full_color=True, black_and_white="Yes", reading_direction="", manga="YesAndRightToLeft")
        reread = Metadata.from_dict(value.to_dict())
        self.assertEqual(reread.color, "")
        self.assertEqual(reread.reading_direction, "")
        self.assertIsNone(parse_xml(to_comicinfo(reread, 1)).find("BlackAndWhite"))

    def test_old_color_evidence_is_conservative_and_conflicts_stay_unknown(self):
        self.assertEqual(Metadata.from_dict({"full_color": True}).color, "全彩")
        self.assertEqual(Metadata.from_dict({"black_and_white": "Yes"}).color, "黑白")
        self.assertEqual(Metadata.from_dict({"black_and_white": "No"}).color, "")
        xml = "<ComicInfo><BlackAndWhite>No</BlackAndWhite></ComicInfo>"
        self.assertEqual(Metadata.from_dict({"full_color": True, "black_and_white": "No", "original_xml": xml}).color, "")
        self.assertEqual(Metadata.from_dict({"full_color": True, "original_xml": xml, "tags": ["全彩"]}).color, "全彩")
        self.assertEqual(Metadata.from_dict({"tags": ["色彩:全彩", "色彩:黑白"]}).color, "")
        self.assertEqual(from_comicinfo(xml.encode(), "作品").color, "")

    def test_filename_extracts_only_explicit_release_dimensions(self):
        value, _ = parse_filename("[作者] 作品 [小说] [pixivfanbox] [会员订阅] [部分彩色]")
        self.assertEqual(value.genre, "小说")
        self.assertEqual(value.color, "部分彩色")
        self.assertEqual(value.release_platforms, ["pixivFANBOX"])
        self.assertEqual(value.release_methods, ["会员订阅"])
        self.assertEqual(value.release_type, "")
        platform_only, _ = parse_filename("[作者] 作品 [DLsite]")
        self.assertEqual((platform_only.genre, platform_only.release_type, platform_only.release_methods), ("", "", []))
        legacy, _ = parse_filename("[作者] 作品 [同人志] [独立短篇] [全彩]")
        self.assertEqual((legacy.genre, legacy.release_type, legacy.publication_format, legacy.color), ("漫画", "同人发行", "独立作品", "全彩"))
        conflict, warnings = parse_filename("作品 [全彩] [黑白] [全彩]")
        self.assertEqual(conflict.color, "")
        self.assertTrue(any("多个色彩" in warning for warning in warnings))


class CatalogV12Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "catalog.sqlite3"
        self.repo = Repository(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def local(self, name="本地作品"):
        folder = self.root / name
        folder.mkdir()
        (folder / "1.jpg").write_bytes(b"original page")
        return self.repo.upsert_scan(inspect_source(folder))

    def remote(self):
        source = {"source": "komga://scope/book", "server_url": "http://localhost:25600", "library_id": "lib", "library_name": "书库", "book_id": "book", "series_id": "series", "remote_path": "file:/books/series/book.cbz", "scope": "scope"}
        snapshot = {"book": {"id": "book", "libraryId": "lib", "seriesId": "series", "url": source["remote_path"], "oneshot": False, "media": {"pagesCount": 1}, "metadata": {"title": "原作タイトル", "tags": ["发布平台:pixivfanbox"], "notableEntities": [{"kind": "发布平台", "name": "pixivfanbox"}]}},
                    "series": {"id": "series", "libraryId": "lib", "url": "file:/books/series", "oneshot": False, "metadata": {"title": "系列", "readingDirection": "WEBTOON", "language": "zh-Hans"}}}
        value = valid(series="系列")
        self.repo.upsert_komga("remote", source, value, snapshot, snapshot_fingerprint(snapshot), 1, "")
        return source, snapshot, value

    def test_local_upgrade_backs_up_before_editing_and_keeps_all_names_outputs_originals(self):
        identity = self.local("[全彩] 本地作品")
        previous = valid(title="人工标题", original_title="原始名称", chinese_title="中文译名", original_works=["A", "B"], full_color=True,
                         black_and_white="No", original_xml="<ComicInfo><BlackAndWhite>No</BlackAndWhite></ComicInfo>").to_dict()
        for field in V12_FIELDS:
            previous.pop(field)
        previous.update(genre="同人志", release_type="同人", publication_format="独立短篇", manga="YesAndRightToLeft")
        raw = json.dumps(previous, ensure_ascii=False)
        with self.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=?,status='packaged',output='existing.cbz' WHERE id=?", (raw, identity))
        migrated = Repository(self.path)
        comic = migrated.get(identity)
        self.assertEqual((comic.metadata.title, comic.metadata.original_title, comic.metadata.chinese_title), ("人工标题", "原始名称", "中文译名"))
        self.assertEqual((comic.metadata.genre, comic.metadata.release_type, comic.metadata.publication_format), ("漫画", "同人发行", "独立作品"))
        self.assertEqual((comic.metadata.color, comic.metadata.reading_direction), ("全彩", "RIGHT_TO_LEFT"))
        self.assertEqual(comic.metadata.original_works, ["A", "B"])
        self.assertEqual((comic.status, comic.output), ("draft", "existing.cbz"))
        self.assertIn("多个原作", comic.warning)
        migrated.batch_edit([identity], {"summary": "只改简介"})
        self.assertEqual(migrated.get(identity).metadata.original_works, ["A", "B"])
        with self.assertRaisesRegex(ValueError, "原作"):
            migrated.get(identity).metadata.validate_review()
        backups = list(self.root.glob("*.before-metadata-v1_2-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute("SELECT metadata,status FROM comics WHERE id=?", (identity,)).fetchone(), (raw, "packaged"))
        Repository(self.path)
        self.assertEqual(len(list(self.root.glob("*.before-metadata-v1_2-*.sqlite3"))), 1)
        self.assertEqual((self.root / "[全彩] 本地作品" / "1.jpg").read_bytes(), b"original page")

    def test_plain_old_bwnocolor_is_not_migrated_to_fullcolor(self):
        identity = self.local()
        previous = valid(full_color=True, black_and_white="No", original_xml="<ComicInfo><BlackAndWhite>No</BlackAndWhite></ComicInfo>").to_dict()
        for field in V12_FIELDS:
            previous.pop(field)
        with self.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=? WHERE id=?", (json.dumps(previous), identity))
        comic = Repository(self.path).get(identity)
        self.assertEqual(comic.metadata.color, "")
        self.assertIn("无法确认全彩", comic.warning)

    def test_remote_migration_preserves_raw_snapshot_and_dirty_without_phantom_fields(self):
        source, snapshot, value = self.remote()
        baseline = value.to_dict()
        for field in V12_FIELDS:
            baseline.pop(field)
        baseline.update(genre="同人志", release_type="同人", publication_format="独立短篇", manga="Unknown")
        edited = {**baseline, "summary": "用户未同步的草稿"}
        literal_snapshot = json.dumps(snapshot, ensure_ascii=False, indent=1)
        observed = '{"preserved": true}'
        with self.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=?,fingerprint='old-algorithm',status='approved' WHERE id='remote'", (json.dumps(edited),))
            db.execute("UPDATE komga_sources SET base_metadata=?,snapshot=?,observed_snapshot=?,dirty_fields='[\"summary\"]',pending_sync=1 WHERE comic_id='remote'", (json.dumps(baseline), literal_snapshot, observed))
        migrated = Repository(self.path)
        comic = migrated.get("remote")
        registry = migrated.get_komga_source("remote")
        self.assertEqual(registry["dirty_fields"], ["summary"])
        self.assertEqual((comic.metadata.genre, comic.metadata.release_type), ("同人志", "同人"))
        self.assertEqual((comic.metadata.reading_direction, comic.metadata.release_platforms), ("WEBTOON", ["pixivFANBOX"]))
        self.assertEqual(registry["base_metadata"]["release_platforms"], ["pixivFANBOX"])
        self.assertEqual(comic.status, "draft")
        with migrated.connect() as db:
            saved = db.execute("SELECT snapshot,observed_snapshot FROM komga_sources WHERE comic_id='remote'").fetchone()
            self.assertEqual(tuple(saved), (literal_snapshot, observed))
        self.assertEqual(migrated.upsert_komga("remote", source, Metadata.from_dict(registry["base_metadata"]), snapshot, snapshot_fingerprint(snapshot), 1, ""), "kept")
        self.assertFalse(migrated.get_komga_source("remote")["conflict"])
        self.assertEqual(migrated.get("remote").metadata.summary, "用户未同步的草稿")
        migrated.batch_edit(["remote"], {"genre": "漫画", "release_type": "同人发行"})
        self.assertEqual(migrated.get_komga_source("remote")["dirty_fields"], ["genre", "release_type", "summary"])

    def test_remote_xml_only_fields_are_rejected_and_batch_rolls_back(self):
        self.remote()
        identity = self.local()
        self.repo.save(identity, valid())
        for field, value in (("volume", "2"), ("manga", "No"), ("black_and_white", "Yes"), ("full_color", True)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "专用字段"):
                self.repo.batch_edit([identity, "remote"], {field: value})
            self.assertEqual(getattr(self.repo.get(identity).metadata, field), getattr(valid(), field))
            payload = self.repo.get("remote").metadata.to_dict()
            payload[field] = value
            with self.assertRaisesRegex(ValueError, "专用字段"):
                self.repo.save("remote", Metadata.from_dict(payload))
        self.assertEqual(self.repo.get_komga_source("remote")["dirty_fields"], [])


if __name__ == "__main__":
    unittest.main()
