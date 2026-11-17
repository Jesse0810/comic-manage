"""Real legacy migrations and remove/restore histories, rather than fresh drafts."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

from comic_manage.repository import Repository
from comic_manage.service import ComicService
from comic_manage.sources import inspect_source

if __package__:
    from .variant_upgrade_fixture import make_legacy_catalog, migrated_restore_fixture
else:
    from variant_upgrade_fixture import make_legacy_catalog, migrated_restore_fixture


def raw_rows(repo):
    with repo.connect() as db:
        return {row["id"]: dict(row) for row in db.execute("SELECT * FROM comics")}


def sqlite_files(root):
    result = set()
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(16) == b"SQLite format 3\x00":
                    result.add(path.resolve())
    return result


class LegacyVariantUpgradeRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="comic-variant-upgrade-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_first_bootstrap_repairs_real_migrated_restored_labels_with_one_prior_backup(self):
        fixture = migrated_restore_fixture(self.root)
        repo = fixture["repo"]
        before = raw_rows(repo)
        prior_files = sqlite_files(self.root)
        self.assertEqual(repo.setting("metadata_rules"), "1.3")
        self.assertTrue(all("v1.2" in row["warning"] for row in before.values()))
        self.assertEqual([json.loads(before[identity]["metadata"])["title"] for identity in fixture["identities"][:2]], ["有文字", "无文字"])
        service = ComicService(self.root / "data")
        result = service.call("bootstrap")
        self.assertEqual(result["metadataRules"], "1.3")
        self.assertEqual(len(result["comics"]), 4)
        after = raw_rows(service.repo)
        variants = [service.repo.get(identity) for identity in fixture["identities"][:2]]
        self.assertEqual({comic.metadata.title for comic in variants}, {fixture["expected_title"]})
        self.assertEqual({comic.metadata.original_title for comic in variants}, {fixture["expected_title"]})
        self.assertEqual({comic.metadata.text_version for comic in variants}, {"有文字", "无文字"})
        self.assertEqual(len({comic.metadata.work_group for comic in variants}), 1)
        self.assertTrue(variants[0].metadata.work_group)
        for comic in variants:
            old = json.loads(before[comic.id]["metadata"])
            for field in ("authors", "genre", "tags", "summary", "age_rating"):
                self.assertEqual(comic.metadata.to_dict()[field], old[field], field)
            self.assertEqual(comic.status, "draft")
            self.assertEqual((comic.source, comic.fingerprint, comic.page_count, comic.output),
                             tuple(before[comic.id][key] for key in ("source", "fingerprint", "page_count", "output")))
            self.assertIn("v1.2", comic.warning, "Historical migration information is not fabricated or erased")
        for identity in fixture["identities"][2:]:
            self.assertEqual(after[identity], before[identity], "Ordinary hand-edited records remain byte-for-byte intact")
        new_backups = sqlite_files(self.root) - prior_files
        self.assertEqual(len(new_backups), 1)
        with closing(sqlite3.connect(next(iter(new_backups)))) as db:
            backed_up = dict(db.execute("SELECT id,metadata FROM comics"))
        self.assertEqual(backed_up, {identity: row["metadata"] for identity, row in before.items()}, "The backup contains pre-repair metadata")

    def test_bootstrap_repair_is_idempotent_across_refresh_and_restart(self):
        fixture = migrated_restore_fixture(self.root)
        service = ComicService(self.root / "data")
        first = service.call("bootstrap")
        self.assertEqual({comic["metadata"]["title"] for comic in first["comics"] if comic["id"] in fixture["identities"][:2]}, {fixture["expected_title"]})
        before = raw_rows(service.repo)
        backups = sqlite_files(self.root)
        service.call("bootstrap")
        reopened = ComicService(self.root / "data")
        reopened.call("bootstrap")
        self.assertEqual(raw_rows(reopened.repo), before)
        self.assertEqual(sqlite_files(self.root), backups)
        with reopened.repo.connect() as db:
            repairs = db.execute("SELECT comic_id,COUNT(*) FROM events WHERE action='version_folder_repair' GROUP BY comic_id").fetchall()
        self.assertEqual({row[0]: row[1] for row in repairs}, {identity: 1 for identity in fixture["identities"][:2]})

    def test_removed_preview_and_restore_repair_the_same_selected_record_without_bootstrap(self):
        fixture = make_legacy_catalog(self.root)
        repo = Repository(fixture["path"])
        targets = fixture["identities"][:2]
        repo.remove(targets)
        service = ComicService(self.root / "data")
        ordinary_before = {identity: raw_rows(service.repo)[identity] for identity in fixture["identities"][2:]}
        preview = service.call("prepare_import", {"paths": [str(fixture["parent"])]})
        self.assertEqual(len(preview["items"]), 2, preview["errors"])
        self.assertTrue(all(item["import_status"] == "removed" for item in preview["items"]))
        self.assertEqual({item["metadata"]["title"] for item in preview["items"]}, {fixture["expected_title"]})
        self.assertTrue(all(service.repo.is_removed(identity) for identity in targets), "Preview stays read-only")
        result = service.call("commit_import", {"token": preview["token"], "ids": [targets[0]]})
        self.assertEqual(result["count"], 1)
        restored = service.repo.get(targets[0])
        self.assertEqual((restored.metadata.title, restored.metadata.original_title), (fixture["expected_title"], fixture["expected_title"]))
        self.assertEqual(restored.metadata.text_version, "有文字")
        self.assertEqual(restored.metadata.authors, ["pink-noise (みずいろめがね)"])
        self.assertTrue(service.repo.is_removed(targets[1]), "An unchecked sibling is never restored")
        self.assertEqual(service.repo.get(targets[1], include_removed=True).metadata.title, "无文字")
        for identity, before in ordinary_before.items():
            self.assertEqual(raw_rows(service.repo)[identity], before)

    def test_bootstrap_never_restores_hidden_records(self):
        fixture = migrated_restore_fixture(self.root)
        fixture["repo"].remove(fixture["identities"][:2])
        before = raw_rows(fixture["repo"])
        service = ComicService(self.root / "data")
        result = service.call("bootstrap")
        self.assertEqual(len(result["comics"]), 2)
        self.assertEqual(raw_rows(service.repo), before)
        self.assertTrue(all(service.repo.is_removed(identity) for identity in fixture["identities"][:2]))

    def test_historical_review_and_own_xml_remain_protected(self):
        for name, options in (("reviewed", {"historical_approval": True}), ("xml", {"own_xml": True})):
            fixture = migrated_restore_fixture(self.root / name, **options)
            before = raw_rows(fixture["repo"])
            service = ComicService(self.root / name / "data")
            result = service.call("bootstrap")
            self.assertEqual(result["metadataRules"], "1.3")
            self.assertEqual(raw_rows(service.repo), before, name)

    def test_missing_source_or_new_pages_do_not_rewrite_catalog_on_startup(self):
        for name, mutate in (("missing", lambda folder: (folder / "1.png").unlink()),
                             ("changed", lambda folder: (folder / "extra.png").write_bytes(b"new page"))):
            fixture = migrated_restore_fixture(self.root / name)
            for folder in fixture["folders"][:2]:
                mutate(folder)
            before = raw_rows(fixture["repo"])
            service = ComicService(self.root / name / "data")
            service.call("bootstrap")
            self.assertEqual(raw_rows(service.repo), before, name)

    def test_startup_deadline_returns_original_data_and_late_source_reader_cannot_write(self):
        fixture = migrated_restore_fixture(self.root)
        service = ComicService(self.root / "data")
        before = raw_rows(service.repo)
        before_files = sqlite_files(self.root)
        entered, release, finished = Event(), Event(), Event()
        original_inspect = inspect_source

        def blocked_read(path):
            entered.set()
            release.wait(5)
            try:
                return original_inspect(path)
            finally:
                finished.set()

        with patch("comic_manage.service.LEGACY_VARIANT_CHECK_SECONDS", 0.05), \
                patch("comic_manage.service.inspect_source", side_effect=blocked_read) as source_read:
            try:
                start = time.monotonic()
                result = service.call("bootstrap")
                self.assertLess(time.monotonic() - start, 1.5, "An unavailable source cannot block editor startup")
                self.assertTrue(entered.wait(1))
                self.assertEqual(result["metadataRules"], "1.3")
                self.assertEqual(raw_rows(service.repo), before)
                self.assertEqual(sqlite_files(self.root), before_files)
                release.set()
                self.assertTrue(finished.wait(2), "The late filesystem-only worker must finish in this fixture")
                second = service.call("bootstrap")
                self.assertEqual(len(second["comics"]), 4)
                self.assertEqual(source_read.call_count, 1, "The second bootstrap must not repeat the timed-out inspection")
                self.assertEqual(raw_rows(service.repo), before, "Late source results cannot mutate metadata")
                self.assertEqual(sqlite_files(self.root), before_files, "Late source results cannot create a backup or start repair")
            finally:
                release.set()
                finished.wait(2)

    def test_backup_database_or_filesystem_failure_preserves_the_whole_catalog(self):
        for name, error in (("sqlite", sqlite3.OperationalError("fixture backup unavailable")),
                            ("filesystem", OSError("fixture backup unavailable"))):
            fixture = migrated_restore_fixture(self.root / name)
            service = ComicService(self.root / name / "data")
            before = raw_rows(service.repo)
            files = sqlite_files(self.root / name)
            with patch.object(service.repo, "backup", side_effect=error) as backup:
                result = service.call("bootstrap")
            self.assertEqual(result["metadataRules"], "1.3")
            self.assertEqual(len(result["comics"]), 4)
            self.assertEqual(backup.call_count, 1)
            self.assertEqual(raw_rows(service.repo), before)
            self.assertEqual(sqlite_files(self.root / name), files)

    def test_offline_source_is_skipped_without_fabricating_parent_metadata(self):
        fixture = migrated_restore_fixture(self.root)
        service = ComicService(self.root / "data")
        before = raw_rows(service.repo)
        files = sqlite_files(self.root)
        with patch("comic_manage.service.inspect_source", side_effect=OSError("fixture NAS offline")):
            result = service.call("bootstrap")
        self.assertEqual(result["metadataRules"], "1.3")
        self.assertEqual(raw_rows(service.repo), before)
        self.assertEqual(sqlite_files(self.root), files)


if __name__ == "__main__":
    unittest.main()
