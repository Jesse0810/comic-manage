"""Decode every CBZ page before publishing an automatically indexed archive."""
from pathlib import PurePosixPath
from threading import Event
import warnings
import zipfile

from PIL import Image

from .sources import IMAGE_EXTENSIONS, check_cancel


def verify_page_images(path, cancel: Event | None = None) -> None:
    with zipfile.ZipFile(path) as archive:
        pages = [entry for entry in archive.infolist()
                 if not entry.is_dir() and entry.filename.startswith("pages/")
                 and PurePosixPath(entry.filename).suffix.lower() in IMAGE_EXTENSIONS]
        if not pages:
            raise ValueError("CBZ 中没有可验证的漫画页面。")
        for entry in pages:
            check_cancel(cancel)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with archive.open(entry) as stream, Image.open(stream) as image:
                        image.verify()
                    with archive.open(entry) as stream, Image.open(stream) as image:
                        image.load()
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
                    Image.DecompressionBombWarning) as error:
                raise ValueError(f"图片无法完整读取，未发布 CBZ：{entry.filename}") from error
        check_cancel(cancel)
