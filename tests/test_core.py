from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import zipfile

from comic_manage.metadata import Metadata, from_comicinfo, parse_xml, to_comicinfo
from comic_manage.packaging import export_cbz, make_plan, safe_segment, verify_archive
from comic_manage.repository import Repository
from comic_manage.sources import Cancelled, discover, inspect_source, source_id


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / "漫画库"
        self.book = self.library / "作者" / "日本語の作品"
        self.book.mkdir(parents=True)
        for name, data in [("10.jpg", b"page-ten"), ("2.jpg", b"page-two"), ("1.PNG", b"page-one")]:
            (self.book / name).write_bytes(data)
        self.output = self.root / "成品"
        self.repo = Repository(self.root / "local" / "catalog.sqlite3")
        self.snapshot = inspect_source(self.book)
        self.comic_id = self.repo.upsert_scan(self.snapshot)
        self.metadata = Metadata(original_title="日本語の作品 & <原名>", chinese_title="完整中文名",
                                 authors=["作者甲", "作者乙"], translated=True, language="zh-Hans",
                                 translator="测试汉化组", uncensored=True, color="全彩", genre="漫画", age_rating="R18+")

    def tearDown(self):
        self.temp.cleanup()

    def approve(self):
        self.repo.save(self.comic_id, self.metadata)
        self.repo.approve(self.comic_id, inspect_source(self.book))
        return self.repo.get(self.comic_id)

    def test_title_series_and_namespaced_tags_with_xml_escaping(self):
        root = parse_xml(to_comicinfo(self.metadata, 3))
        self.assertIsNone(root.find("Series"))
        self.assertIsNone(root.find("LocalizedSeries"))
        self.assertEqual(root.findtext("Title"), "日本語の作品 & <原名>")
        self.assertIsNone(root.find("BlackAndWhite"))
        self.assertEqual(root.findtext("Tags"), "色彩:全彩, 修正状态:无修正")
        self.assertEqual(root.findtext("Writer"), "作者甲, 作者乙")
        self.assertEqual(root.findtext("PageCount"), "3")

    def test_raw_omits_localized_and_reverse_tags(self):
        metadata = replace(self.metadata, title=self.metadata.original_title, translator="", translated=False, uncensored=False, full_color=False,
                           language="ja", color="", tags=["生肉", "有修正", "黑白", "校园"])
        root = parse_xml(to_comicinfo(metadata, 3))
        self.assertIsNone(root.find("LocalizedSeries"))
        self.assertIsNone(root.find("Translator"))
        self.assertEqual(root.findtext("Tags"), "校园")
        self.assertIsNone(root.find("Series"))

    def test_import_preserves_unknown_fields_and_cover(self):
        data = '<ComicInfo><Series>Chinese</Series><LocalizedSeries>Japanese</LocalizedSeries><LanguageISO>zh-Hans</LanguageISO><Writer>A, B</Writer><Translator>Group</Translator><Genre>漫画</Genre><Tags>日常</Tags><AgeRating>Everyone</AgeRating><Number>7</Number><Web>https://example.org</Web><Pages><Page Image="2" Type="FrontCover"/></Pages></ComicInfo>'.encode()
        metadata = from_comicinfo(data, "fallback")
        self.assertEqual(metadata.cover_index, 2)
        root = parse_xml(to_comicinfo(metadata, 3))
        self.assertEqual(root.findtext("Number"), "7")
        self.assertEqual(root.findtext("Web"), "https://example.org")
        self.assertEqual(root.find("./Pages/Page").get("Image"), "2")

    def test_required_metadata_and_cover_bounds(self):
        for metadata in [replace(self.metadata, title=""), replace(self.metadata, age_rating="Unknown"),
                         replace(self.metadata, authors=[]), replace(self.metadata, year="xx"),
                         replace(self.metadata, cover_index=3), replace(self.metadata, summary="bad\x00text")]:
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                to_comicinfo(metadata, 3)

    def test_dtd_and_wrong_xml_rejected(self):
        for data in [b'<!DOCTYPE ComicInfo [<!ENTITY x "hello">]><ComicInfo/>', b'<Other/>', b'<ComicInfo>']:
            with self.subTest(data=data), self.assertRaises(Exception):
                parse_xml(data)

    def test_scan_natural_order_and_output_exclusion(self):
        self.assertEqual([m.name for m in self.snapshot.pages], ["1.PNG", "2.jpg", "10.jpg"])
        nested = self.book / "另一作品"
        nested.mkdir()
        (nested / "1.jpg").write_bytes(b"nested")
        self.assertEqual(len(list(discover(self.library))), 2)
        self.assertEqual(len(list(discover(self.library, exclude=[nested]))), 1)

    def test_scan_preserves_edits_and_approval_until_source_changes(self):
        self.approve()
        self.repo.upsert_scan(inspect_source(self.book))
        self.assertEqual(self.repo.get(self.comic_id).status, "approved")
        (self.book / "3.jpg").write_bytes(b"new page")
        self.repo.upsert_scan(inspect_source(self.book))
        comic = self.repo.get(self.comic_id)
        self.assertEqual(comic.status, "draft")
        self.assertEqual(comic.metadata.chinese_title, "完整中文名")
        self.assertEqual(comic.page_count, 4)

    def test_save_invalidates_approval(self):
        self.approve()
        self.repo.save(self.comic_id, replace(self.metadata, translator="新组"))
        with self.assertRaises(ValueError):
            make_plan(self.repo.get(self.comic_id), self.output)

    def test_same_title_different_authors_remain_independent_oneshots(self):
        self.approve()
        other = self.library / "另一本"
        other.mkdir()
        (other / "1.jpg").write_bytes(b"other")
        snapshot = inspect_source(other)
        identity = self.repo.upsert_scan(snapshot)
        self.repo.save(identity, replace(self.metadata, authors=["其他作者"]))
        self.repo.approve(identity, snapshot)
        first = make_plan(self.repo.get(self.comic_id), self.output)
        second = make_plan(self.repo.get(identity), self.output)
        self.assertEqual(first.destination.parent.name, "_oneshots")
        self.assertEqual(first.destination.parent, second.destination.parent)
        self.assertNotEqual(first.destination, second.destination)

    def test_successful_package_is_byte_exact_and_source_untouched(self):
        original = {p.name: p.read_bytes() for p in self.book.iterdir()}
        plan = make_plan(self.approve(), self.output)
        report = export_cbz(plan)
        with zipfile.ZipFile(plan.destination) as archive:
            self.assertEqual(archive.namelist(), ["pages/00001.png", "pages/00002.jpg", "pages/00003.jpg", "ComicInfo.xml"])
            for old, new in zip(["1.PNG", "2.jpg", "10.jpg"], archive.namelist()):
                self.assertEqual(archive.read(new), original[old])
        self.assertEqual({p.name: p.read_bytes() for p in self.book.iterdir()}, original)
        self.assertEqual(report["page_count"], 3)
        self.repo.mark_packaged(self.comic_id, plan.destination, report)
        self.assertEqual(self.repo.get(self.comic_id).status, "packaged")

    def test_existing_output_is_never_overwritten(self):
        plan = make_plan(self.approve(), self.output)
        plan.destination.parent.mkdir(parents=True)
        plan.destination.write_bytes(b"keep me")
        with self.assertRaises(FileExistsError):
            export_cbz(plan)
        self.assertEqual(plan.destination.read_bytes(), b"keep me")
        self.assertFalse(list(self.output.rglob("*.partial")))

    def test_changed_source_blocks_approved_and_prepared_exports(self):
        comic = self.approve()
        plan = make_plan(comic, self.output)
        (self.book / "2.jpg").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "变化"):
            make_plan(comic, self.output)
        with self.assertRaisesRegex(ValueError, "变化"):
            export_cbz(plan)
        self.assertFalse(plan.destination.exists())

    def test_change_during_export_discards_temporary_file(self):
        plan = make_plan(self.approve(), self.output)
        def mutate(message):
            if "校验" in message:
                (self.book / "new.jpg").write_bytes(b"late change")
        with self.assertRaisesRegex(ValueError, "变化"):
            export_cbz(plan, progress=mutate)
        self.assertFalse(plan.destination.exists())
        self.assertFalse(list(self.output.rglob("*.partial")))

    def test_cancel_mid_package_cleans_up(self):
        cancel = Event()
        plan = make_plan(self.approve(), self.output)
        with self.assertRaises(Cancelled):
            export_cbz(plan, cancel=cancel, progress=lambda _: cancel.set())
        self.assertFalse(plan.destination.exists())
        self.assertFalse(list(self.output.rglob("*.partial")))

    def test_verification_failure_does_not_publish(self):
        plan = make_plan(self.approve(), self.output)
        with patch("comic_manage.packaging.verify_archive", side_effect=ValueError("bad digest")):
            with self.assertRaises(ValueError):
                export_cbz(plan)
        self.assertFalse(plan.destination.exists())
        self.assertFalse(list(self.output.rglob("*.partial")))

    def test_verify_detects_content_corruption(self):
        archive_path = self.root / "wrong.cbz"
        xml = to_comicinfo(self.metadata, 1)
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("pages/00001.jpg", b"wrong")
            archive.writestr("ComicInfo.xml", xml)
        with self.assertRaisesRegex(ValueError, "内容不一致"):
            verify_archive(archive_path, {"pages/00001.jpg": hashlib.sha256(b"right").hexdigest()}, xml)

    def test_output_inside_source_rejected(self):
        with self.assertRaisesRegex(ValueError, "独立目录"):
            make_plan(self.approve(), self.book / "成品")

    def test_cbz_import_repackage_retains_attachments(self):
        path = self.library / "原始.cbz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("nested/10.jpg", b"ten")
            archive.writestr("nested/2.jpg", b"two")
            archive.writestr("readme.txt", "原始附件".encode())
            archive.writestr("ComicInfo.xml", to_comicinfo(self.metadata, 2))
        snapshot = inspect_source(path)
        identity = self.repo.upsert_scan(snapshot)
        self.repo.approve(identity, snapshot)
        plan = make_plan(self.repo.get(identity), self.output)
        export_cbz(plan)
        with zipfile.ZipFile(plan.destination) as archive:
            self.assertEqual(archive.read("extras/readme.txt").decode(), "原始附件")
            self.assertEqual(archive.read("pages/00001.jpg"), b"two")
            self.assertEqual(archive.namelist().count("ComicInfo.xml"), 1)

    def test_malicious_zip_paths_duplicates_and_corrupt_archives(self):
        cases = [["../bad.jpg"], ["C:/bad.jpg"], ["a\\b.jpg"], ["1.jpg", "1.JPG"]]
        for i, names in enumerate(cases):
            path = self.root / f"bad{i}.cbz"
            with zipfile.ZipFile(path, "w") as archive:
                for name in names:
                    info = zipfile.ZipInfo("placeholder")
                    info.filename = name
                    archive.writestr(info, b"x")
            with self.subTest(names=names), self.assertRaises(ValueError):
                inspect_source(path)
        (self.library / "broken.cbz").write_bytes(b"not a zip")
        errors = []
        self.assertEqual(len(list(discover(self.library, on_error=lambda p, e: errors.append(e)))), 1)
        self.assertEqual(len(errors), 1)

    def test_bad_comicinfo_is_visible_warning_not_scan_crash(self):
        (self.book / "ComicInfo.xml").write_text("broken", encoding="utf-8")
        snapshot = inspect_source(self.book)
        metadata, warning = snapshot.initial_metadata()
        self.assertTrue(warning)
        self.assertEqual(metadata.original_title, self.book.name)

    def test_unique_output_and_windows_filenames(self):
        self.assertEqual(safe_segment("CON"), "_CON")
        self.assertEqual(safe_segment('a:b?<>. '), "a_b___")
        self.assertLessEqual(len(safe_segment("😀" * 120).encode("utf-16-le")), 180)
        first = make_plan(self.approve(), self.output).destination
        changed_id = replace(self.repo.get(self.comic_id), id="0123456789abcdef")
        self.assertNotEqual(first, make_plan(changed_id, self.output).destination)

    def test_catalog_backup_contains_metadata_and_audit(self):
        self.approve()
        destination = self.root / "backup.sqlite3"
        self.repo.backup(destination)
        copy = Repository(destination)
        self.assertEqual(copy.get(self.comic_id).metadata.authors, self.metadata.authors)
        with copy.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 3)
        with self.assertRaises(FileExistsError):
            self.repo.backup(destination)


if __name__ == "__main__":
    unittest.main()
