"""Behavioral tests for the Electron IPC service, without Qt or a browser."""
import base64
from dataclasses import replace
import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
from threading import Event, Lock, Thread
import time
import unittest
from unittest.mock import patch
import zipfile

from comic_manage.metadata import Metadata
from comic_manage.service import CatalogLock, ComicService, JsonLineServer, ServiceError
from comic_manage.sources import inspect_source


class ProtocolOutput:
    def __init__(self):
        self.buffer = ""
        self.lock = Lock()
        self.queue = queue.Queue()

    def write(self, value):
        with self.lock:
            self.buffer += value
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                self.queue.put(json.loads(line))

    def flush(self):
        pass

    def response(self, identity, timeout=5):
        deadline = time.monotonic() + timeout
        messages = []
        while time.monotonic() < deadline:
            value = self.queue.get(timeout=max(.01, deadline - time.monotonic()))
            if value.get("id") == identity:
                return value
            messages.append(value)
        raise AssertionError(f"Response missing for {identity}: {messages}")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / "中文漫画库"
        self.book = self.library / "春の旅"
        self.book.mkdir(parents=True)
        (self.book / "1.png").write_bytes(b"first-page")
        (self.book / "2.jpg").write_bytes(b"second-page")
        self.data = self.root / "data"
        self.output = self.root / "output"
        self.progress = []
        self.service = ComicService(self.data, self.progress.append)
        self.identity = self.service.repo.upsert_scan(inspect_source(self.book))
        self.metadata = Metadata(original_title="春の旅", chinese_title="春日旅途", authors=["作者甲", "作者乙"],
                                 translated=True, language="zh-Hans", tags=["日常"], full_color=True)

    def tearDown(self):
        self.temp.cleanup()

    def approve(self):
        return self.service.call("approve", {"id": self.identity, "metadata": self.metadata.to_dict()})

    def plan(self):
        self.approve()
        return self.service.call("plan", {"ids": [self.identity], "outputRoot": str(self.output)})

    def test_existing_catalog_bootstrap_uses_unchanged_record_schema(self):
        self.service.call("save", {"id": self.identity, "metadata": self.metadata.to_dict()})
        result = self.service.call("bootstrap")
        self.assertEqual(result["comics"][0]["metadata"]["authors"], ["作者甲", "作者乙"])
        self.assertEqual(result["comics"][0]["status"], "draft")
        self.assertTrue(Path(result["dataDir"]).samefile(self.data))

    def test_scan_retains_manual_data_and_skips_output_and_catalog(self):
        self.approve()
        output = self.library / "成品"
        output.mkdir()
        (output / "1.jpg").write_bytes(b"not another original")
        self.service.call("set_output", {"path": str(output)})
        result = self.service.call("scan", {"roots": [str(self.library), str(self.book)]})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["errors"], [])
        result = self.service.call("bootstrap")
        self.assertEqual(len(result["comics"]), 1)
        self.assertEqual(result["comics"][0]["metadata"]["chinese_title"], "春日旅途")
        self.assertEqual(result["comics"][0]["status"], "approved")
        self.assertEqual(result["roots"], [str(self.library), str(self.book)])

    def test_scan_missing_nas_path_keeps_records_and_reports_error(self):
        result = self.service.call("scan", {"roots": [str(self.root / "offline-share")]})
        self.assertEqual(result["count"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(len(self.service.call("bootstrap")["comics"]), 1)

    def test_invalid_metadata_types_are_rejected_but_incomplete_drafts_save(self):
        for value in [{"authors": "wrong"}, {"translated": 1}, {"cover_index": True}, {"tags": [3]}, {"year": 2025}]:
            with self.subTest(value=value), self.assertRaises(ServiceError):
                self.service.call("save", {"id": self.identity, "metadata": value})
        saved = self.service.call("save", {"id": self.identity, "metadata": {"original_title": ""}})
        self.assertEqual(saved["metadata"]["original_title"], "")
        self.assertEqual(saved["status"], "draft")

    def test_failed_approval_saves_draft_and_does_not_approve(self):
        with self.assertRaises(ValueError):
            self.service.call("approve", {"id": self.identity, "metadata": {"authors": []}})
        self.assertEqual(self.service.repo.get(self.identity).status, "draft")

    def test_page_data_is_scoped_to_record_and_correct_mime(self):
        result = self.service.call("page", {"id": self.identity, "index": 1})
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["name"], "2.jpg")
        self.assertEqual(result["dataUrl"], "data:image/jpeg;base64," + base64.b64encode(b"second-page").decode())
        with self.assertRaises(ValueError):
            self.service.call("page", {"id": str(self.book / "1.png"), "index": 0})
        with self.assertRaises(ServiceError):
            self.service.call("page", {"id": self.identity, "index": True})
        with patch("comic_manage.service.MAX_PAGE_BYTES", 1):
            with self.assertRaisesRegex(ServiceError, "32 MiB"):
                self.service.call("page", {"id": self.identity, "index": 0})

    def test_xml_preview_does_not_change_saved_metadata(self):
        self.service.call("save", {"id": self.identity, "metadata": self.metadata.to_dict()})
        result = self.service.call("xml", {"id": self.identity, "metadata": {"chinese_title": "预览中的名称"}})
        self.assertIn("<Series>预览中的名称</Series>", result["xml"])
        self.assertEqual(self.service.repo.get(self.identity).metadata.chinese_title, "春日旅途")

    def test_review_plan_export_preserves_source_and_marks_verified(self):
        plan = self.plan()
        self.assertEqual(plan["items"][0]["pageCount"], 2)
        self.assertEqual(plan["items"][0]["attachmentCount"], 0)
        self.assertGreater(plan["totalBytes"], 0)
        result = self.service.call("export", {"token": plan["token"]})
        self.assertFalse(result["cancelled"])
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(result["errors"], [])
        with zipfile.ZipFile(result["completed"][0]) as archive:
            self.assertEqual(archive.read("pages/00001.png"), b"first-page")
            self.assertIn("<LocalizedSeries>春の旅</LocalizedSeries>", archive.read("ComicInfo.xml").decode())
        self.assertEqual((self.book / "1.png").read_bytes(), b"first-page")
        self.assertEqual(self.service.repo.get(self.identity).status, "packaged")
        with self.assertRaises(ServiceError):
            self.service.call("export", {"token": plan["token"]})

    def test_changed_metadata_invalidates_prepared_plan(self):
        plan = self.plan()
        self.service.call("save", {"id": self.identity, "metadata": {"summary": "change"}})
        with self.assertRaisesRegex(ServiceError, "失效"):
            self.service.call("export", {"token": plan["token"]})
        self.assertFalse(self.output.exists())

    def test_source_change_after_plan_is_rejected_before_export(self):
        plan = self.plan()
        (self.book / "3.jpg").write_bytes(b"new page")
        with self.assertRaisesRegex(ValueError, "变化"):
            self.service.call("export", {"token": plan["token"]})
        self.assertFalse(self.output.exists())

    def test_cancelled_scan_returns_partial_count_and_preserves_record(self):
        cancel = Event()
        self.service.progress = lambda _: cancel.set()
        result = self.service.call("scan", {"roots": [str(self.library)]}, cancel)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(self.service.repo.all()), 1)

    def test_cancelled_export_reports_no_published_file(self):
        plan = self.plan()
        cancel = Event()
        self.service.progress = lambda _: cancel.set()
        result = self.service.call("export", {"token": plan["token"]}, cancel)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["completed"], [])
        self.assertFalse(list(self.output.rglob("*.cbz")))
        self.assertFalse(list(self.output.rglob("*.partial")))

    def test_import_partial_errors_and_backup(self):
        path = self.root / "现有.cbz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("1.jpg", b"page")
        result = self.service.call("import", {"paths": [str(path), str(self.root / "bad.txt")]})
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["errors"]), 1)
        backup = self.root / "backup.sqlite3"
        self.service.call("backup", {"path": str(backup)})
        self.assertTrue(backup.is_file())

    def test_protocol_cancel_is_not_blocked_by_worker(self):
        output = ProtocolOutput()
        server = JsonLineServer(self.root / "server-data", output)
        entered = Event()

        def blocking_call(method, params, cancel):
            entered.set()
            self.assertTrue(cancel.wait(5))
            return {"cancelled": True}

        with patch.object(server.service, "call", side_effect=blocking_call):
            server.accept({"id": "work", "method": "scan", "params": {}})
            self.assertTrue(entered.wait(5))
            server.accept({"id": "busy", "method": "save", "params": {}})
            self.assertEqual(output.response("busy")["error"]["code"], "BUSY")
            server.accept({"id": "cancel", "method": "cancel"})
            self.assertTrue(output.response("cancel")["result"]["cancelled"])
            self.assertTrue(output.response("work")["result"]["cancelled"])

    def test_protocol_errors_are_json_and_next_request_still_works(self):
        output = ProtocolOutput()
        server = JsonLineServer(self.root / "server-data", output)
        server.accept({"id": "bad", "method": "os.execute", "params": {}})
        self.assertEqual(output.response("bad")["error"]["code"], "METHOD_NOT_FOUND")
        server.accept({"id": "good", "method": "bootstrap"})
        self.assertEqual(output.response("good")["result"]["comics"], [])

    def test_catalog_process_lock_released_on_exit(self):
        with CatalogLock(self.root / "locked"):
            with self.assertRaises(ServiceError) as error:
                with CatalogLock(self.root / "locked"):
                    pass
            self.assertEqual(error.exception.code, "CATALOG_LOCKED")
        with CatalogLock(self.root / "locked"):
            pass

    def test_cli_round_trip_has_only_json_stdout_and_utf8_titles(self):
        launcher = Path(__file__).resolve().parents[1] / "service_main.py"
        process = subprocess.Popen([sys.executable, str(launcher), "--data-dir", str(self.root / "cli-data")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", bufsize=1)
        try:
            process.stdin.write(json.dumps({"id": "scan", "method": "scan", "params": {"roots": [str(self.library)]}}, ensure_ascii=False) + "\n")
            process.stdin.flush()
            while True:
                message = json.loads(process.stdout.readline())
                if message.get("id") == "scan":
                    self.assertEqual(message["result"]["count"], 1)
                    break
            process.stdin.write('{"id":"load","method":"bootstrap"}\n')
            process.stdin.flush()
            message = json.loads(process.stdout.readline())
            self.assertEqual(message["result"]["comics"][0]["metadata"]["original_title"], "春の旅")
            process.stdin.close()
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertEqual(process.stderr.read(), "")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
