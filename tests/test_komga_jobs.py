"""Durable auto-sync tests use isolated catalogs, real archives and fake Komga I/O."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from comic_manage import komga_jobs as jobs
from comic_manage.komga_client import KomgaError
from comic_manage.metadata import Metadata, to_comicinfo
from comic_manage.repository import Repository
from comic_manage.sources import inspect_source


_image = BytesIO()
Image.new("RGB", (2, 3), "white").save(_image, format="PNG")
PNG = _image.getvalue()


class FakeKomga:
    def __init__(self):
        self.url = "http://127.0.0.1:49231/komga"
        self.cancel = None
        self.remote = {}
        self.series = {}
        self.calls = []
        self.failure = None
        self.before_request = lambda method, path: None
        self.before_patch = lambda: None
        self.after_patch = lambda record: None

    def libraries(self):
        self.calls.append(("GET", "libraries", None))
        if self.failure:
            raise self.failure
        return [{"id": "actual-library-id", "name": "漫画库"}]

    def books(self, library_id):
        self.calls.append(("POST", "books/list", library_id))
        self.before_request("POST", "books/list")
        return deepcopy(list(self.remote.values()))

    def request(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        self.before_request(method, path)
        parts = path.split("/")
        source = self.remote if parts[2] == "books" else self.series
        record = source[parts[3]]
        if method == "PATCH":
            self.before_patch()
            self.assert_only_index_fields(parts[2], body)
            record["metadata"].update(deepcopy(body))
            self.after_patch(record)
            return {}
        return deepcopy(record)

    @staticmethod
    def assert_only_index_fields(kind, body):
        allowed = {"notableEntities", "notableEntitiesLock"} if kind == "books" else {"alternateTitles", "alternateTitlesLock"}
        assert set(body) == allowed, body


class KomgaJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = Repository(self.root / "catalog.sqlite3")
        self.client = FakeKomga()
        self.output = self.root / "published"
        self.output.mkdir()
        self.secret = "synthetic-api-key-never-persisted"
        self.factory = patch.object(jobs, "KomgaClient", side_effect=self.make_client)
        self.factory.start()

    def tearDown(self):
        self.factory.stop()
        self.temp.cleanup()

    def make_client(self, url, api_key, *, cancel=None):
        assert url == self.client.url
        assert api_key == self.secret
        self.client.cancel = cancel
        return self.client

    def comic(self, name="one", *, packaged=True, **fields):
        folder = self.root / ("source-" + name)
        folder.mkdir()
        (folder / "page.png").write_bytes(PNG)
        snapshot = inspect_source(folder)
        identity = self.repo.upsert_scan(snapshot)
        values = {"title": "日本語の題名-" + name, "authors": ["作者Ａ"], "genre": "漫画", "age_rating": "Everyone",
                  "tags": ["日常"], "characters": ["博丽灵梦"], "original_works": ["东方Project"], "collection_mode": "oneshot",
                  "work_group": "CaseＡ Work  作者Ｂ", "text_version": "无文字"}
        values.update(fields)
        self.repo.save(identity, Metadata(**values))
        self.repo.approve(identity, snapshot)
        if packaged:
            destination = self.output / "nested" / "_oneshots" / (name + ".cbz")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr("pages/00001.png", PNG)
                archive.writestr("ComicInfo.xml", to_comicinfo(self.repo.get(identity).metadata, 1))
            self.repo.mark_packaged(identity, destination, {"test": True})
        return self.repo.get(identity)

    def enqueue(self, comic):
        return jobs.enqueue_export(self.repo, comic.id, self.output, self.client.url, "actual-library-id")

    def remote(self, comic, identity="book-one"):
        relative = Path(comic.output).relative_to(self.output).as_posix()
        self.client.remote[identity] = {"id": identity, "libraryId": "actual-library-id", "seriesId": identity + "-series",
                                        "oneshot": True, "name": Path(comic.output).stem, "url": "/nas/library/" + relative,
                                        "readProgress": {"page": 27}, "metadata": {"title": comic.metadata.display_title,
                                        "number": "1", "summary": "原生资料保留", "notableEntitiesLock": False,
                                        "notableEntities": [{"kind": "team", "name": "第三方队伍"}, {"kind": "character", "name": "旧角色"},
                                                            {"kind": "原作", "name": "旧原作"}]}}
        return self.client.remote[identity]

    def run_jobs(self, current_time=None, **kwargs):
        return jobs.run_due(self.repo, self.client.url, self.secret, "actual-library-id", current_time=current_time, **kwargs)

    def job(self):
        return jobs.list_jobs(self.repo)[0]

    def patches(self):
        return [call for call in self.client.calls if call[0] == "PATCH"]

    def test_approval_persists_without_any_network_or_invented_artifact(self):
        comic = self.comic(packaged=False)
        jobs.record_approval(self.repo, comic.id)
        reopened = Repository(self.repo.path)
        job = jobs.KomgaJobs(reopened).list()[0]
        self.assertEqual((job["status"], job["outputPath"], job["serverUrl"]), ("Approved", "", ""))
        self.assertEqual(self.run_jobs()["processed"], 0)
        self.assertEqual(self.client.calls, [])
        self.assertFalse(job["canRetry"])

    def test_automation_off_published_book_can_explicitly_enqueue_without_source_or_repackaging(self):
        comic = self.comic()
        jobs.record_approval(self.repo, comic.id)
        remembered = jobs.record_validated_export(self.repo, comic.id, self.output)
        self.assertEqual((remembered["status"], remembered["serverUrl"], remembered["libraryId"]), ("Approved", "", ""))
        self.assertTrue(remembered["canRetry"])
        self.assertEqual(remembered["relativePath"], "nested/_oneshots/one.cbz")
        original = Path(comic.output).read_bytes()
        (Path(comic.source) / "page.png").unlink()
        Path(comic.source).rmdir()
        self.assertEqual(self.run_jobs(current_time=1000)["processed"], 0)
        self.assertEqual(self.client.calls, [])
        self.repo = Repository(self.repo.path)
        response = jobs.retry_jobs(self.repo, [remembered["id"]], self.client.url, "actual-library-id")
        self.assertEqual(response["count"], 1)
        self.assertEqual((self.job()["status"], self.job()["serverUrl"], self.job()["libraryId"]),
                         ("PendingIndex", self.client.url, "actual-library-id"))
        self.assertEqual(Path(comic.output).read_bytes(), original)
        self.remote(comic)
        self.assertEqual(self.run_jobs(current_time=2000)["synced"], 1)

    def test_unbound_retry_preflights_the_whole_selection_before_any_binding(self):
        first, second = self.comic("first"), self.comic("second")
        first_job = jobs.record_validated_export(self.repo, first.id, self.output)
        second_job = jobs.record_validated_export(self.repo, second.id, self.output)
        with zipfile.ZipFile(second.output, "w") as archive:
            archive.writestr("ComicInfo.xml", to_comicinfo(second.metadata, 1))
            archive.writestr("pages/00001.png", b"broken image")
        with self.assertRaises(ValueError):
            jobs.retry_jobs(self.repo, [first_job["id"], second_job["id"]], self.client.url, "actual-library-id")
        current = jobs.list_jobs(self.repo)
        self.assertTrue(all(job["status"] == "Approved" and not job["serverUrl"] for job in current))
        self.assertEqual(self.client.calls, [])

    def test_unbound_retry_rejects_stale_draft_and_missing_artifact(self):
        comic = self.comic()
        remembered = jobs.record_validated_export(self.repo, comic.id, self.output)
        Path(comic.output).unlink()
        self.assertFalse(self.job()["canRetry"])
        with self.assertRaises(ValueError):
            jobs.retry_jobs(self.repo, [remembered["id"]], self.client.url, "actual-library-id")
        self.assertEqual(self.job()["status"], "Approved")
        changed = self.comic("changed")
        stale = jobs.record_validated_export(self.repo, changed.id, self.output)
        metadata = Metadata.from_dict(changed.metadata.to_dict())
        metadata.text_version = ""
        self.repo.save(changed.id, metadata)
        with self.assertRaises(ValueError):
            jobs.retry_jobs(self.repo, [stale["id"]], self.client.url, "actual-library-id")
        visible = next(job for job in jobs.list_jobs(self.repo) if job["comicId"] == changed.id)
        self.assertEqual(visible["status"], "NeedsManualReview")
        self.assertFalse(visible["canRetry"])

    def test_pending_index_survives_restart_then_matches_and_synchronizes_only_entities(self):
        comic = self.comic()
        queued = self.enqueue(comic)
        self.assertEqual(queued["relativePath"], "nested/_oneshots/one.cbz")
        first = self.run_jobs(current_time=1000)
        self.assertEqual((first["processed"], self.job()["status"]), (1, "PendingIndex"))
        self.assertEqual(self.patches(), [])
        self.repo = Repository(self.repo.path)
        remote = self.remote(comic)
        original = deepcopy(remote)
        second = self.run_jobs(current_time=1030)
        self.assertEqual((second["synced"], self.job()["status"]), (1, "Synced"))
        self.assertEqual(len(self.patches()), 1)
        entities = remote["metadata"]["notableEntities"]
        self.assertIn({"kind": "team", "name": "第三方队伍"}, entities)
        self.assertIn({"kind": "character", "name": "博丽灵梦"}, entities)
        self.assertNotIn({"kind": "character", "name": "旧角色"}, entities)
        self.assertNotIn({"kind": "原作", "name": "旧原作"}, entities)
        self.assertEqual(remote["readProgress"], original["readProgress"])
        self.assertEqual(remote["metadata"]["summary"], original["metadata"]["summary"])
        before = len(self.client.calls)
        self.assertEqual(self.run_jobs(current_time=2000)["processed"], 0)
        self.assertEqual(len(self.client.calls), before)

    def test_scoped_jobs_never_run_against_another_library(self):
        self.enqueue(self.comic())
        result = jobs.run_due(self.repo, self.client.url, self.secret, "different-library", current_time=1000)
        self.assertEqual(result["processed"], 0)
        self.assertEqual(self.client.calls, [])

    def test_permission_failure_stops_automatic_retries_and_can_be_explicitly_retried(self):
        self.enqueue(self.comic())
        self.client.failure = KomgaError("API Key 无效", "KOMGA_AUTH")
        self.run_jobs(current_time=1000)
        job = self.job()
        self.assertEqual((job["status"], job["attempts"], job["nextAttemptAt"]), ("PermanentFailure", 1, ""))
        self.assertEqual(self.run_jobs(current_time=2000)["processed"], 0)
        self.client.failure = None
        response = jobs.retry_jobs(self.repo, [job["id"]], self.client.url, "actual-library-id")
        self.assertEqual((response["count"], self.job()["status"]), (1, "PendingIndex"))

    def test_rate_limit_honors_retry_after_and_uses_exponential_backoff(self):
        self.enqueue(self.comic())
        error = KomgaError("限流", "KOMGA_RATE_LIMIT")
        error.http_status, error.retry_after = 429, 120
        self.client.failure = error
        self.run_jobs(current_time=1000)
        with self.repo.connect() as db:
            row = db.execute("SELECT * FROM komga_auto_jobs").fetchone()
        self.assertEqual((row["status"], row["next_attempt"]), ("RetryableFailure", 1120))
        self.assertEqual(self.run_jobs(current_time=1119)["processed"], 0)
        self.client.failure = KomgaError("network", "KOMGA_NETWORK")
        self.run_jobs(current_time=1120)
        with self.repo.connect() as db:
            row = db.execute("SELECT * FROM komga_auto_jobs").fetchone()
        self.assertEqual((row["attempts"], row["next_attempt"]), (2, 1150))

    def test_long_retry_after_is_not_shortened_and_delays_the_scope(self):
        self.enqueue(self.comic("first"))
        self.enqueue(self.comic("second"))
        self.client.failure = KomgaError("限流", "KOMGA_RATE_LIMIT", http_status=429, retry_after=172800)
        self.run_jobs(current_time=1000)
        with self.repo.connect() as db:
            rows = list(db.execute("SELECT next_attempt FROM komga_auto_jobs"))
        self.assertEqual([row[0] for row in rows], [173800, 173800])
        previous = len(self.client.calls)
        self.assertEqual(self.run_jobs(current_time=173799)["processed"], 0)
        self.assertEqual(len(self.client.calls), previous)

    def test_unrepresentable_retry_time_stops_without_timestamp_overflow(self):
        self.enqueue(self.comic())
        self.client.failure = KomgaError("限流", "KOMGA_RATE_LIMIT", http_status=429, retry_after=10**20)
        self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["status"], "PermanentFailure")
        self.assertEqual(self.job()["nextAttemptAt"], "")

    def test_auth_failure_suspends_all_pending_jobs_but_not_successful_or_other_scope(self):
        successful = self.comic("successful")
        self.enqueue(successful)
        self.remote(successful)
        self.run_jobs(current_time=1000)
        self.enqueue(self.comic("pending-a"))
        self.enqueue(self.comic("pending-b"))
        foreign = self.comic("other-scope")
        jobs.enqueue_export(self.repo, foreign.id, self.output, self.client.url, "other-library")
        self.client.failure = KomgaError("权限缺失", "KOMGA_FORBIDDEN", http_status=403)
        self.run_jobs(current_time=2000)
        current = {job["comicId"]: job for job in jobs.list_jobs(self.repo)}
        self.assertEqual(current[successful.id]["status"], "Synced")
        self.assertEqual(current[foreign.id]["status"], "PendingIndex")
        self.assertEqual(sum(job["status"] == "PermanentFailure" for job in current.values()), 2)
        before = len(self.client.calls)
        self.assertEqual(self.run_jobs(current_time=3000)["processed"], 0)
        self.assertEqual(len(self.client.calls), before)
        self.client.failure = None
        failed = [job["id"] for job in current.values() if job["status"] == "PermanentFailure"]
        self.assertEqual(jobs.retry_jobs(self.repo, failed, self.client.url, "actual-library-id")["count"], 2)

    def test_transient_server_error_is_retryable_but_bad_request_is_permanent(self):
        self.enqueue(self.comic())
        error = KomgaError("HTTP503", "KOMGA_SERVER")
        error.http_status = 503
        self.client.failure = error
        self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["status"], "RetryableFailure")
        error = KomgaError("HTTP400", "KOMGA_HTTP")
        error.http_status = 400
        self.client.failure = error
        self.run_jobs(current_time=1015)
        self.assertEqual(self.job()["status"], "PermanentFailure")

    def test_multiple_matches_and_title_mismatch_require_manual_review(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic, "a")
        self.remote(comic, "b")
        self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["status"], "NeedsManualReview")
        self.assertEqual(self.patches(), [])
        self.client.remote.pop("b")
        self.client.remote["a"]["metadata"]["title"] = "标题不一致"
        jobs.retry_jobs(self.repo, [self.job()["id"]], self.client.url, "actual-library-id")
        self.run_jobs(current_time=2000)
        self.assertEqual(self.job()["status"], "NeedsManualReview")
        self.assertEqual(self.patches(), [])

    def test_missing_or_altered_archive_never_patches_and_does_not_repackage(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic)
        Path(comic.output).write_bytes(b"replaced artifact")
        self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["status"], "NeedsManualReview")
        self.assertEqual(Path(comic.output).read_bytes(), b"replaced artifact")
        self.assertEqual(self.patches(), [])

    def test_edit_after_enqueue_stops_stale_job_and_new_approval_creates_new_version(self):
        comic = self.comic()
        original = self.enqueue(comic)
        self.remote(comic)
        metadata = Metadata.from_dict(comic.metadata.to_dict())
        metadata.characters = []
        self.repo.save(comic.id, metadata)
        self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["status"], "NeedsManualReview")
        self.assertEqual(self.patches(), [])
        self.repo.approve(comic.id, inspect_source(Path(comic.source)))
        replacement = jobs.record_approval(self.repo, comic.id)
        self.assertNotEqual(replacement["id"], original["id"])
        self.assertNotEqual(replacement["metadataVersion"], original["metadataVersion"])
        self.assertEqual(replacement["status"], "Approved")

    def test_write_guard_rejects_metadata_edit_during_remote_read(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic)
        changed = False
        def edit(method, path):
            nonlocal changed
            if method == "GET" and "books/" in path and not changed:
                changed = True
                updated = Metadata.from_dict(comic.metadata.to_dict())
                updated.summary = "remote read期间本机改了资料"
                self.repo.save(comic.id, updated)
        self.client.before_request = edit
        self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["status"], "NeedsManualReview")
        self.assertEqual(self.patches(), [])

    def test_validated_cbz_can_retry_even_if_original_images_were_removed(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic)
        (Path(comic.source) / "page.png").unlink()
        Path(comic.source).rmdir()
        self.assertEqual(self.run_jobs(current_time=1000)["synced"], 1)

    def test_prewrite_backup_contains_old_locks_and_target_without_credentials(self):
        comic = self.comic()
        self.enqueue(comic)
        remote = self.remote(comic)
        def verify_backup():
            with self.repo.connect() as db:
                row = db.execute("SELECT * FROM komga_auto_backups").fetchone()
            self.assertIsNotNone(row)
            detail = json.loads(row["detail"])
            self.assertFalse(detail["before"]["notableEntitiesLock"])
            self.assertIn({"kind": "character", "name": "旧角色"}, detail["before"]["notableEntities"])
            self.assertTrue(detail["request"]["notableEntitiesLock"])
            self.assertNotIn(self.secret, row["detail"])
        self.client.before_patch = verify_backup
        self.run_jobs(current_time=1000)
        self.assertEqual(remote["metadata"]["notableEntitiesLock"], True)
        with self.repo.connect() as db:
            persisted = " ".join(str(tuple(row)) for table in ("komga_auto_jobs", "komga_auto_backups") for row in db.execute("SELECT * FROM " + table))
        self.assertNotIn(self.secret, persisted)

    def test_readback_mismatch_stays_retryable_then_noop_verifies_without_duplicate_patch(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic)
        self.client.after_patch = lambda record: record["metadata"].update(notableEntitiesLock=False)
        self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["status"], "RetryableFailure")
        self.assertEqual(self.job()["syncedAt"], "")
        self.client.after_patch = lambda record: None
        self.run_jobs(current_time=1015)
        self.assertEqual(self.job()["status"], "Synced")
        # This retry only needs to fix the failed lock, then future runs are idle.
        self.assertEqual(len(self.patches()), 2)
        self.run_jobs(current_time=2000)
        self.assertEqual(len(self.patches()), 2)

    def test_cancel_does_not_mark_success_and_releases_lease(self):
        self.enqueue(self.comic())
        cancel = Event()
        cancel.set()
        response = self.run_jobs(current_time=1000, cancel=cancel)
        self.assertTrue(response["cancelled"])
        self.assertEqual(self.job()["status"], "PendingIndex")
        self.assertEqual(self.client.calls, [])
        with self.repo.connect() as db:
            self.assertEqual(db.execute("SELECT lease_token FROM komga_auto_jobs").fetchone()[0], "")

    def test_concurrent_runner_and_book_lease_cannot_patch_same_target(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic)
        entered, release = Event(), Event()
        def hold(method, path):
            if method == "POST":
                entered.set()
                release.wait(5)
        self.client.before_request = hold
        responses = []
        thread = Thread(target=lambda: responses.append(self.run_jobs(current_time=1000)))
        thread.start()
        self.assertTrue(entered.wait(5))
        self.assertTrue(self.run_jobs(current_time=1000)["busy"])
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.patches()), 1)

    def test_cross_process_book_lease_defers_without_writing(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic)
        with self.repo.connect() as db:
            db.execute("INSERT INTO komga_auto_book_leases VALUES (?,?,?,?,?)", (self.client.url, "actual-library-id", "book-one", "other-worker", 1300))
        response = self.run_jobs(current_time=1000)
        self.assertTrue(response["busy"])
        self.assertEqual(self.patches(), [])
        self.assertEqual(self.job()["status"], "PendingIndex")

    def test_wrong_xml_or_invalid_image_cannot_enter_pending_index(self):
        comic = self.comic()
        with zipfile.ZipFile(comic.output, "w") as archive:
            archive.writestr("ComicInfo.xml", b"<ComicInfo><Title>wrong</Title></ComicInfo>")
            archive.writestr("pages/00001.png", PNG)
        with self.assertRaisesRegex(ValueError, "元数据不一致"):
            self.enqueue(comic)
        self.assertEqual(jobs.list_jobs(self.repo), [])
        with zipfile.ZipFile(comic.output, "w") as archive:
            archive.writestr("ComicInfo.xml", to_comicinfo(comic.metadata, 1))
            archive.writestr("pages/00001.png", b"broken image")
        with self.assertRaises(ValueError):
            self.enqueue(comic)
        self.assertEqual(jobs.list_jobs(self.repo), [])

    def test_identical_enqueue_is_idempotent_and_retry_scope_is_enforced(self):
        comic = self.comic()
        original = self.enqueue(comic)
        self.assertEqual(self.enqueue(comic)["id"], original["id"])
        with self.assertRaisesRegex(ValueError, "不属于"):
            jobs.retry_jobs(self.repo, [original["id"]], self.client.url, "other-library")

    def test_synced_badge_is_invalidated_by_edit_or_removal_without_reopening_archive(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic)
        self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["status"], "Synced")
        updated = Metadata.from_dict(comic.metadata.to_dict())
        updated.text_version = ""
        self.repo.save(comic.id, updated)
        with patch.object(jobs, "_file_hash", side_effect=AssertionError("list must not hash files")):
            self.assertEqual(self.job()["status"], "NeedsManualReview")
        self.assertFalse(self.job()["canRetry"])
        self.assertTrue(self.job()["syncedAt"], "Keep previous successful time as history")
        with self.assertRaises(ValueError):
            jobs.retry_jobs(self.repo, [self.job()["id"]], self.client.url, "actual-library-id")

    def test_removal_retires_the_pending_job(self):
        comic = self.comic()
        self.enqueue(comic)
        with self.repo.connect() as db:
            db.execute("INSERT INTO removed_comics VALUES (?,?)", (comic.id, "removed"))
        self.assertEqual(jobs.invalidate_jobs(self.repo, [comic.id]), 1)
        self.assertEqual(self.job()["status"], "NeedsManualReview")
        self.assertFalse(self.job()["canRetry"])
        self.assertEqual(self.run_jobs(current_time=1000)["processed"], 0)

    def test_replaced_book_id_after_failed_readback_requires_manual_confirmation(self):
        comic = self.comic()
        self.enqueue(comic)
        remote = self.remote(comic, "original")
        self.client.after_patch = lambda record: record["metadata"].update(notableEntitiesLock=False)
        self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["bookId"], "original")
        original_patch_count = len(self.patches())
        self.client.remote.pop("original")
        replacement = deepcopy(remote)
        replacement["id"] = "replacement"
        self.client.remote["replacement"] = replacement
        self.run_jobs(current_time=1015)
        self.assertEqual(self.job()["status"], "NeedsManualReview")
        self.assertEqual(len(self.patches()), original_patch_count)

    def test_lost_cross_process_book_lease_stops_before_write(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic)
        stolen = False
        def steal(method, path):
            nonlocal stolen
            if method == "GET" and "books/" in path and not stolen:
                stolen = True
                with self.repo.connect() as db:
                    db.execute("UPDATE komga_auto_book_leases SET owner='new-owner'")
        self.client.before_request = steal
        self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["status"], "RetryableFailure")
        self.assertEqual(self.patches(), [])

    def test_book_only_hashes_at_start_prewrite_and_success_then_noop_at_both_ends(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic)
        with patch.object(jobs, "_file_hash", wraps=jobs._file_hash) as hashes:
            self.assertEqual(self.run_jobs(current_time=1000)["synced"], 1)
            self.assertEqual(hashes.call_count, 3)
        with self.repo.connect() as db:
            db.execute("UPDATE komga_auto_jobs SET status='PendingSync',next_attempt=0")
        before = len(self.patches())
        with patch.object(jobs, "_file_hash", wraps=jobs._file_hash) as hashes:
            self.assertEqual(self.run_jobs(current_time=2000)["synced"], 1)
            self.assertEqual(hashes.call_count, 2)
        self.assertEqual(len(self.patches()), before)

    def test_changed_artifact_stat_stops_before_write_even_if_content_stayed_identical(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic)
        changed = False
        def touch(method, path):
            nonlocal changed
            if method == "GET" and "books/" in path and not changed:
                changed = True
                value = Path(comic.output).stat()
                os.utime(comic.output, ns=(value.st_atime_ns, value.st_mtime_ns + 1_000_000_000))
        self.client.before_request = touch
        self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["status"], "NeedsManualReview")
        self.assertEqual(self.patches(), [])

    def test_lease_taken_during_slow_hash_is_rechecked_after_hash_before_write(self):
        comic = self.comic()
        self.enqueue(comic)
        self.remote(comic)
        original = jobs._file_hash
        count = 0
        def slow_hash(path, cancel=None):
            nonlocal count
            count += 1
            digest = original(path, cancel)
            if count == 2:
                with self.repo.connect() as db:
                    db.execute("UPDATE komga_auto_book_leases SET owner='new-owner'")
            return digest
        with patch.object(jobs, "_file_hash", side_effect=slow_hash):
            self.run_jobs(current_time=1000)
        self.assertEqual(self.job()["status"], "RetryableFailure")
        self.assertEqual(self.patches(), [])


if __name__ == "__main__":
    unittest.main()
