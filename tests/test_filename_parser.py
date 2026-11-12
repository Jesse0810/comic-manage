from pathlib import Path
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

from comic_manage.filename_parser import parse_filename
from comic_manage.metadata import Metadata, from_comicinfo, to_comicinfo
from comic_manage.sources import Snapshot, is_link


class FilenameParserTests(unittest.TestCase):
    def test_full_folder_name_extracts_all_explicit_fields(self):
        metadata, warnings = parse_filename(
            "[2019.10] [pink-noise (みずいろめがね)] 漫画原名 "
            "[畜牧系學姊漢化組] [无修正] [全彩]"
        )
        self.assertEqual(metadata.original_title, "漫画原名")
        self.assertEqual(metadata.chinese_title, "")
        self.assertEqual(metadata.authors, ["みずいろめがね"])
        self.assertEqual(metadata.publisher, "pink-noise")
        self.assertEqual(metadata.translator, "畜牧系學姊漢化組")
        self.assertEqual((metadata.year, metadata.month), ("2019", "10"))
        self.assertTrue(metadata.translated)
        self.assertEqual(metadata.language, "zh-Hans")
        self.assertTrue(metadata.uncensored)
        self.assertTrue(metadata.full_color)
        self.assertEqual(warnings, [])

    def test_simple_title_is_not_changed(self):
        title = "日本語の作品 第2巻 (完全版)"
        metadata, warnings = parse_filename(title)
        self.assertEqual(metadata.original_title, title)
        self.assertEqual(metadata.authors, [])
        self.assertEqual(metadata.language, "ja")
        self.assertEqual(warnings, [])

    def test_unknown_translator_aliases_use_same_name(self):
        for alias in ("中国翻訳", "中国翻译", "中國翻譯", "未知汉化", "未知漢化"):
            with self.subTest(alias=alias):
                metadata, _ = parse_filename(f"作品 [{alias}]")
                self.assertEqual(metadata.translator, "未知汉化")
                self.assertEqual(metadata.original_title, "作品")
                self.assertTrue(metadata.translated)
                self.assertEqual(metadata.chinese_title, "")

    def test_translator_names_with_simplified_and_traditional_spelling(self):
        for translator in ("某某汉化组", "某某漢化組", "星空翻译组", "星空翻譯組", "星空翻訳組"):
            with self.subTest(translator=translator):
                metadata, _ = parse_filename(f"作品 [{translator}]")
                self.assertEqual(metadata.translator, translator)
                self.assertTrue(metadata.translated)

    def test_traditional_tags_and_genre_are_canonicalized(self):
        metadata, _ = parse_filename("[作者] 作品 [無修正] [全彩] [同人誌]")
        self.assertEqual(metadata.authors, ["作者"])
        self.assertEqual(metadata.genre, "同人志")
        self.assertTrue(metadata.uncensored)
        self.assertTrue(metadata.full_color)
        self.assertEqual(metadata.original_title, "作品")

    def test_explicit_genres_without_guessing(self):
        for label, expected in (("商業誌", "漫画"), ("商业志", "漫画"), ("單行本", "漫画")):
            with self.subTest(label=label):
                metadata, _ = parse_filename(f"[作者] 作品 [{label}]")
                self.assertEqual(metadata.genre, expected)
        metadata, _ = parse_filename("[作者] 同人誌という名前の作品")
        self.assertEqual(metadata.genre, "")

    def test_conflicting_genres_preserve_second_type_for_review(self):
        metadata, warnings = parse_filename("作品 [同人志] [商业志]")
        self.assertEqual(metadata.genre, "同人志")
        self.assertIn("商业志", metadata.tags)
        self.assertTrue(any("多个作品类型" in warning for warning in warnings))

    def test_fullwidth_brackets_and_nested_author_alias(self):
        metadata, _ = parse_filename("［2020-01］ ［社團（作者（別名））］ 作品 ［無修正］")
        self.assertEqual(metadata.publisher, "社團")
        self.assertEqual(metadata.authors, ["作者（別名）"])
        self.assertEqual(metadata.original_title, "作品")
        self.assertEqual((metadata.year, metadata.month), ("2020", "1"))
        self.assertTrue(metadata.uncensored)

    def test_multiple_authors_use_reliable_separators(self):
        metadata, _ = parse_filename("[Circle (作者A, 作者B；作者C)] Title")
        self.assertEqual(metadata.authors, ["作者A", "作者B", "作者C"])
        metadata, _ = parse_filename("[Circle (A & B/別名)] Title")
        self.assertEqual(metadata.authors, ["A & B/別名"])

    def test_multiple_parenthetical_aliases_are_not_truncated(self):
        metadata, _ = parse_filename("[作者 (別名A) (別名B)] 作品")
        self.assertEqual(metadata.authors, ["作者 (別名A) (別名B)"])
        self.assertEqual(metadata.publisher, "")
        self.assertEqual(metadata.original_title, "作品")

    def test_unknown_boundary_tags_are_preserved(self):
        metadata, warnings = parse_filename("[DL版] [作者] 作品 [高解像度] [Extra Bonus]")
        self.assertEqual(metadata.authors, ["作者"])
        self.assertEqual(metadata.tags, ["DL版", "高解像度", "Extra Bonus"])
        self.assertEqual(metadata.original_title, "作品")
        self.assertTrue(any("高解像度" in warning for warning in warnings))

    def test_unknown_bracket_text_inside_title_is_kept(self):
        metadata, warnings = parse_filename("[作者] 第一部 [秘密の章] 後篇 [全彩]")
        self.assertEqual(metadata.original_title, "第一部 [秘密の章] 後篇")
        self.assertEqual(metadata.tags, [])
        self.assertTrue(any("秘密の章" in warning for warning in warnings))

    def test_nested_square_brackets_are_kept_for_review(self):
        name = "[Circle [Alias] (作者)] 原名 [全彩]"
        metadata, warnings = parse_filename(name)
        self.assertEqual(metadata.original_title, "[Circle [Alias] (作者)] 原名")
        self.assertEqual(metadata.authors, [])
        self.assertTrue(any("嵌套" in warning for warning in warnings))
        self.assertTrue(metadata.full_color)

    def test_incomplete_author_group_does_not_delete_title(self):
        metadata, warnings = parse_filename("[Circle (作者] 完整作品名 [无修正]")
        self.assertEqual(metadata.original_title, "[Circle (作者] 完整作品名")
        self.assertEqual(metadata.authors, [])
        self.assertTrue(any("括号不完整" in warning for warning in warnings))

    def test_unclosed_square_bracket_preserves_following_title(self):
        name = "[Circle (作者) 完整作品名 [全彩]"
        metadata, warnings = parse_filename(name)
        self.assertEqual(metadata.original_title, name)
        self.assertEqual(metadata.authors, [])
        self.assertTrue(warnings)

    def test_invalid_date_is_not_an_author_or_guessed_month(self):
        for date in ("2019.00", "2019.13", "0000.10", "2019.abc", "2019"):
            with self.subTest(date=date):
                metadata, warnings = parse_filename(f"[{date}] [作者] 作品")
                self.assertEqual((metadata.year, metadata.month), ("", ""))
                self.assertEqual(metadata.authors, ["作者"])
                self.assertEqual(metadata.original_title, f"[{date}] 作品")
                self.assertTrue(warnings)

    def test_date_is_only_extracted_at_start(self):
        metadata, warnings = parse_filename("[作者] 作品 [2019.10]")
        self.assertEqual((metadata.year, metadata.month), ("", ""))
        self.assertEqual(metadata.original_title, "作品 [2019.10]")
        self.assertTrue(warnings)

    def test_only_tags_reports_missing_title_without_losing_fields(self):
        metadata, warnings = parse_filename("[作者] [全彩]")
        self.assertEqual(metadata.original_title, "")
        self.assertEqual(metadata.authors, ["作者"])
        self.assertTrue(metadata.full_color)
        self.assertTrue(any("未识别到作品标题" in warning for warning in warnings))


