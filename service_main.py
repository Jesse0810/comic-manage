"""Source and PyInstaller entry point for the standard-library local service."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / "src"))

from comic_manage.service import main

if __name__ == "__main__":
    main()
