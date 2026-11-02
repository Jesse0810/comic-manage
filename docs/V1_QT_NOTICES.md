# Third-party notices

Comic Manage uses Python's standard library and Qt for Python (PySide6 Essentials).

- **PySide6 / Shiboken6**: Qt Company, LGPL-3.0 / GPL-3.0 / commercial licensing options. See the [Qt for Python license documentation](https://doc.qt.io/qtforpython-6/licenses.html). Qt shared libraries remain separate in the portable distribution, rather than statically linked. The distribution retains package license files.
- **Qt**: Qt Company and contributors. Qt module licenses and third-party notices are provided with the upstream wheels and retained in the portable package. See [Qt licensing](https://doc.qt.io/qt-6/licensing.html).
- **PyInstaller** (build tool): GPL-2.0-or-later with the PyInstaller exception for distributing bundled applications. See [PyInstaller license](https://pyinstaller.org/en/stable/license.html).
- **Python**: Python Software Foundation License. See [Python license](https://docs.python.org/3/license.html).

This file records upstream licenses; it does not grant a license to manga files, imported metadata, or this repository's original source. The repository owner has not yet selected a license for original source code.

The portable package includes upstream GPL / LGPL texts, Python's license, and license files shipped in the selected wheels under `licenses/`. Qt DLLs can be replaced in `_internal/PySide6/` with ABI-compatible builds. These components are unmodified. Corresponding source is available from the upstream versioned repositories:

- [Qt base v6.10.2](https://github.com/qt/qtbase/tree/v6.10.2)
- [Qt image formats v6.10.2](https://github.com/qt/qtimageformats/tree/v6.10.2)
- [Qt SVG v6.10.2](https://github.com/qt/qtsvg/tree/v6.10.2)
- [Qt for Python / Shiboken v6.10.2](https://github.com/pyside/pyside-setup/tree/v6.10.2)
- [Qt source archive index](https://download.qt.io/archive/qt/6.10/6.10.2/)
