"""Behavioral coverage for NAS metadata v1.1 and existing catalog migration."""
from dataclasses import replace
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from comic_manage.metadata import Metadata, from_comicinfo, parse_xml, to_comicinfo
from comic_manage.repository import Repository
from comic_manage.service import ComicService, ServiceError
from comic_manage.sources import inspect_source


class MetadataRulesTests(unittest.TestCase):
    def valid(self, **fields):
        return Metadata(title="作品 & 名称", authors=["作者"], age_rating="Everyone", **fields)

    def test_all_standard_fields_remain_independent_and_round_trip(self):
        metadata = self.valid(series="系列", localized_series="系列译名", number="1.5", count="12", volume="2",
                              summary="完整简介", year="2024", month="2", day="29", pencillers=["作者", "画师"],
                              publisher="发行社团", translator="译者", genre="漫画", tags=["日常"],
                              web=["https://example.org/Work?a=1,2", "https://example.org/Other"],
                              black_and_white="No", manga="YesAndRightToLeft")
        root = parse_xml(to_comicinfo(metadata, 3))
        self.assertEqual(root.findtext("Title"), "作品 & 名称")
        self.assertEqual(root.findtext("Series"), "系列")
        self.assertIsNone(root.find("LocalizedSeries"))
        self.assertIsNone(root.find("Volume"))
        self.assertEqual(root.findtext("Penciller"), "画师")
        restored = from_comicinfo(to_comicinfo(metadata, 3), "fallback")
        for field in ("title", "series", "number", "count", "summary", "year", "month", "day", "pencillers", "authors", "publisher", "translator", "genre", "tags", "web", "black_and_white", "manga"):
            self.assertEqual(getattr(restored, field), getattr(metadata, field), field)
        self.assertEqual(root.findtext("PageCount"), "3")

    def test_independent_works_have_no_xml_series_or_series_numbering(self):
        metadata = self.valid(original_works=["东方Project"])
        root = parse_xml(to_comicinfo(metadata, 1))
        self.assertIsNone(root.find("Series"))
        self.assertIsNone(root.find("LocalizedSeries"))
        self.assertIsNone(root.find("Number"))
        self.assertIsNone(root.find("Volume"))
        self.assertIsNone(root.find("Count"))
        self.assertEqual(root.findtext("Tags"), "原作:东方Project")

    def test_custom_fields_export_independent_namespaced_tags_and_round_trip(self):
        metadata = Metadata(title="同人作品", authors=["作者"], age_rating="R18+", original_works=["东方Project", "Fate"],
                            characters=["博丽灵梦", "雾雨魔理沙"], release_type="同人", publication_format="独立短篇",
                            uncensored=True, translator="测试汉化组", black_and_white="No", language="zh-Hans",
                            genre="同人志", tags=["汉化", "全彩", "无修正", "年龄等级:R18", "纯爱", "日常"])
        root = parse_xml(to_comicinfo(metadata, 1))
        self.assertEqual(root.findtext("Tags"), "原作:东方Project, 原作:Fate, 角色:博丽灵梦, 角色:雾雨魔理沙, 发行性质:同人, 出版形态:独立短篇, 修正状态:无修正, 纯爱, 日常")
        for nonstandard in ("OriginalWorks", "ReleaseType", "PublicationFormat", "Uncensored"):
            self.assertIsNone(root.find(nonstandard))
        self.assertEqual(root.findtext("Characters"), "博丽灵梦, 雾雨魔理沙")
        restored = from_comicinfo(to_comicinfo(metadata, 1), "fallback")
        self.assertEqual(restored.characters, metadata.characters)
        self.assertEqual(restored.original_works, metadata.original_works)
        self.assertEqual(restored.tags, ["纯爱", "日常"])
        self.assertTrue(restored.uncensored)

    def test_censorship_only_applies_to_r18_and_censored_default_is_omitted(self):
        for rating in ("Everyone", "MA15+", "Unknown"):
            self.assertNotIn("修正状态:无修正", Metadata(age_rating=rating, uncensored=True).all_tags)
        self.assertEqual(Metadata(age_rating="R18+", uncensored=False).all_tags, [])
        self.assertEqual(Metadata(age_rating="R18+", uncensored=True).all_tags, ["修正状态:无修正"])

    def test_import_plain_legacy_flags_becomes_standard_authorities(self):
        value = from_comicinfo(b"<ComicInfo><Title>Title</Title><Writer>A</Writer><AgeRating>Adults Only 18+</AgeRating><Tags>\xe6\x97\xa0\xe4\xbf\xae\xe6\xad\xa3,\xe5\x85\xa8\xe5\xbd\xa9</Tags></ComicInfo>", "fallback")
        self.assertEqual(value.black_and_white, "No")
        self.assertEqual(value.age_rating, "R18+")
        self.assertEqual(value.all_tags, ["修正状态:无修正"])

    def test_unknown_color_remains_unknown_and_ambiguous_age_requires_review(self):
        for rating in ("Teen", "Mature 17+", "Everyone 10+", "unrecognized"):
            metadata = Metadata(title="Title", authors=["A"], age_rating=rating)
            self.assertEqual(metadata.age_rating, "Unknown")
            self.assertEqual(metadata.black_and_white, "Unknown")
            with self.assertRaisesRegex(ValueError, "年龄"):
                to_comicinfo(metadata, 1)
        self.assertEqual(Metadata(age_rating="R15").age_rating, "MA15+")
        self.assertEqual(Metadata(age_rating="R18").age_rating, "R18+")

    def test_unknown_translation_does_not_export_as_confirmed_credit(self):
        for translator in ("未知汉化", "中国翻訳"):
            with self.assertRaisesRegex(ValueError, "译者"):
                to_comicinfo(self.valid(language="zh-Hans", translator=translator, translated=True), 1)
        root = parse_xml(to_comicinfo(self.valid(language="zh-Hant", translator="已确认汉化组"), 1))
        self.assertEqual(root.findtext("Translator"), "已确认汉化组")
        self.assertIsNone(root.find("Tags"))

    def test_chinese_original_and_translation_evidence_are_independent_of_language(self):
        original = self.valid(language="zh-Hans")
        self.assertIsNone(parse_xml(to_comicinfo(original, 1)).find("Translator"))
        imported = from_comicinfo(to_comicinfo(original, 1), "fallback")
        self.assertFalse(imported.translated)
        to_comicinfo(imported, 1)
        for language in ("ja", "en", "zh-Hant"):
            for translator in ("未知汉化", "中国翻訳", "中國翻譯"):
                with self.subTest(language=language, translator=translator), self.assertRaisesRegex(ValueError, "译者"):
                    to_comicinfo(self.valid(language=language, translator=translator), 1)
            # A stale internal flag cannot override an explicitly empty field.
            to_comicinfo(self.valid(language=language, translated=True), 1)
        evidence = from_comicinfo('<ComicInfo><Title>A</Title><Writer>B</Writer><AgeRating>Everyone</AgeRating><LanguageISO>en</LanguageISO><Tags>汉化</Tags></ComicInfo>'.encode(), 'fallback')
        self.assertTrue(evidence.translated)
        self.assertEqual(evidence.translator, "未知汉化")
        with self.assertRaisesRegex(ValueError, "译者"):
            to_comicinfo(evidence, 1)
        evidence.translator = ""
        corrected = Metadata.from_dict(evidence.to_dict())
        self.assertTrue(corrected.translated)
        self.assertIsNone(parse_xml(to_comicinfo(corrected, 1)).find("Translator"))
        legacy = Metadata.from_dict({'original_title': 'A', 'translated': True, 'authors': ['B'], 'age_rating': 'Everyone'})
        self.assertEqual(legacy.translator, "未知汉化")

    def test_classification_chips_split_individual_values_before_export(self):
        metadata = self.valid(original_works=["东方Project、Fate", "东方Project"], characters=["甲、乙", "乙"])
        self.assertEqual(metadata.original_works, ["东方Project", "Fate"])
        self.assertEqual(metadata.characters, ["甲", "乙"])
        self.assertEqual(metadata.all_tags, ["原作:东方Project", "原作:Fate", "角色:甲", "角色:乙"])
        restored = from_comicinfo(to_comicinfo(metadata, 1), "fallback")
        self.assertEqual(restored.original_works, metadata.original_works)
        self.assertEqual(restored.characters, metadata.characters)

    def test_day_and_integer_ranges_and_partial_dates(self):
        for fields in ({"year": "2023", "month": "2", "day": "29"}, {"month": "4", "day": "31"}, {"day": "3"}, {"count": "0", "series": "系列", "number": "1"}, {"count": "2147483648", "series": "系列", "number": "1"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                to_comicinfo(self.valid(**fields), 1)
        for fields in ({"year": "2024"}, {"month": "2", "day": "29"}, {"number": "1-5"}, {"number": "TPB1"}, {"volume": "0"}):
            to_comicinfo(self.valid(**fields), 1)
        root = parse_xml(to_comicinfo(self.valid(year="2024"), 1))
        self.assertIsNone(root.find("Month"))
        self.assertIsNone(root.find("Day"))

    def test_web_urls_are_exact_deduplicated_space_separated_and_keep_commas(self):
        urls = ["https://example.org/A?q=A,B", "https://example.org/a?q=a,b"]
        metadata = self.valid(web=urls + [urls[0]])
        self.assertEqual(metadata.web, urls)
        xml = to_comicinfo(metadata, 1)
        self.assertEqual(parse_xml(xml).findtext("Web"), " ".join(urls))
        self.assertEqual(from_comicinfo(xml, "fallback").web, urls)
        for value in ("javascript:alert(1)", "https://example.org/not a url", "example.org"):
            with self.assertRaises(ValueError):
                to_comicinfo(self.valid(web=[value]), 1)

    def test_series_sort_removed_unknown_fields_and_valid_page_attributes_preserved(self):
        old = '<ComicInfo><Title>Old title</Title><Series>Old series</Series><SeriesSort>Stale override</SeriesSort><Notes>Keep</Notes><GTIN>123456</GTIN><Characters>A, B</Characters><Pages><Page Image="0" Type="FrontCover" DoublePage="True"/><Page Image="1" Bookmark="Chapter"/><Page Image="100" Type="FrontCover"/></Pages></ComicInfo>'
        metadata = self.valid(series="New series", number="1", original_xml=old, cover_index=1)
        root = parse_xml(to_comicinfo(metadata, 2))
        self.assertEqual(root.findtext("Series"), "New series")
        self.assertIsNone(root.find("SeriesSort"))
        self.assertEqual(root.findtext("Notes"), "Keep")
        self.assertEqual(root.findtext("GTIN"), "123456")
        self.assertEqual(len(root.findall("./Pages/Page")), 2)
        self.assertEqual(root.find('./Pages/Page[@Image="0"]').get("DoublePage"), "True")
        self.assertIsNone(root.find('./Pages/Page[@Image="0"]').get("Type"))
        self.assertEqual(root.find('./Pages/Page[@Image="1"]').get("Bookmark"), "Chapter")
        self.assertEqual(root.find('./Pages/Page[@Image="1"]').get("Type"), "FrontCover")
        restored = from_comicinfo(old.encode(), "fallback")
        self.assertEqual(restored.characters, ["A", "B"])
        self.assertEqual(restored.title, "Old title")
        self.assertEqual(restored.series, "Old series")

    def test_explicit_blank_fields_do_not_reappear_from_xml_or_legacy_flags(self):
        data = self.valid()
        data.original_title = "旧原名"
        data.chinese_title = "旧译名"
        data.full_color = True
        data.original_xml = "<ComicInfo><Series>Old</Series><Tags>原作:Old,全彩</Tags><Web>https://old.test</Web></ComicInfo>"
        payload = data.to_dict()
        payload.update(title="", series="", original_works=[], web=[], black_and_white="Unknown")
        normalized = Metadata.from_dict(payload)
        self.assertEqual(normalized.title, "")
        self.assertEqual(normalized.series, "")
        self.assertEqual(normalized.original_works, [])
        self.assertEqual(normalized.web, [])
        self.assertEqual(normalized.black_and_white, "Unknown")
        with self.assertRaisesRegex(ValueError, "作品名称"):
            to_comicinfo(normalized, 1)

    def test_normalized_genres_and_namespaces_have_single_authority(self):
        metadata = self.valid(genre="同人志,恋爱,NTR", tags=["内容类型:漫画", "原作：东方Project", "角色:灵梦、魔理沙", "汉化状态:汉化", "日常"])
        self.assertEqual(metadata.genre, "同人志")
        self.assertEqual(metadata.tags, ["日常", "恋爱", "NTR"])
        self.assertEqual(metadata.original_works, ["东方Project"])
        self.assertEqual(metadata.characters, ["灵梦", "魔理沙"])
        self.assertEqual(self.valid(genre="商业志").release_type, "商业")
        self.assertEqual(self.valid(genre="单行本").publication_format, "单行本")
        self.assertEqual(self.valid(pencillers=["作者", "画师", "画师"]).pencillers, ["画师"])
        root = parse_xml(to_comicinfo(self.valid(localized_series="Ignored One-Shot alias"), 1))
        self.assertIsNone(root.find("LocalizedSeries"))


class RulesCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.book = self.root / "书"
        self.book.mkdir()
        (self.book / "1.jpg").write_bytes(b"page")
        self.service = ComicService(self.root / "data")
        self.identity = self.service.repo.upsert_scan(inspect_source(self.book))

    def tearDown(self):
        self.temp.cleanup()

    def test_list_fields_are_validated_and_saved_with_canonical_title(self):
        for key in ("pencillers", "web", "original_works", "characters"):
            with self.assertRaises(ServiceError):
                self.service.call("save", {"id": self.identity, "metadata": {key: "not a list"}})
        payload = {"title": "新标题", "original_works": ["Fate"], "characters": ["A", "B"], "pencillers": ["画师"], "web": ["https://example.org/A,B"], "age_rating": "Unknown"}
        saved = self.service.call("save", {"id": self.identity, "metadata": payload})
        for field, expected in payload.items():
            self.assertEqual(saved["metadata"][field], expected)
        with self.assertRaises(ValueError):
            self.service.call("approve", {"id": self.identity, "metadata": {"authors": ["作者"]}})
        self.assertEqual(self.service.repo.get(self.identity).status, "draft")
        self.service.call("save", {"id": self.identity, "metadata": {"authors": ["作者"], "age_rating": "Everyone", "translated": True, "translator": "未知汉化"}})
        for language in ("en", "ja"):
            with self.assertRaisesRegex(ValueError, "译者"):
                self.service.call("approve", {"id": self.identity, "metadata": {"language": language}})
        corrected = self.service.call("approve", {"id": self.identity, "metadata": {"translator": ""}})
        self.assertEqual(corrected["status"], "approved")
        self.assertEqual(corrected["metadata"]["translator"], "")
        self.assertTrue(corrected["metadata"]["translated"])

    def test_legacy_catalog_is_backed_up_migrated_once_and_review_is_revoked(self):
        legacy = {"original_title": "旧原名", "chinese_title": "手工译名", "translated": True, "authors": ["原作者"], "genre": "商业志", "full_color": True, "uncensored": True, "age_rating": "Mature 17+", "tags": ["日常"]}
        self.service.repo.remove([self.identity])
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=?,status='packaged',output='existing.cbz' WHERE id=?", (json.dumps(legacy), self.identity))
        path = self.service.repo.path
        migrated = Repository(path)
        comic = migrated.get(self.identity, include_removed=True)
        self.assertEqual(comic.metadata.title, "手工译名")
        self.assertEqual(comic.metadata.original_title, "旧原名")
        self.assertEqual(comic.metadata.authors, ["原作者"])
        self.assertEqual(comic.metadata.translator, "未知汉化")
        self.assertEqual(comic.metadata.age_rating, "Unknown")
        self.assertEqual(comic.metadata.black_and_white, "No")
        self.assertEqual(comic.metadata.genre, "漫画")
        self.assertEqual(comic.metadata.release_type, "商业")
        self.assertEqual(comic.status, "draft")
        self.assertEqual(comic.output, "existing.cbz")
        self.assertTrue(migrated.is_removed(self.identity))
        self.assertIn("Mature 17+", comic.warning)
        backups = list(path.parent.glob('*.before-metadata-v1_1-*.sqlite3'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            old, status = db.execute("SELECT metadata,status FROM comics WHERE id=?", (self.identity,)).fetchone()
            self.assertEqual(json.loads(old), legacy)
            self.assertEqual(status, "packaged")
        Repository(path)
        self.assertEqual(len(list(path.parent.glob('*.before-metadata-v1_1-*.sqlite3'))), 1)
        self.assertEqual((self.book / '1.jpg').read_bytes(), b'page')

    def test_explicit_series_allows_writer_changes_and_oneshots_stay_independent(self):
        self.service.call('approve', {'id': self.identity, 'metadata': {'title': '卷一', 'series': '共同系列', 'number': '1', 'collection_mode': 'series', 'authors': ['作者甲'], 'age_rating': 'Everyone'}})
        another = self.root / '第二卷'
        another.mkdir()
        (another / '1.jpg').write_bytes(b'second')
        identity = self.service.repo.upsert_scan(inspect_source(another))
        result = self.service.call('approve', {'id': identity, 'metadata': {'title': '卷二', 'series': '共同系列', 'number': '2', 'collection_mode': 'series', 'authors': ['作者乙'], 'age_rating': 'Everyone'}})
        self.assertEqual(result['status'], 'approved')
        self.service.call('approve', {'id': self.identity, 'metadata': {'title': '独立同名', 'collection_mode': 'oneshot', 'authors': ['作者甲'], 'age_rating': 'Everyone'}})
        result = self.service.call('approve', {'id': identity, 'metadata': {'title': '独立同名', 'collection_mode': 'oneshot', 'authors': ['作者乙'], 'age_rating': 'Everyone'}})
        self.assertEqual(result['status'], 'approved')


if __name__ == '__main__':
    unittest.main()
