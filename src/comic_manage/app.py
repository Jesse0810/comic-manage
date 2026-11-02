from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys
from threading import Event

from PySide6.QtCore import Qt, QThread, QTimer, QStandardPaths, QUrl, QBuffer, QByteArray, QIODevice, QLockFile
from PySide6.QtGui import QColor, QDesktopServices, QPixmap, QCloseEvent, QImageReader, QFont, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QListWidget, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QHeaderView, QSplitter, QScrollArea,
    QPlainTextEdit, QCheckBox, QSpinBox, QTabWidget, QFileDialog,
    QMessageBox, QDialog, QDialogButtonBox, QProgressBar, QStackedWidget,
    QGridLayout, QToolButton, QSizePolicy, QAbstractSpinBox,
)

from . import __version__
from .jobs import Worker
from .metadata import AGE_RATINGS, Metadata, split_values, to_comicinfo
from .packaging import ExportPlan, export_cbz, make_plan
from .repository import Comic, Repository
from .sources import check_cancel, discover, inspect_source, source_id
from .style import STYLE

STATUS = {"draft": "待审核", "approved": "已审核", "packaged": "已生成"}
COLORS = {"draft": "#b1763f", "approved": "#246d5c", "packaged": "#567889"}


def label(text: str, name: str = "") -> QLabel:
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setObjectName(name)
    return item


def button(text: str, callback, primary=False) -> QPushButton:
    item = QPushButton(text)
    if primary:
        item.setObjectName("primary")
    item.clicked.connect(callback)
    return item


