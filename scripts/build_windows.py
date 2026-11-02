"""Build a portable Windows directory and ZIP; run from any working directory."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
from importlib.metadata import distribution

root = Path(__file__).resolve().parents[1]
if os.name != "nt":
    raise SystemExit("Windows 构建请在 Windows 上执行。")
build_env = os.environ.copy()
# Other tools on PATH may ship incompatible python3.dll, ICU or CRT DLLs.
# Resolve dependencies only against this interpreter and Windows system files.
windows = Path(os.environ["SystemRoot"])
build_env["PATH"] = os.pathsep.join(str(p) for p in (
    Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs",
    windows / "System32", windows,
))
build_env["PYINSTALLER_CONFIG_DIR"] = str(root / "build" / "pyinstaller-cache")
subprocess.run([
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
    "--name", "ComicManage", "--paths", str(root / "src"),
    "--distpath", str(root / "dist"), "--workpath", str(root / "build"),
    "--specpath", str(root / "build"), str(root / "run.py"),
], cwd=root, check=True, env=build_env)
destination = root / "dist" / "ComicManage"
for name in ("README.md", "THIRD_PARTY_NOTICES.md"):
    shutil.copy2(root / name, destination / name)
subprocess.run([str(destination / "ComicManage.exe"), "--help"], cwd=destination, check=True, timeout=30)
licenses = destination / "licenses"
shutil.copytree(root / "licenses", licenses, dirs_exist_ok=True)
shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python-LICENSE.txt")
for package in ("PySide6_Essentials", "shiboken6", "pyinstaller"):
    dist = distribution(package)
    for file in dist.files or []:
        if "/licenses/" in str(file).replace("\\", "/"):
            source = Path(dist.locate_file(file))
            if source.is_file():
                target = licenses / package / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
print(shutil.make_archive(str(root / "dist" / "ComicManage-v1-windows-x64"), "zip", root / "dist", "ComicManage"))
