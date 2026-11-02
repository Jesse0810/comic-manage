"""Bundle the stdlib-only file service. Qt is deliberately absent from this build."""
from pathlib import Path
from importlib.metadata import distribution
import os
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = os.environ.copy()
windows = Path(os.environ["SystemRoot"])
env["PATH"] = os.pathsep.join(str(p) for p in (
    Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs",
    windows / "System32", windows,
))
env["PYINSTALLER_CONFIG_DIR"] = str(root / "build" / "pyinstaller-cache")
subprocess.run([
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--console",
    "--name", "ComicManageService", "--paths", str(root / "src"),
    "--distpath", str(root / "build" / "service"),
    "--workpath", str(root / "build" / "service-work"),
    "--specpath", str(root / "build" / "service-work"), str(root / "service_main.py"),
], check=True, cwd=root, env=env)
destination = root / "build" / "service" / "ComicManageService"
notices = destination / "licenses"
notices.mkdir(exist_ok=True)
shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", notices / "Python-LICENSE.txt")
dist = distribution("pyinstaller")
for file in dist.files or []:
    if "/licenses/" in str(file).replace("\\", "/") and Path(dist.locate_file(file)).is_file():
        shutil.copy2(dist.locate_file(file), notices / Path(file).name)
print(destination)