class InitialMetadataTests(unittest.TestCase):
    def test_python_311_windows_junction_is_rejected(self):
        path = SimpleNamespace(is_symlink=lambda: False,
                               stat=lambda **kwargs: SimpleNamespace(st_file_attributes=0x400))
        self.assertTrue(is_link(path))
        path.stat = lambda **kwargs: SimpleNamespace(st_file_attributes=0x10)
        self.assertFalse(is_link(path))

    def test_folder_name_fallback_uses_parser(self):
        metadata, _ = Snapshot(Path("[作者] 作品 [全彩]"), "folder", []).initial_metadata()
        self.assertEqual(metadata.original_title, "作品")
        self.assertEqual(metadata.authors, ["作者"])
        self.assertTrue(metadata.full_color)

    def test_cbz_fallback_uses_stem_including_date_period(self):
        metadata, _ = Snapshot(Path("[2019.10] [作者] 作品.cbz"), "cbz", []).initial_metadata()
        self.assertEqual(metadata.original_title, "作品")
        self.assertEqual((metadata.year, metadata.month), ("2019", "10"))

    def test_valid_existing_xml_wins_over_filename_labels(self):
        xml = b"<ComicInfo><Series>XML title</Series><Writer>XML author</Writer><Year>2023</Year><Month>12</Month></ComicInfo>"
        metadata, warning = Snapshot(Path("[2019.10] [Folder Author] Folder Title [全彩]"), "folder", [], xml=xml).initial_metadata()
        self.assertEqual(metadata.original_title, "XML title")
        self.assertEqual(metadata.authors, ["XML author"])
        self.assertEqual((metadata.year, metadata.month), ("2023", "12"))
        self.assertFalse(metadata.full_color)
        self.assertEqual(metadata.original_xml, xml.decode())
        self.assertEqual(warning, "")

    def test_broken_xml_falls_back_with_visible_warning(self):
        metadata, warning = Snapshot(Path("[作者] 作品 [全彩]"), "folder", [], xml=b"<ComicInfo>broken").initial_metadata()
        self.assertEqual(metadata.original_title, "作品")
        self.assertEqual(metadata.authors, ["作者"])
        self.assertTrue(metadata.full_color)
        self.assertIn("原 ComicInfo", warning)


