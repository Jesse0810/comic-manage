"""The public approval/export path creates durable work only after safe publication."""
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from PIL import Image

from comic_manage.metadata import Metadata
from comic_manage.service import ComicService, ServiceError
from comic_manage.sources import inspect_source


class AutomaticServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.book = self.root / "漫画源"
        self.book.mkdir()
        Image.new("RGB", (3, 4), "white").save(self.book / "1.png")
        self.service = ComicService(self.root / "data")
        self.identity = self.service.repo.upsert_scan(inspect_source(self.book))
        self.metadata = Metadata(title="春の旅", authors=["原文作者"], genre="漫画", tags=["日常"],
                                 age_rating="Everyone", characters=["甲", "乙"], work_group="春の旅（作者）")
        self.scope = {"url": "http://127.0.0.1:25600", "libraryId": "actual-library-id"}

    def tearDown(self):
        self.temp.cleanup()

    def approve(self):
        return self.service.call("approve", {"id": self.identity, "metadata": self.metadata.to_dict()})

    def plan(self):
        self.approve()
        return self.service.call("plan", {"ids": [self.identity], "outputRoot": str(self.root / "成品")})

    def jobs(self):
        return self.service.call("komga_jobs")["jobs"]

    def test_approval_records_waiting_work_without_network_and_reopens(self):
        self.approve()
        first = self.jobs()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["status"], "Approved")
        self.assertEqual(first[0]["bookId"], "")
        reopened = ComicService(self.root / "data")
        self.assertEqual(reopened.call("bootstrap")["autoSyncJobs"], first)

    def test_auto_export_is_validated_before_atomic_publication_then_queued_without_secrets(self):
        plan = self.plan()
        result = self.service.call("export", {"token": plan["token"], "autoSync": self.scope})
        self.assertFalse(result["errors"])
        self.assertEqual(len(result["completed"]), 1)
        jobs = self.jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "PendingIndex")
        self.assertEqual(jobs[0]["outputPath"], result["completed"][0])
        self.assertEqual(jobs[0]["libraryId"], "actual-library-id")
        self.assertEqual(self.service.repo.get(self.identity).status, "packaged")
        self.assertFalse(list((self.root / "成品").rglob("*.partial")))
        self.assertTrue((self.book / "1.png").is_file())
        self.assertNotIn("apiKey", json.dumps(jobs))

    def test_corrupt_image_auto_export_never_publishes_or_enqueues_pending_index(self):
        (self.book / "1.png").write_bytes(b"invalid image bytes")
        self.service.repo.upsert_scan(inspect_source(self.book))
        plan = self.plan()
        result = self.service.call("export", {"token": plan["token"], "autoSync": self.scope})
        self.assertEqual(result["completed"], [])
        self.assertTrue(any("图片无法完整读取" in error for error in result["errors"]))
        self.assertFalse(list((self.root / "成品").rglob("*.cbz")))
        self.assertFalse(list((self.root / "成品").rglob("*.partial")))
        self.assertEqual(self.jobs()[0]["status"], "Approved")
        self.assertEqual(self.service.repo.get(self.identity).status, "approved")

    def test_disabled_connection_keeps_generated_cbz_and_task_separate(self):
        plan = self.plan()
        result = self.service.call("export", {"token": plan["token"]})
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(self.jobs()[0]["status"], "Approved")
        self.assertTrue(self.jobs()[0]["canRetry"])
        self.assertEqual(self.service.repo.get(self.identity).status, "packaged")

    def test_existing_validated_export_can_join_current_scope_explicitly_without_repackaging(self):
        plan = self.plan()
        result = self.service.call("export", {"token": plan["token"]})
        output = Path(result["completed"][0])
        original = output.read_bytes()
        job = self.jobs()[0]
        (self.book / "1.png").unlink()
        queued = self.service.call("komga_jobs_retry", {"ids": [job["id"]], **self.scope})
        self.assertEqual(queued["count"], 1)
        self.assertEqual(queued["jobs"][0]["status"], "PendingIndex")
        self.assertEqual(queued["jobs"][0]["libraryId"], "actual-library-id")
        self.assertEqual(output.read_bytes(), original)

    def test_secret_or_unknown_publication_options_reject_before_consuming_plan(self):
        plan = self.plan()
        for options in ({**self.scope, "apiKey": "must-not-persist"}, [], "yes"):
            with self.assertRaises(ServiceError):
                self.service.call("export", {"token": plan["token"], "autoSync": options})
        result = self.service.call("export", {"token": plan["token"], "autoSync": self.scope})
        self.assertEqual(len(result["completed"]), 1)
        with self.assertRaises(ServiceError):
            self.service.call("komga_jobs", {"apiKey": "unexpected"})
        with self.assertRaises(ServiceError):
            self.service.call("komga_jobs_run", {**self.scope, "apiKey": "test", "ids": ["injected"]})

    def test_saved_draft_invalidates_previously_packaged_task(self):
        plan = self.plan()
        self.service.call("export", {"token": plan["token"], "autoSync": self.scope})
        self.service.call("save", {"id": self.identity, "metadata": {"title": "重新核对"}})
        self.assertEqual(self.jobs()[0]["status"], "NeedsManualReview")

    def test_queue_failure_keeps_validated_export_success_and_reports_sync_separately(self):
        plan = self.plan()
        with patch("comic_manage.service.enqueue_export", side_effect=OSError("任务数据库暂时不可写")):
            result = self.service.call("export", {"token": plan["token"], "autoSync": self.scope})
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(len(result["autoSyncErrors"]), 1)
        self.assertTrue(Path(result["completed"][0]).is_file())
        self.assertEqual(self.service.repo.get(self.identity).status, "packaged")
        self.assertTrue(self.jobs()[0]["canRetry"])


if __name__ == "__main__":
    unittest.main()
