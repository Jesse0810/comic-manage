"""Import previews freeze version-directory hints and only commit checked sources."""
import json
from pathlib import Path
import tempfile
import unittest

from comic_manage.metadata import Metadata
from comic_manage.service import ComicService
from comic_manage.sources import inspect_source, source_id


class VersionFolderImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.collection = self.root / "[pink-noise (みずいろめがね)] 合集"
        self.work = self.collection / "[2011.08] Cattleya & Lana [穿刺個人漢化]"
        self.with_text = self.work / "有文字"
        self.without_text = self.work / "无文字"
        for folder, count in ((self.with_text, 2), (self.without_text, 3)):
            folder.mkdir(parents=True)
            for number in range(count):
                (folder / f"{number}.png").write_bytes(b"preserved-page-" + str(number).encode())
        self.service = ComicService(self.root / "data")

    def tearDown(self):
        self.temp.cleanup()

    def preview(self, path=None):
        return self.service.call("prepare_import", {"paths": [str(path or self.work)]})

    def commit(self, preview, ids):
        return self.service.call("commit_import", {"token": preview["token"], "ids": ids})

    def legacy(self, folder, **fields):
        snapshot = inspect_source(folder)
        identity = self.service.repo.upsert_scan(snapshot)
        old = Metadata(title=folder.name, original_title=folder.name, **fields)
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET metadata=? WHERE id=?", (json.dumps(old.to_dict()), identity))
        return identity

    def test_preview_uses_parent_metadata_and_keeps_versions_independent(self):
        preview = self.preview()
        self.assertEqual(preview["errors"], [])
        self.assertEqual(len(preview["items"]), 2)
        groups = set()
        for item in preview["items"]:
            metadata = item["metadata"]
            self.assertEqual(metadata["title"], "Cattleya & Lana")
            self.assertEqual(metadata["authors"], ["みずいろめがね"])
            self.assertEqual(metadata["publisher"], "pink-noise")
            self.assertEqual(metadata["translator"], "穿刺個人漢化")
            self.assertEqual((metadata["year"], metadata["month"]), ("2011", "8"))
            self.assertEqual(metadata["collection_mode"], "oneshot")
            self.assertEqual((metadata["series"], metadata["number"], metadata["volume"]), ("", "", ""))
            self.assertIn(metadata["text_version"], ("有文字", "无文字"))
            groups.add(metadata["work_group"])
        self.assertEqual(len(groups), 1)
        self.assertNotEqual(groups, {""})
        self.assertEqual(sorted(item["page_count"] for item in preview["items"]), [2, 3])
        self.assertEqual(self.service.repo.all(), [])

    def test_only_checked_version_imports_and_rescan_does_not_add_sibling(self):
        preview = self.preview()
        first_id = source_id(self.with_text)
        result = self.commit(preview, [first_id])
        self.assertEqual(result["count"], 1)
        self.service.call("scan")
        self.assertEqual([comic.id for comic in self.service.repo.all()], [first_id])
        second = self.preview(self.without_text)
        self.assertEqual(len(second["items"]), 1)
        self.assertEqual(second["items"][0]["metadata"]["work_group"], self.service.repo.get(first_id).metadata.work_group)
        self.commit(second, [source_id(self.without_text)])
        self.assertEqual(len(self.service.repo.all()), 2)

    def test_parent_version_structure_change_rejects_frozen_preview_without_image_change(self):
        independent = self.collection / "[作者] 另一作品" / "【有文字】"
        independent.mkdir(parents=True)
        (independent / "1.png").write_bytes(b"original")
        snapshot = inspect_source(independent)
        preview = self.preview(independent)
        (independent.parent / "【无文字】").mkdir()
        fresh = inspect_source(independent)
        self.assertEqual(snapshot.fingerprint, fresh.fingerprint)
        self.assertNotEqual(snapshot.metadata_context_fingerprint, fresh.metadata_context_fingerprint)
        result = self.commit(preview, [source_id(independent)])
        self.assertEqual(result["count"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("变化", result["errors"][0])
        self.assertEqual(self.service.repo.all(), [])

    def test_existing_preview_and_commit_share_the_same_safe_repair(self):
        identity = self.legacy(self.with_text)
        preview = self.preview(self.with_text)
        self.assertEqual(preview["items"][0]["metadata"]["title"], "Cattleya & Lana")
        self.assertEqual(self.service.repo.get(identity).metadata.title, "有文字", "Preview remains read-only")
        result = self.commit(preview, [identity])
        self.assertEqual((result["count"], result["skipped"]), (1, 0))
        saved = self.service.repo.get(identity)
        self.assertEqual(saved.metadata.to_dict(), preview["items"][0]["metadata"])
        self.assertEqual(saved.status, "draft")

    def test_rescan_repairs_legacy_label_but_preserves_manually_entered_author(self):
        identity = self.legacy(self.without_text, authors=["已确认作者"])
        with self.service.repo.connect() as db:
            self.service.repo._event(db, identity, "edit", "人工修改元数据")
        self.service.call("scan", {"roots": [str(self.without_text)]})
        saved = self.service.repo.get(identity)
        self.assertEqual(saved.metadata.title, "Cattleya & Lana")
        self.assertEqual(saved.metadata.authors, ["已确认作者"])
        self.assertEqual(saved.metadata.text_version, "无文字")
        self.assertTrue(saved.metadata.work_group)

    def test_manual_clearing_after_repair_does_not_reappear_on_scan_or_import(self):
        identity = self.legacy(self.with_text)
        self.service.call("scan", {"roots": [str(self.with_text)]})
        self.service.call("save", {"id": identity, "metadata": {"work_group": "", "text_version": ""}})
        self.service.call("scan", {"roots": [str(self.with_text)]})
        preview = self.preview(self.with_text)
        self.commit(preview, [identity])
        saved = self.service.repo.get(identity).metadata
        self.assertEqual((saved.work_group, saved.text_version), ("", ""))


if __name__ == "__main__":
    unittest.main()
