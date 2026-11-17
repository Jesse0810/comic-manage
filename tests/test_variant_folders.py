"""Explicit parent-work/version leaf inference without altering source identity."""
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET

from comic_manage.folder_variants import recognize_version_folder
from comic_manage.metadata import Metadata, to_comicinfo
from comic_manage.sources import discover, inspect_source, source_id


class VariantFolderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.author = self.root / "[pink-noise (みずいろめがね)] 合集"
        self.work = self.author / "[2011.08] Cattleya & Lana ～武器屋の母子～ [穿刺個人漢化]"
        self.with_text = self.pages(self.work / "有文字", 2)
        self.without_text = self.pages(self.work / "无文字", 3)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def pages(directory, count=1):
        directory.mkdir(parents=True, exist_ok=True)
        for number in range(count):
            (directory / f"{number + 1}.png").write_bytes(b"read-only discovery fixture")
        return directory

    def test_whole_leaf_aliases_only(self):
        for name, expected in (("有文字", "有文字"), ("无文字", "无文字"), ("无字版", "无文字"),
                               ("【有文字】", "有文字"), ("[无文字]", "无文字"), ("［无字版］", "无文字")):
            with self.subTest(name=name):
                self.assertEqual(recognize_version_folder(name), expected)
        for name in ("作品 有文字", "无文字的世界", "[无文字] 作品", "【无文字]", "[[有文字]]", "有文字版"):
            self.assertEqual(recognize_version_folder(name), "")

    def test_parent_title_and_explicit_author_collection_are_shared_by_independent_versions(self):
        one, two = inspect_source(self.with_text), inspect_source(self.without_text)
        first, warning = one.initial_metadata()
        second, _ = two.initial_metadata()
        self.assertEqual(first.title, "Cattleya & Lana ～武器屋の母子～")
        self.assertEqual(second.title, first.title)
        self.assertEqual(first.original_title, first.title)
        self.assertEqual((first.text_version, second.text_version), ("有文字", "无文字"))
        self.assertEqual(first.work_group, second.work_group)
        self.assertIn(first.title, first.work_group)
        self.assertIn("みずいろめがね", first.work_group)
        self.assertEqual(first.authors, ["みずいろめがね"])
        self.assertEqual(second.authors, first.authors)
        self.assertEqual(first.publisher, "pink-noise")
        self.assertEqual(first.translator, "穿刺個人漢化")
        self.assertEqual(second.translator, first.translator)
        self.assertEqual(second.language, first.language)
        self.assertEqual((first.year, first.month), ("2011", "8"))
        self.assertEqual((len(one.pages), len(two.pages)), (2, 3))
        self.assertNotEqual(source_id(one.path), source_id(two.path))
        self.assertEqual((first.collection_mode, first.series, first.number, first.count), ("oneshot", "", "", ""))
        self.assertEqual(first.age_rating, "Unknown")
        self.assertFalse(first.uncensored)
        self.assertTrue(warning)

    def test_direct_selected_leaf_has_same_hints_as_recursive_discovery(self):
        direct = inspect_source(self.without_text)
        found = list(discover(self.work))
        other = next(snapshot for snapshot in found if snapshot.path == self.without_text.resolve())
        self.assertEqual(direct.initial_metadata(), other.initial_metadata())
        self.assertEqual(len(found), 2)
        self.assertNotIn("合集", direct.initial_metadata()[0].title)

    def test_distinct_known_authors_keep_same_named_works_in_different_groups(self):
        left = self.pages(self.root / "[作者甲] 相同作品" / "有文字")
        self.pages(left.parent / "无文字")
        right = self.pages(self.root / "[作者乙] 相同作品" / "有文字")
        self.pages(right.parent / "无文字")
        first, _ = inspect_source(left).initial_metadata()
        second, _ = inspect_source(right).initial_metadata()
        self.assertEqual(first.title, second.title)
        self.assertNotEqual(first.work_group, second.work_group)
        self.assertIn("作者甲", first.work_group)
        self.assertIn("作者乙", second.work_group)

    def test_unknown_authors_use_source_marker_without_leaking_parent_path(self):
        left = self.pages(self.root / "area-one" / "同名作品" / "有文字")
        self.pages(left.parent / "无文字")
        right = self.pages(self.root / "area-two" / "同名作品" / "有文字")
        self.pages(right.parent / "无文字")
        first, warning = inspect_source(left).initial_metadata()
        second, _ = inspect_source(right).initial_metadata()
        self.assertNotEqual(first.work_group, second.work_group)
        self.assertNotIn(str(self.root), first.work_group)
        self.assertIn("来源", first.work_group)
        self.assertIn("来源短标识", warning)

    def test_single_explicit_work_leaf_gets_version_without_fabricated_series(self):
        leaf = self.pages(self.root / "[作者甲] 一部作品" / "【无文字】")
        value, _ = inspect_source(leaf).initial_metadata()
        self.assertEqual(value.title, "一部作品")
        self.assertEqual(value.text_version, "无文字")
        self.assertTrue(value.work_group)
        self.assertEqual((value.collection_mode, value.series, value.number), ("oneshot", "", ""))

    def test_generic_parent_never_becomes_work_title_or_group(self):
        leaf = self.pages(self.root / "[作者] 合集" / "有文字")
        self.pages(leaf.parent / "无文字")
        value, warning = inspect_source(leaf).initial_metadata()
        self.assertNotEqual(value.title, "合集")
        self.assertEqual(value.work_group, "")
        self.assertEqual(value.text_version, "有文字")
        self.assertIn("不是明确作品名", warning)

    def test_real_title_containing_version_words_does_not_read_parent(self):
        leaf = self.pages(self.root / "[Other Author] unrelated parent" / "无文字的世界")
        value, _ = inspect_source(leaf).initial_metadata()
        self.assertEqual(value.title, "无文字的世界")
        self.assertEqual(value.text_version, "")
        self.assertEqual(value.work_group, "")
        self.assertEqual(value.authors, [])
        self.assertEqual(inspect_source(leaf).metadata_context, {})

    def test_sibling_context_change_does_not_change_image_fingerprint(self):
        leaf = self.pages(self.root / "[Author] One Work" / "有文字")
        before = inspect_source(leaf)
        (leaf.parent / "无文字").mkdir()
        after = inspect_source(leaf)
        self.assertEqual(before.fingerprint, after.fingerprint)
        self.assertNotEqual(before.metadata_context_fingerprint, after.metadata_context_fingerprint)
        self.assertEqual(before.initial_metadata()[0].text_version, "有文字")
        self.assertEqual(before.metadata_context["sibling_versions"], ["有文字"])
        self.assertEqual(after.metadata_context["sibling_versions"], ["无文字", "有文字"])

    def test_context_is_captured_instead_of_reopening_parent_when_reading_metadata(self):
        before = inspect_source(self.with_text)
        expected = before.initial_metadata()
        (self.work / "【无字版】").mkdir()
        self.assertEqual(before.initial_metadata(), expected)
        self.assertNotEqual(before.metadata_context_fingerprint,
                            inspect_source(self.with_text).metadata_context_fingerprint)

    def test_leaf_xml_authority_survives_parent_and_version_conflicts(self):
        xml = b"""<ComicInfo><Title>Local title</Title><Writer>Local author</Writer>
        <Translator>Local translator</Translator><LanguageISO>ja</LanguageISO><Tags>work,
        \xe4\xbd\x9c\xe5\x93\x81\xe7\xbb\x84:Explicit Work,\xe6\x96\x87\xe5\xad\x97\xe7\x89\x88\xe6\x9c\xac:\xe6\x97\xa0\xe6\x96\x87\xe5\xad\x97</Tags></ComicInfo>"""
        (self.with_text / "ComicInfo.xml").write_bytes(xml)
        value, warning = inspect_source(self.with_text).initial_metadata()
        self.assertEqual(value.title, "Local title")
        self.assertEqual(value.authors, ["Local author"])
        self.assertEqual(value.translator, "Local translator")
        self.assertEqual(value.language, "ja")
        self.assertEqual(value.work_group, "Explicit Work")
        self.assertEqual(value.text_version, "无文字")
        self.assertIn("保留 ComicInfo 原值", warning)
        self.assertEqual(value.original_xml.encode(), xml)

    def test_xml_missing_native_values_is_not_filled_from_parent_edition_flags(self):
        (self.without_text / "ComicInfo.xml").write_text("<ComicInfo><Title>Same Work</Title></ComicInfo>", encoding="utf8")
        value, _ = inspect_source(self.without_text).initial_metadata()
        self.assertEqual(value.title, "Same Work")
        self.assertEqual(value.authors, [])
        self.assertEqual(value.translator, "")
        self.assertEqual(value.language, "")
        self.assertEqual(value.text_version, "无文字")
        self.assertTrue(value.work_group)
        self.assertEqual(value.age_rating, "Unknown")

    def test_xml_missing_title_uses_captured_work_name_instead_of_version_label(self):
        (self.without_text / "ComicInfo.xml").write_text("<ComicInfo><Writer>本版作者</Writer></ComicInfo>", encoding="utf8")
        value, _ = inspect_source(self.without_text).initial_metadata()
        self.assertEqual(value.title, "Cattleya & Lana ～武器屋の母子～")
        self.assertEqual(value.original_title, value.title)
        self.assertEqual(value.authors, ["本版作者"])
        self.assertEqual(value.text_version, "无文字")

    def test_explicit_generic_xml_title_is_preserved_with_a_parent_candidate_warning(self):
        (self.with_text / "ComicInfo.xml").write_text("<ComicInfo><Title>有文字</Title></ComicInfo>", encoding="utf8")
        value, warning = inspect_source(self.with_text).initial_metadata()
        self.assertEqual(value.title, "有文字")
        self.assertIn("父作品名候选", warning)
        self.assertIn("Cattleya & Lana", warning)

    def test_single_plain_parent_has_version_hint_but_no_unconfirmed_group(self):
        leaf = self.pages(self.root / "Plain Work Name" / "无文字")
        value, warning = inspect_source(leaf).initial_metadata()
        self.assertEqual(value.title, "Plain Work Name")
        self.assertEqual(value.text_version, "无文字")
        self.assertEqual(value.work_group, "")
        self.assertIn("仅发现单个版本", warning)

    def test_explicit_empty_or_conflicting_namespaced_xml_is_not_resurrected(self):
        for tags in ("作品组:,文字版本:", "作品组:A,作品组:B,文字版本:有文字,文字版本:无文字"):
            with self.subTest(tags=tags):
                xml = f"<ComicInfo><Title>Work</Title><Tags>{tags}</Tags></ComicInfo>"
                (self.with_text / "ComicInfo.xml").write_text(xml, encoding="utf8")
                value, warning = inspect_source(self.with_text).initial_metadata()
                self.assertEqual(value.work_group, "")
                self.assertEqual(value.text_version, "")
                self.assertTrue(warning)

    def test_parent_images_and_child_editions_stay_three_independent_sources(self):
        (self.work / "cover.png").write_bytes(b"parent edition")
        found = list(discover(self.work))
        self.assertEqual(len(found), 3)
        self.assertEqual(sum(len(snapshot.pages) for snapshot in found), 6)
        self.assertEqual(len({source_id(snapshot.path) for snapshot in found}), 3)

    def test_parent_explicit_group_is_shared_and_xml_exports_only_namespaced_tags(self):
        work = self.root / "[作者] 原名 [作品组：Exact CaseＡ Group]"
        first = self.pages(work / "[有文字]")
        second = self.pages(work / "［无文字］")
        left, _ = inspect_source(first).initial_metadata()
        right, _ = inspect_source(second).initial_metadata()
        self.assertEqual(left.work_group, "Exact CaseＡ Group")
        self.assertEqual(right.work_group, left.work_group)
        left.genre = "漫画"
        left.age_rating = "Everyone"
        xml = ET.fromstring(to_comicinfo(left, 1))
        self.assertIn("作品组:Exact CaseＡ Group", xml.findtext("Tags"))
        self.assertIn("文字版本:有文字", xml.findtext("Tags"))
        self.assertIsNone(xml.find("Series"))
        self.assertIsNone(xml.find("Number"))
        self.assertIsNone(xml.find("作品组"))


if __name__ == "__main__":
    unittest.main()