class SourceLabel(QLabel):
    """Keep long NAS paths in one line without widening the details panel."""

    def __init__(self):
        super().__init__()
        self.full_text = ""
        self.setObjectName("source")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text):
        self.full_text = text
        self.setToolTip(text)
        self.elide_text()

    def elide_text(self):
        super().setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideMiddle, max(0, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.elide_text()


class EmptyDetailsIcon(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(64, 64)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#edf5f0"))
        painter.drawRoundedRect(self.rect(), 18, 18)
        painter.setPen(QPen(QColor("#6e9987"), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        book = QPainterPath()
        book.moveTo(16, 20)
        book.quadTo(24, 18, 32, 23)
        book.quadTo(40, 18, 48, 20)
        book.lineTo(48, 44)
        book.quadTo(40, 42, 32, 47)
        book.quadTo(24, 42, 16, 44)
        book.closeSubpath()
        painter.drawPath(book)
        painter.drawLine(32, 23, 32, 47)
        painter.end()


class ExportDialog(QDialog):
    def __init__(self, plans: list[ExportPlan], parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认生成计划")
        self.resize(940, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(label(f"即将生成 {len(plans)} 本漫画", "heading"))
        note = label("只新增 CBZ，不覆盖现有文件。校验成功后保留全部源文件。")
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(len(plans), 3)
        table.setHorizontalHeaderLabels(["来源", "成品路径", "图片 / 附件"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, plan in enumerate(plans):
            values = [plan.comic.source, str(plan.destination), f"{len(plan.snapshot.pages)} / {len(plan.snapshot.extras)}"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row, col, item)
        layout.addWidget(table)
        note = label("页序采用自然排序；图片不转码。CBZ 原附件保留到 extras/，原 ComicInfo 的其他字段保留。", "source")
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Ok).setText("确认生成并校验")
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText("返回")
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path):
        super().__init__()
        self.data_dir = data_dir
        self.repo = Repository(data_dir / "catalog.sqlite3")
        self.current: Comic | None = None
        self.dirty = False
        self.loading = False
        self.busy = False
        self.job_thread = None
        self.worker = None
        self.cancel_event = Event()
        self.pending_selection = None
        self.preview_pixmap = QPixmap()
        self.setWindowTitle(f"Comic Manage · 漫画整理 v{__version__}")
        self.resize(1480, 960)
        self.setMinimumSize(1100, 760)
        self.build_ui()
        self.refresh()

    def build_ui(self):
        shell = QWidget()
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(shell)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(205)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(20, 30, 20, 22)
        side.addWidget(label("漫画整理", "brand"))
        side.addWidget(label("COMIC MANAGE   /   V1", "brandCaption"))
        side.addSpacing(30)
        self.scan_button = button("＋  添加目录", self.scan_directory)
        self.scan_button.setObjectName("sidebarButton")
        side.addWidget(self.scan_button)
        self.file_button = button("导入已有 CBZ", self.import_cbz)
        self.file_button.setObjectName("sidebarButton")
        side.addWidget(self.file_button)
        side.addSpacing(26)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.addItems(["全部漫画", "待审核", "已审核", "已生成"])
        self.navigation.setCurrentRow(0)
        self.navigation.currentRowChanged.connect(self.filter_table)
        side.addWidget(self.navigation)
        self.rescan_button = button("重新扫描已添加目录", self.rescan)
        self.rescan_button.setObjectName("sidebarButton")
        side.addWidget(self.rescan_button)
        self.backup_button = button("备份资料库", self.backup)
        self.backup_button.setObjectName("sidebarButton")
        side.addWidget(self.backup_button)
        side.addSpacing(12)
        side.addWidget(label("本地管理 · 人工确认\n无损打包 · 校验后入库", "sidebarNote"))
        layout.addWidget(self.sidebar)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(27, 27, 27, 14)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("我的漫画库", "heading"))
        titles.addWidget(label("从原始文件到 Kavita，按自己的节奏整理。", "subtitle"))
        heading.addLayout(titles)
        heading.addStretch()
        self.stats = label("", "subtitle")
        heading.addWidget(self.stats)
        body.addLayout(heading)
        body.addSpacing(12)

        self.output_bar = QWidget()
        output_layout = QHBoxLayout(self.output_bar)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(label("成品目录"))
        self.output_path = QLineEdit(self.repo.setting("output_root"))
        self.output_path.setPlaceholderText("选择独立的成品目录，例如 \\\\NAS\\Comics\\已整理")
        self.output_path.setReadOnly(True)
        output_layout.addWidget(self.output_path, 1)
        output_layout.addWidget(button("选择…", self.choose_output))
        self.export_button = button("生成所选 CBZ", self.prepare_export, True)
        output_layout.addWidget(self.export_button)
        body.addWidget(self.output_bar)
        body.addSpacing(10)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        library = QWidget()
        lib = QVBoxLayout(library)
        lib.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索中文名、日文名、作者、标签…")
        self.search.textChanged.connect(self.filter_table)
        filters.addWidget(self.search, 1)
        self.author_filter = QComboBox()
        self.author_filter.setMinimumWidth(120)
        self.author_filter.currentIndexChanged.connect(self.filter_table)
        filters.addWidget(self.author_filter)
        lib.addLayout(filters)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["作品 / 原名", "作者", "页数", "状态", "来源"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index, width in [(1, 105), (2, 48), (3, 70), (4, 58)]:
            self.table.setColumnWidth(index, width)
        self.table.currentCellChanged.connect(self.selection_changed)
        lib.addWidget(self.table, 1)
        self.empty = label("还没有作品\n\n点击左侧“添加目录”，扫描本地文件夹或 NAS 共享目录。")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        lib.addWidget(self.empty)
        self.selection_note = label("Ctrl / Shift 多选作品，可批量生成已审核漫画。", "source")
        lib.addWidget(self.selection_note)
        self.splitter.addWidget(library)

        self.build_editor()
        self.splitter.addWidget(self.editor)
        self.splitter.setSizes([710, 460])
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        body.addWidget(self.splitter, 1)
        self.task_row = QHBoxLayout()
        self.task_label = label("", "source")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(120)
        self.progress.setTextVisible(False)
        self.cancel_button = button("取消任务", self.cancel_task)
        self.task_row.addWidget(self.task_label, 1)
        self.task_row.addWidget(self.progress)
        self.task_row.addWidget(self.cancel_button)
        body.addLayout(self.task_row)
        self.progress.hide()
        self.cancel_button.hide()
        layout.addWidget(content, 1)
        self.statusBar().showMessage("元数据保存在本机资料库；源文件保持原样。")

    def build_editor(self):
        self.editor = QFrame()
        self.editor.setObjectName("editor")
        self.editor.setMinimumWidth(400)
        self.editor.setMaximumWidth(580)
        edit = QVBoxLayout(self.editor)
        edit.setContentsMargins(20, 20, 20, 18)
        edit.setSpacing(14)
        heading = QHBoxLayout()
        heading.addWidget(label("作品资料", "section"))
        heading.addStretch()
        self.panel_status = label("", "statusBadge")
        heading.addWidget(self.panel_status)
        edit.addLayout(heading)

        self.editor_stack = QStackedWidget()
        self.editor_stack.setObjectName("editorStack")
        edit.addWidget(self.editor_stack, 1)
        self.editor_empty = QWidget()
        self.editor_empty.setObjectName("editorEmpty")
        empty = QVBoxLayout(self.editor_empty)
        empty.setContentsMargins(14, 0, 14, 55)
        empty.setSpacing(12)
        empty.addStretch()
        empty.addWidget(EmptyDetailsIcon(), 0, Qt.AlignmentFlag.AlignHCenter)
        empty.addSpacing(4)
        title = label("先选择一部漫画", "emptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(title)
        note = label("从左侧列表选择作品，\n在这里预览页面、编辑资料并完成审核。", "emptyDescription")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setWordWrap(True)
        empty.addWidget(note)
        empty.addStretch()
        self.editor_stack.addWidget(self.editor_empty)

        self.editor_content = QWidget()
        self.editor_content.setObjectName("editorContent")
        sheet = QVBoxLayout(self.editor_content)
        sheet.setContentsMargins(0, 0, 0, 0)
        sheet.setSpacing(12)
        self.source_label = SourceLabel()
        sheet.addWidget(self.source_label)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("detailsTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.form_container = QWidget()
        self.form_container.setObjectName("detailsForm")
        form = QVBoxLayout(self.form_container)
        form.setContentsMargins(0, 16, 12, 8)
        form.setSpacing(16)

        preview_card = QFrame()
        preview_card.setObjectName("previewCard")
        preview = QHBoxLayout(preview_card)
        preview.setContentsMargins(12, 12, 12, 12)
        preview.setSpacing(16)
        self.cover = label("页面预览", "cover")
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setFixedSize(100, 134)
        self.cover.setWordWrap(True)
        preview.addWidget(self.cover)
        pages = QVBoxLayout()
        pages.setSpacing(8)
        pages.addWidget(label("页面预览", "groupTitle"))
        self.preview_note = label("", "source")
        self.preview_note.setWordWrap(True)
        pages.addWidget(self.preview_note)
        page_controls = QHBoxLayout()
        page_controls.setSpacing(4)
        previous = button("‹", lambda: self.page.stepDown())
        previous.setObjectName("pageArrow")
        previous.setFixedSize(28, 30)
        previous.setToolTip("上一页")
        page_controls.addWidget(previous)
        self.page = QSpinBox()
        self.page.setPrefix("第 ")
        self.page.setSuffix(" 页")
        self.page.setMinimum(1)
        self.page.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page.setFixedWidth(84)
        self.page.valueChanged.connect(self.load_preview)
        page_controls.addWidget(self.page)
        following = button("›", lambda: self.page.stepUp())
        following.setObjectName("pageArrow")
        following.setFixedSize(28, 30)
        following.setToolTip("下一页")
        page_controls.addWidget(following)
        page_controls.addStretch()
        pages.addLayout(page_controls)
        self.cover_button = button("设为封面", self.set_cover)
        self.cover_button.setObjectName("textButton")
        pages.addWidget(self.cover_button, 0, Qt.AlignmentFlag.AlignLeft)
        preview.addLayout(pages, 1)
        form.addWidget(preview_card)
        form.addWidget(label("基本信息", "groupTitle"))
        self.fields: dict[str, QLineEdit] = {}

        def field_block(title, widget):
            block = QWidget()
            row = QVBoxLayout(block)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            caption = label(title, "fieldLabel")
            caption.setBuddy(widget)
            row.addWidget(caption)
            row.addWidget(widget)
            return block

        def field(key, title, placeholder):
            widget = QLineEdit()
            widget.setPlaceholderText(placeholder)
            widget.textChanged.connect(self.mark_dirty)
            self.fields[key] = widget
            return field_block(title, widget)

        for key, title, placeholder in [
            ("original_title", "日文原名 *", "完整保留作品原名"),
            ("chinese_title", "中文名称", "汉化作品填写中文名"),
            ("authors", "作者 *", "多位作者用逗号分隔"),
        ]:
            form.addWidget(field(key, title, placeholder))

        flags_widget = QWidget()
        flags_widget.setObjectName("metadataFlags")
        flags = QHBoxLayout(flags_widget)
        flags.setContentsMargins(0, 0, 0, 0)
        flags.setSpacing(8)
        self.flags = {}
        for key, title in [("translated", "汉化"), ("uncensored", "无修正"), ("full_color", "全彩")]:
            checkbox = QCheckBox(title)
            checkbox.toggled.connect(self.mark_dirty)
            self.flags[key] = checkbox
            flags.addWidget(checkbox)
        self.flags["translated"].toggled.connect(self.translation_changed)
        form.addWidget(field_block("作品标记", flags_widget))
        form.addWidget(field("tags", "其他标签", "用逗号分隔，例如：日常, 校园"))

        self.more_toggle = QToolButton()
        self.more_toggle.setObjectName("moreToggle")
        self.more_toggle.setText("出版与补充信息")
        self.more_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.more_toggle.setCheckable(True)
        self.more_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.more_toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.more_toggle.setToolTip("社团、汉化组、类型、语言、年份、分级与简介")
        form.addWidget(self.more_toggle)
        self.supplement = QWidget()
        extra = QGridLayout(self.supplement)
        extra.setContentsMargins(0, 0, 0, 0)
        extra.setHorizontalSpacing(12)
        extra.setVerticalSpacing(14)
        extra.setColumnStretch(0, 1)
        extra.setColumnStretch(1, 1)
        for row, col, key, title, placeholder in [
            (0, 0, "publisher", "社团 / 出版社", "可选"),
            (0, 1, "translator", "汉化组", "可选"),
            (1, 0, "genre", "类型", "例如：同人志"),
            (1, 1, "language", "语言", "ja / zh-Hans"),
            (2, 0, "year", "发行年份", "可留空"),
        ]:
            extra.addWidget(field(key, title, placeholder), row, col)
        self.age = QComboBox()
        self.age.setEditable(True)
        self.age.addItems(AGE_RATINGS)
        self.age.currentTextChanged.connect(self.mark_dirty)
        extra.addWidget(field_block("年龄分级", self.age), 2, 1)
        self.summary = QPlainTextEdit()
        self.summary.setFixedHeight(84)
        self.summary.setPlaceholderText("写一段作品简介（可选）")
        self.summary.textChanged.connect(self.mark_dirty)
        extra.addWidget(field_block("简介", self.summary), 3, 0, 1, 2)
        self.supplement.hide()
        self.more_toggle.toggled.connect(self.toggle_supplement)
        form.addWidget(self.supplement)
        self.warning = label("", "source")
        self.warning.setWordWrap(True)
        self.warning.hide()
        form.addWidget(self.warning)
        form.addStretch()
        scroll = QScrollArea()
        scroll.setObjectName("detailsScroll")
        scroll.viewport().setObjectName("detailsViewport")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.form_container)
        self.tabs.addTab(scroll, "资料与预览")
        self.xml_preview = QPlainTextEdit()
        self.xml_preview.setObjectName("xmlPreview")
        self.xml_preview.setReadOnly(True)
        self.tabs.addTab(self.xml_preview, "ComicInfo.xml")
        self.tabs.currentChanged.connect(self.update_xml)
        sheet.addWidget(self.tabs, 1)

        review = QFrame()
        review.setObjectName("reviewArea")
        actions = QVBoxLayout(review)
        actions.setContentsMargins(0, 14, 0, 0)
        actions.setSpacing(12)
        self.review_check = QCheckBox("已核对作品、原名、作者、页序与封面")
        self.review_check.setObjectName("reviewCheck")
        actions.addWidget(self.review_check)
        edit_actions = QHBoxLayout()
        edit_actions.setSpacing(10)
        self.save_button = button("保存草稿", self.save_current)
        self.approve_button = button("审核通过", self.approve_current, True)
        edit_actions.addWidget(self.save_button)
        edit_actions.addWidget(self.approve_button)
        actions.addLayout(edit_actions)
        source_actions = QHBoxLayout()
        for text, callback in [("打开源位置", self.open_source), ("打开成品", self.open_output)]:
            link = button(text, callback)
            link.setObjectName("textButton")
            source_actions.addWidget(link)
        source_actions.addStretch()
        actions.addLayout(source_actions)
        sheet.addWidget(review)
        self.editor_stack.addWidget(self.editor_content)

    def toggle_supplement(self, expanded):
        self.supplement.setVisible(expanded)
        self.more_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def set_panel_status(self, status):
        self.panel_status.setText("未保存" if status == "edited" else STATUS.get(status, ""))
        self.panel_status.setVisible(bool(status))
        self.panel_status.setProperty("state", status)
        self.panel_status.style().unpolish(self.panel_status)
        self.panel_status.style().polish(self.panel_status)

    def notify(self, title: str, text: str, detail: str = "", error=False):
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning if error else QMessageBox.Icon.Information)
        message.setWindowTitle(title)
        message.setText(text)
        if detail:
            message.setDetailedText(detail)
        message.exec()

    def mark_dirty(self, *_):
        if not self.loading and self.current:
            self.dirty = True
            self.set_panel_status("edited")
            self.review_check.setChecked(False)
            self.statusBar().showMessage("有未保存的修改；保存后需要重新审核。")

    def translation_changed(self, translated: bool):
        if not self.loading:
            self.fields["language"].setText("zh-Hans" if translated else "ja")

    def read_metadata(self) -> Metadata:
        original = self.current.metadata
        values = {key: item.text().strip() for key, item in self.fields.items()}
        values["authors"] = split_values(values["authors"])
        values["tags"] = split_values(values["tags"])
        return replace(original, **values, **{k: v.isChecked() for k, v in self.flags.items()},
                       age_rating=self.age.currentText(), summary=self.summary.toPlainText().strip(),
                       cover_index=self.cover_index)

    def resolve_edits(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, "保存修改", "当前作品有未保存的修改，是否保存为草稿？",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            self.repo.save(self.current.id, self.read_metadata())
        self.dirty = False
        return True

    def refresh(self, select_id: str | None = None):
        self.comics = self.repo.all()
        counts = {s: sum(c.status == s for c in self.comics) for s in STATUS}
        self.stats.setText(f"{len(self.comics)} 本作品   /   {counts['draft']} 本待审核")
        for i, text in enumerate(["全部漫画", "待审核", "已审核", "已生成"]):
            count = len(self.comics) if i == 0 else counts[list(STATUS)[i - 1]]
            self.navigation.item(i).setText(f"{text}   {count}")
        previous_author = self.author_filter.currentText()
        self.author_filter.blockSignals(True)
        self.author_filter.clear()
        self.author_filter.addItem("全部作者")
        self.author_filter.addItems(sorted({a for c in self.comics for a in c.metadata.authors}, key=str.casefold))
        if previous_author:
            self.author_filter.setCurrentText(previous_author)
        self.author_filter.blockSignals(False)
        self.filter_table(select_id=select_id or (self.current.id if self.current else None))

    def filter_table(self, *_, select_id: str | None = None):
        if not hasattr(self, "comics"):
            return
        # Refiltering is a navigation action: do not silently discard an edited form.
        if self.dirty:
            if not self.resolve_edits():
                return
            self.comics = self.repo.all()
        search = self.search.text().casefold().strip()
        nav = self.navigation.currentRow()
        status = list(STATUS)[nav - 1] if nav > 0 else ""
        author = self.author_filter.currentText()
        rows = []
        for comic in self.comics:
            meta = comic.metadata
            text = " ".join([meta.original_title, meta.chinese_title, *meta.authors, *meta.all_tags, meta.translator]).casefold()
            if search and search not in text:
                continue
            if status and comic.status != status:
                continue
            if author and author != "全部作者" and author not in meta.authors:
                continue
            rows.append(comic)
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        selected_row = -1
        for row, comic in enumerate(rows):
            meta = comic.metadata
            title = meta.display_title or Path(comic.source).stem
            if meta.translated and meta.original_title:
                title += "\n" + meta.original_title
            values = [title, ", ".join(meta.authors) or "未填写", str(comic.page_count), STATUS[comic.status], "CBZ" if comic.kind == "cbz" else "文件夹"]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, comic.id)
                item.setToolTip(comic.source if col in (0, 4) else text)
                if col == 3:
                    item.setForeground(QColor(COLORS[comic.status]))
                self.table.setItem(row, col, item)
            self.table.setRowHeight(row, 62 if "\n" in title else 48)
            if comic.id == select_id:
                selected_row = row
        self.table.clearSelection()
        if selected_row >= 0:
            self.table.setCurrentCell(selected_row, 0)
            self.table.selectRow(selected_row)
        else:
            self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        self.empty.setVisible(not rows)
        self.table.setVisible(bool(rows))
        self.editor.setEnabled(selected_row >= 0 and not self.busy)
        if selected_row >= 0:
            self.show_comic(self.repo.get(rows[selected_row].id))
        else:
            self.current = None
            self.editor_stack.setCurrentWidget(self.editor_empty)
            self.set_panel_status("")
            self.source_label.setText("")
            self.cover.clear()

    def selection_changed(self, row, _col, previous_row, _previous_col):
        if self.loading or row < 0:
            return
        item = self.table.item(row, 0)
        if not item:
            return
        target_id = item.data(Qt.ItemDataRole.UserRole)
        if self.current and target_id == self.current.id:
            return
        if not self.resolve_edits():
            self.table.blockSignals(True)
            if previous_row >= 0:
                self.table.setCurrentCell(previous_row, 0)
            self.table.blockSignals(False)
            return
        self.show_comic(self.repo.get(target_id))

    def show_comic(self, comic: Comic):
        self.loading = True
        self.current = comic
        self.editor_stack.setCurrentWidget(self.editor_content)
        self.set_panel_status(comic.status)
        self.editor.setEnabled(not self.busy)
        metadata = comic.metadata
        for key, field in self.fields.items():
            value = getattr(metadata, key)
            field.setText(", ".join(value) if isinstance(value, list) else value)
        for key, flag in self.flags.items():
            flag.setChecked(getattr(metadata, key))
        self.age.setCurrentText(metadata.age_rating)
        self.summary.setPlainText(metadata.summary)
        self.cover_index = metadata.cover_index
        self.page.setMaximum(max(1, comic.page_count))
        self.page.setValue(min(metadata.cover_index + 1, comic.page_count))
        self.source_label.setText(comic.source)
        self.warning.setText(comic.warning)
        self.warning.setVisible(bool(comic.warning))
        self.review_check.setChecked(comic.status in ("approved", "packaged"))
        self.dirty = False
        self.loading = False
        self.update_xml()
        self.load_preview()

    def load_preview(self, *_):
        if self.loading or not self.current or self.busy:
            return
        source = Path(self.current.source)
        index = self.page.value() - 1
        self.cover.setText("读取中…")
        self.preview_note.setText(f"共 {self.current.page_count} 页 · 封面第 {self.cover_index + 1} 页")
        comic_id = self.current.id

        def load(message, cancel):
            check_cancel(cancel)
            snapshot = inspect_source(source)
            return (comic_id, index, snapshot.page_bytes(index), snapshot.pages[index].name)

        self.start_job(load, self.preview_loaded, quiet=True)

    def preview_loaded(self, result):
        comic_id, index, data, name = result
        if not self.current or self.current.id != comic_id or self.page.value() - 1 != index:
            return
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        reader.setAutoTransform(True)
        reader.setScaledSize(reader.size().scaled(self.cover.size(), Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            self.preview_pixmap = pixmap
            self.cover.setPixmap(pixmap.scaled(self.cover.size(),
                                               Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.cover.setText("此图片格式无法预览\n仍可原样打包")
        self.cover.setToolTip(name)

    def set_cover(self):
        self.cover_index = self.page.value() - 1
        self.mark_dirty()
        self.preview_note.setText(f"共 {self.current.page_count} 页 · 封面第 {self.cover_index + 1} 页")

    def update_xml(self, *_):
        if self.current:
            try:
                self.xml_preview.setPlainText(to_comicinfo(self.read_metadata(), self.current.page_count).decode("utf-8"))
            except Exception as exc:
                self.xml_preview.setPlainText(f"补全资料后可预览 ComicInfo.xml：\n{exc}")

    def save_current(self):
        if not self.current:
            return
        self.repo.save(self.current.id, self.read_metadata())
        self.dirty = False
        self.refresh(self.current.id)
        self.statusBar().showMessage("草稿已保存到本机。请核对并重新审核。")

    def approve_current(self):
        if not self.current:
            return
        if not self.review_check.isChecked():
            self.notify("需要核对", "请预览页面、核对资料，再勾选审核确认。")
            return
        comic_id = self.current.id
        source = Path(self.current.source)
        self.repo.save(comic_id, self.read_metadata())
        self.dirty = False

        def approve(message, cancel):
            check_cancel(cancel)
            message("正在确认源文件…")
            self.repo.approve(comic_id, inspect_source(source))
            return comic_id

        self.start_job(approve, lambda result: self.refresh(result))

    def choose_output(self):
        selected = QFileDialog.getExistingDirectory(self, "选择独立成品目录", self.output_path.text())
        if selected:
            self.output_path.setText(selected)
            self.repo.set_setting("output_root", selected)

    def scan_directory(self):
        if not self.resolve_edits():
            return
        selected = QFileDialog.getExistingDirectory(self, "选择本地目录或 NAS 共享目录", self.repo.setting("last_root"))
        if selected:
            self.repo.set_setting("last_root", selected)
            roots = self.repo.setting("roots").splitlines()
            if selected not in roots:
                roots.append(selected)
                self.repo.set_setting("roots", "\n".join(roots))
            self.run_scan([Path(selected)])

    def rescan(self):
        if not self.resolve_edits():
            return
        roots = [Path(p) for p in self.repo.setting("roots").splitlines() if p]
        if not roots:
            self.notify("还没有目录", "请先添加一个漫画目录。")
            return
        self.run_scan(roots)

    def run_scan(self, roots: list[Path]):
        output = self.output_path.text()
        exclude = [self.data_dir] + ([Path(output)] if output else [])

        def scan(message, cancel):
            count, errors = 0, []
            seen = set()
            for root in roots:
                try:
                    for snapshot in discover(root, exclude=exclude, cancel=cancel,
                                             on_error=lambda path, error: errors.append(f"{path}: {error}")):
                        identity = source_id(snapshot.path)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        self.repo.upsert_scan(snapshot)
                        count += 1
                        message(f"已扫描 {count} 本 · {snapshot.path.name}")
                except OSError as exc:
                    errors.append(f"{root}: {exc}")
            return count, errors

        def complete(result):
            count, errors = result
            self.refresh()
            self.notify("扫描完成", f"已扫描 {count} 本作品，{len(errors)} 项未读取。\n人工修改过的资料已保留。", "\n".join(errors))

        self.start_job(scan, complete)

    def import_cbz(self):
        if not self.resolve_edits():
            return
        files, _ = QFileDialog.getOpenFileNames(self, "导入已有 CBZ", "", "漫画压缩包 (*.cbz *.CBZ)")
        if not files:
            return

        def load(message, cancel):
            count, errors = 0, []
            for file in files:
                check_cancel(cancel)
                try:
                    message(f"正在导入 {Path(file).name}")
                    self.repo.upsert_scan(inspect_source(Path(file)))
                    count += 1
                except Exception as exc:
                    errors.append(f"{file}: {exc}")
            return count, errors

        def complete(result):
            self.refresh()
            self.notify("导入完成", f"已导入 {result[0]} 本，{len(result[1])} 项未读取。", "\n".join(result[1]))

        self.start_job(load, complete)

    def selected_comics(self) -> list[Comic]:
        return [self.repo.get(self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole))
                for index in self.table.selectionModel().selectedRows()]

    def prepare_export(self):
        if not self.resolve_edits():
            return
        comics = self.selected_comics()
        if not comics:
            self.notify("选择作品", "请先选择一本或多本已审核作品。")
            return
        if not self.output_path.text():
            self.choose_output()
        if not self.output_path.text():
            return
        root = Path(self.output_path.text())

        def prepare(message, cancel):
            plans = []
            for comic in comics:
                check_cancel(cancel)
                message(f"检查生成计划 · {comic.metadata.display_title}")
                plans.append(make_plan(comic, root))
            return plans

        self.start_job(prepare, self.confirm_export)

    def confirm_export(self, plans):
        if ExportDialog(plans, self).exec() != QDialog.DialogCode.Accepted:
            return
        # Schedule after the previous worker has finished cleaning up.
        QTimer.singleShot(0, lambda: self.run_export(plans))

    def run_export(self, plans):
        def package(message, cancel):
            completed, errors = [], []
            for plan in plans:
                check_cancel(cancel)
                try:
                    report = export_cbz(plan, cancel=cancel, progress=message)
                    self.repo.mark_packaged(plan.comic.id, plan.destination, report)
                    completed.append(str(plan.destination))
                except Exception as exc:
                    from .sources import Cancelled
                    if isinstance(exc, Cancelled):
                        raise
                    errors.append(f"{plan.comic.source}: {exc}")
            return completed, errors

        def complete(result):
            completed, errors = result
            self.refresh()
            self.notify("生成完成", f"{len(completed)} 本已生成并通过校验，{len(errors)} 本失败。\n全部源文件已保留。", "\n".join(errors or completed))

        self.start_job(package, complete)

    def start_job(self, function, callback, quiet=False):
        if self.busy:
            return
        self.busy = True
        self.cancel_event = Event()
        self.set_busy(True)
        self.progress.setVisible(not quiet)
        self.cancel_button.setVisible(not quiet)
        self.task_label.setText("读取页面…" if quiet else "正在准备…")
        thread = QThread(self)
        worker = Worker(function, self.cancel_event)
        worker.moveToThread(thread)
        self.job_thread, self.worker = thread, worker
        results = {}
        worker.message.connect(self.task_label.setText)
        worker.succeeded.connect(lambda result: results.update(value=result))
        worker.failed.connect(lambda error: results.update(error=error))
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)

        def cleanup():
            self.busy = False
            self.job_thread = self.worker = None
            self.set_busy(False)
            self.progress.hide()
            self.cancel_button.hide()
            self.task_label.setText("")
            thread.deleteLater()
            if "error" in results:
                if quiet:
                    self.cover.setText("无法读取页面")
                    self.cover.setToolTip(results["error"])
                    self.task_label.setText(results["error"])
                else:
                    self.refresh()
                    self.notify("任务结束", results["error"], error=True)
            elif "value" in results:
                try:
                    callback(results["value"])
                except Exception as exc:
                    self.notify("操作未完成", str(exc), error=True)

        thread.started.connect(worker.run)
        thread.finished.connect(cleanup)
        thread.start()

    def set_busy(self, busy):
        self.sidebar.setEnabled(not busy)
        self.output_bar.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.search.setEnabled(not busy)
        self.author_filter.setEnabled(not busy)
        self.editor.setEnabled(not busy and self.current is not None)

    def cancel_task(self):
        self.cancel_event.set()
        self.task_label.setText("正在取消，等待当前文件操作返回…")
        self.cancel_button.setEnabled(False)
        QTimer.singleShot(100, lambda: self.cancel_button.setEnabled(True))

    def open_source(self):
        if self.current:
            source = Path(self.current.source)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(source if source.is_dir() else source.parent)))

    def open_output(self):
        if self.current and self.current.output and Path(self.current.output).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.current.output).parent)))
        else:
            self.notify("还没有成品", "此作品尚未生成 CBZ，或成品已被移动。")

    def backup(self):
        path, _ = QFileDialog.getSaveFileName(self, "备份资料库", "comic-manage-backup.sqlite3", "SQLite (*.sqlite3)")
        if path:
            try:
                self.repo.backup(Path(path))
                self.notify("备份完成", "备份包含目录索引、元数据、审核和校验记录，不包含漫画图片。")
            except Exception as exc:
                self.notify("备份失败", str(exc), error=True)

    def closeEvent(self, event: QCloseEvent):
        if self.busy:
            self.notify("任务进行中", "请先取消任务，等待文件操作结束后再关闭。")
            event.ignore()
        elif not self.resolve_edits():
            event.ignore()
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser(description="Comic Manage 本地漫画整理器")
    parser.add_argument("--data-dir", type=Path, help="指定本机资料库目录（不要放在 NAS 上）")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setOrganizationName("ComicManage")
    app.setApplicationName("ComicManage")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    QImageReader.setAllocationLimit(128)
    app.setStyleSheet(STYLE)
    data_dir = args.data_dir or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(data_dir / "app.lock"))
        if not lock.tryLock(0):
            QMessageBox.information(None, "资料库已打开", "另一个 Comic Manage 正在使用此资料库。请切换到已打开的窗口。")
            return
        window = MainWindow(data_dir)
    except Exception as exc:
        QMessageBox.critical(None, "启动失败", f"无法打开本机资料库：\n{exc}")
        return
    window.show()
    sys.exit(app.exec())
