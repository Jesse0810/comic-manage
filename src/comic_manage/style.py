STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #263d42; }
QMainWindow, QDialog { background: #f4f6f4; }
QLabel#brand { font-size: 22px; font-weight: 700; color: #f4fbf7; }
QLabel#brandCaption { color: #b5ccc5; font-size: 11px; }
QFrame#sidebar { background: #183d39; border: none; }
QFrame#sidebar QLabel { color: #c6d8d1; }
QLabel#sidebarNote { font-size: 11px; color: #b5ccc5; }
QLabel#heading { font-size: 25px; font-weight: 650; }
QLabel#subtitle { color: #6f8383; font-size: 12px; }
QLabel#section { font-size: 15px; font-weight: 650; }
QLabel#eyebrow { color: #6a8981; font-size: 10px; font-weight: 700; }
QLabel#source { color: #6c7d7b; font-size: 11px; }
QLabel#cover { background: #e8eee9; border: 1px solid #d5dfd7; border-radius: 8px; color: #73877e; }
QFrame#editor { background: #ffffff; border: 1px solid #dfe6e0; border-radius: 10px; }
QPushButton { border: 1px solid #cfdad3; background: #ffffff; border-radius: 6px; padding: 8px 13px; }
QPushButton:hover { background: #edf3ee; border-color: #8fa99e; }
QPushButton:disabled { color: #9ea9a3; background: #eef0ed; border-color: #dce2dd; }
QPushButton#primary { background: #246d5c; color: #ffffff; border: 1px solid #246d5c; font-weight: 600; }
QPushButton#primary:hover { background: #1a594b; }
QPushButton#primary:disabled { background: #b5c7be; border-color: #b5c7be; }
QPushButton#sidebarButton { background: #2b554b; color: #eff7ee; border: 1px solid #527569; }
QListWidget#navigation { background: transparent; border: none; outline: 0; color: #c6d8d1; }
QListWidget#navigation::item { border-radius: 6px; padding: 13px 10px; margin-bottom: 5px; }
QListWidget#navigation::item:selected { background: #345c50; color: #ffffff; }
QListWidget#navigation::item:hover { background: #294e45; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { background: #ffffff; border: 1px solid #d9e1db; border-radius: 5px; padding: 6px; selection-background-color: #d6e9dd; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #609c82; }
QTableWidget { background: #ffffff; alternate-background-color: #fafcf9; border: 1px solid #dfe6e0; border-radius: 8px; gridline-color: #edf1ed; selection-background-color: #e2eee5; selection-color: #1b5145; }
QHeaderView::section { background: #eef2ed; padding: 10px; color: #677d73; border: none; border-bottom: 1px solid #dfe6e0; font-size: 11px; }
QTableWidget::item { padding: 8px; border-bottom: 1px solid #eef2ed; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #ffffff; color: #7a8c81; padding: 10px 16px; border-bottom: 2px solid #ffffff; }
QTabBar::tab:selected { color: #246d5c; border-bottom: 2px solid #246d5c; }
QCheckBox { padding: 3px 0; }
QScrollArea { border: none; background: white; }
QStatusBar { background: #e9efea; color: #657c71; font-size: 11px; }
QProgressBar { border: none; background: #dbe6de; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #609c82; }
QSplitter::handle { background: transparent; width: 14px; }

/* Details panel: quiet surface, grouped fields and a persistent review footer. */
QFrame#editor { background: #ffffff; border: 1px solid #dce5de; border-radius: 14px; }
QWidget#editorEmpty, QWidget#editorContent, QStackedWidget#editorStack,
QWidget#detailsForm, QWidget#detailsViewport { background: #ffffff; border: none; }
QLabel#emptyTitle { color: #35584b; font-size: 16px; font-weight: 600; }
QLabel#emptyDescription { color: #83958c; font-size: 12px; }
QLabel#statusBadge { color: #a57643; background: #fbf3e7; border-radius: 10px; padding: 4px 9px; font-size: 11px; }
QLabel#statusBadge[state="approved"] { color: #2a7357; background: #eaf5ed; }
QLabel#statusBadge[state="packaged"] { color: #517888; background: #edf3f6; }
QLabel#statusBadge[state="edited"] { color: #93714c; background: #f7efdf; }
QLabel#fieldLabel { color: #62756a; font-size: 12px; }
QLabel#groupTitle { color: #35584b; font-size: 13px; font-weight: 600; }
QFrame#previewCard { background: #f5f8f5; border: 1px solid #e6ede7; border-radius: 10px; }
QLabel#cover { color: #8fa196; background: #e7eee7; border: none; border-radius: 7px; font-size: 11px; }
QFrame#editor QLineEdit, QFrame#editor QComboBox, QFrame#editor QSpinBox,
QFrame#editor QPlainTextEdit { color: #2d463b; background: #fbfcfa; border: 1px solid #dfe7df; border-radius: 7px; padding: 7px 9px; min-height: 18px; }
QFrame#editor QLineEdit:focus, QFrame#editor QPlainTextEdit:focus,
QFrame#editor QSpinBox:focus, QFrame#editor QComboBox:focus { background: #ffffff; border-color: #72a68a; }
QTabWidget#detailsTabs::pane { border: none; }
QTabWidget#detailsTabs QTabBar::tab { color: #819087; padding: 9px 12px; font-size: 12px; }
QTabWidget#detailsTabs QTabBar::tab:selected { color: #286d55; border-bottom: 2px solid #286d55; }
QPushButton#pageArrow { color: #62836f; background: #ffffff; border: 1px solid #dce6dc; border-radius: 6px; font-size: 19px; padding: 0; }
QPushButton#pageArrow:hover { background: #e9f1e9; }
QPushButton#textButton { color: #628472; border: none; background: transparent; padding: 3px 0; font-size: 11px; text-align: left; }
QPushButton#textButton:hover { color: #246d5c; }
QPushButton#textButton:disabled { color: #a6b4aa; }
QWidget#metadataFlags QCheckBox { color: #718175; background: #fafcf9; border: 1px solid #e0e7df; border-radius: 7px; padding: 7px 8px; font-size: 12px; }
QWidget#metadataFlags QCheckBox:checked { color: #306b4c; background: #edf6ec; border-color: #bed5bf; }
QWidget#metadataFlags QCheckBox::indicator { width: 13px; height: 13px; }
QToolButton#moreToggle { color: #6d8373; background: transparent; border: none; border-top: 1px solid #edf1eb; padding: 12px 0 4px 0; text-align: left; font-size: 12px; }
QToolButton#moreToggle:hover { color: #246d5c; }
QFrame#reviewArea { background: #ffffff; border: none; border-top: 1px solid #e7ede7; }
QCheckBox#reviewCheck { color: #687d6d; font-size: 11px; padding: 0; }
QFrame#reviewArea QPushButton { min-height: 20px; }
QFrame#reviewArea QPushButton#textButton { min-height: 12px; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 3px 0; }
QScrollBar::handle:vertical { background: #cddacf; border-radius: 3px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #abc3ae; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""