class MonthMappingTests(unittest.TestCase):
    def test_month_round_trip_preserves_other_xml_fields(self):
        metadata = from_comicinfo(
            b"<ComicInfo><Series>Title</Series><Number>1</Number><Writer>Author</Writer><AgeRating>Everyone</AgeRating><Month>1</Month><Month>2</Month><CustomField>Keep me</CustomField></ComicInfo>", "fallback"
        )
        self.assertEqual(metadata.month, "1")
        metadata.month = "10"
        xml = to_comicinfo(metadata, 1)
        root = ET.fromstring(xml)
        self.assertEqual([node.text for node in root.findall("Month")], ["10"])
        self.assertEqual(root.findtext("CustomField"), "Keep me")
        self.assertEqual(from_comicinfo(xml, "fallback").month, "10")

    def test_empty_month_removes_existing_month(self):
        metadata = from_comicinfo(b"<ComicInfo><Series>Title</Series><Number>1</Number><Writer>Author</Writer><AgeRating>Everyone</AgeRating><Month>1</Month></ComicInfo>", "fallback")
        metadata.month = ""
        self.assertIsNone(ET.fromstring(to_comicinfo(metadata, 1)).find("Month"))

    def test_old_metadata_without_month_is_compatible(self):
        self.assertEqual(Metadata.from_dict({"original_title": "Title", "authors": ["Author"]}).month, "")

    def test_month_validation_is_bounded_and_optional(self):
        for month in ("", "1", "01", "12"):
            with self.subTest(month=month):
                Metadata(original_title="Title", authors=["Author"], month=month, age_rating="Everyone").validate(1)
        for month in ("0", "13", "-1", "1.5", "Jan", "001"):
            with self.subTest(month=month):
                with self.assertRaisesRegex(ValueError, "月份"):
                    Metadata(original_title="Title", authors=["Author"], month=month, age_rating="Everyone").validate(1)


if __name__ == "__main__":
    unittest.main()
