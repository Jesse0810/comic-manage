"""Exercise the actual Qt windows and workers with synthetic images, offscreen."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QThread, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from comic_manage.app import MainWindow
from comic_manage.metadata import Metadata
from comic_manage.repository import Repository
from comic_manage.sources import check_cancel, inspect_source
from comic_manage.style import STYLE


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "中文路径" / "作品"
        self.source.mkdir(parents=True)
        for i in range(1, 4):
            image = QImage(240, 320, QImage.Format.Format_RGB32)
            image.fill(QColor("#74a891" if i == 1 else "#d3ba84"))
            self.assertTrue(image.save(str(self.source / f"{i}.png")))
        self.window = MainWindow(self.root / "data")
        self.messages = []
        self.window.notify = lambda *args, **kwargs: self.messages.append((args, kwargs))
        self.window.show()
        self.app.processEvents()
        self.identity = self.window.repo.upsert_scan(inspect_source(self.source))
        self.window.repo.save(self.identity, Metadata(original_title="春の旅", chinese_title="春日旅途", authors=["画师甲", "画师乙"], translated=True, language="zh-Hans"))
        self.window.refresh()

    def wait_for(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail(f"Qt operation timed out: {self.messages}")

    def idle(self):
        self.wait_for(lambda: not self.window.busy)
        self.app.processEvents()

    def select(self):
        self.window.table.setCurrentCell(0, 0)
        self.window.table.selectRow(0)
        self.idle()

    def tearDown(self):
        self.idle()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def test_preview_and_metadata_load(self):
        self.select()
        self.assertEqual(self.window.fields["original_title"].text(), "春の旅")
        self.assertFalse(self.window.cover.pixmap().isNull())
        self.assertEqual(self.window.page.maximum(), 3)
        self.assertTrue(self.window.editor.isEnabled())

    def test_search_both_titles_and_author(self):
        for query in ("春の旅", "春日旅途", "画师乙"):
            self.window.search.setText(query)
            self.assertEqual(self.window.table.rowCount(), 1)
        self.window.search.setText("不存在")
        self.assertEqual(self.window.table.rowCount(), 0)
        self.assertFalse(self.window.editor.isEnabled())

    def test_edit_save_and_approval_invalidation(self):
        self.select()
        self.window.review_check.setChecked(True)
        self.window.approve_current()
        self.idle()
        self.assertEqual(self.window.repo.get(self.identity).status, "approved")
        self.window.fields["chinese_title"].setText("新中文名")
        self.assertTrue(self.window.dirty)
        self.assertFalse(self.window.review_check.isChecked())
        self.window.save_current()
        self.idle()
        comic = self.window.repo.get(self.identity)
        self.assertEqual(comic.status, "draft")
        self.assertEqual(comic.metadata.chinese_title, "新中文名")

    def test_approval_requires_human_checkbox(self):
        self.select()
        self.window.approve_current()
        self.assertEqual(self.window.repo.get(self.identity).status, "draft")
        self.assertTrue(self.messages)

    def test_cover_selection_and_xml_preview(self):
        self.select()
        self.window.page.setValue(2)
        self.idle()
        self.window.set_cover()
        self.window.tabs.setCurrentIndex(1)
        self.assertIn('Image="1"', self.window.xml_preview.toPlainText())
        self.assertTrue(self.window.dirty)

    def test_full_gui_review_plan_export_flow(self):
        self.select()
        self.window.review_check.setChecked(True)
        self.window.approve_current()
        self.idle()
        self.window.output_path.setText(str(self.root / "成品"))
        with patch("comic_manage.app.ExportDialog.exec", return_value=QDialog.DialogCode.Accepted):
            self.window.prepare_export()
            self.wait_for(lambda: self.window.repo.get(self.identity).status == "packaged")
            self.idle()
        comic = self.window.repo.get(self.identity)
        self.assertTrue(Path(comic.output).is_file())
        self.assertTrue((self.source / "1.png").exists())
        self.assertTrue(any("生成完成" in args[0] for args, _ in self.messages))

    def test_worker_completion_runs_on_gui_thread(self):
        observed = []
        self.window.start_job(lambda message, cancel: "result", lambda result: observed.append((result, QThread.currentThread() == self.app.thread())))
        self.idle()
        self.assertEqual(observed, [("result", True)])

    def test_offline_source_preview_keeps_catalog(self):
        for file in self.source.iterdir():
            file.unlink()
        self.source.rmdir()
        self.select()
        self.assertEqual(self.window.cover.text(), "无法读取页面")
        self.assertEqual(len(self.window.repo.all()), 1)

    def test_unsaved_edit_is_preserved_on_cancelled_navigation(self):
        self.select()
        self.window.fields["chinese_title"].setText("尚未保存")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.window.search.setText("something else")
        self.assertTrue(self.window.dirty)
        self.assertEqual(self.window.fields["chinese_title"].text(), "尚未保存")


if __name__ == "__main__":
    unittest.main()
