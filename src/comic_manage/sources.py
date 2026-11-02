"""Read-only discovery of image folders and CBZ files, including UNC paths."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from threading import Event
from typing import Callable, Iterator
import zipfile

from .metadata import MAX_XML_BYTES, Metadata, from_comicinfo

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"})
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024**3
MAX_MEMBERS = 100_000
SKIP_DIRS = {".git", ".venv", "__pycache__", "@eadir", "#recycle", "$recycle.bin", ".comic-manage"}


class Cancelled(Exception):
    pass


def check_cancel(cancel: Event | None) -> None:
    if cancel and cancel.is_set():
        raise Cancelled("任务已取消；已完成的文件保留。")


def natural_key(name: str) -> tuple:
    return tuple((1, int(s)) if s.isdecimal() else (0, s.casefold()) for s in re.split(r"(\d+)", name)) + ((2, name),)


def is_inside(path: Path, parent: Path) -> bool:
    return path.resolve().is_relative_to(parent.resolve())


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def normalized_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def source_id(path: Path) -> str:
    return hashlib.sha256(normalized_path(path).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Member:
    name: str
    size: int
    stamp: str


@dataclass
class Snapshot:
    path: Path
    kind: str
    pages: list[Member]
    extras: list[Member] = field(default_factory=list)
    xml: bytes = b""
    fingerprint: str = ""

    @contextmanager
    def reader(self):
        if self.kind == "cbz":
            with zipfile.ZipFile(self.path) as archive:
                yield lambda member: archive.open(member.name)
        else:
            yield lambda member: (self.path / member.name).open("rb")

    def page_bytes(self, index: int) -> bytes:
        if not 0 <= index < len(self.pages):
            raise ValueError("页码超出范围。")
        with self.reader() as open_member:
            with open_member(self.pages[index]) as handle:
                data = handle.read(MAX_MEMBER_BYTES + 1)
                if len(data) > MAX_MEMBER_BYTES:
                    raise ValueError("单张图片超过 512 MiB。")
                return data

    def initial_metadata(self) -> tuple[Metadata, str]:
        title = self.path.stem if self.kind == "cbz" else self.path.name
        if self.xml:
            try:
                return from_comicinfo(self.xml, title), ""
            except (ValueError, UnicodeError) as exc:
                return Metadata(original_title=title), f"原 ComicInfo 未读取：{exc}"
            except Exception as exc:
                return Metadata(original_title=title), f"原 ComicInfo 格式错误：{exc}"
        return Metadata(original_title=title), ""


def _safe_zip_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (bool(name) and "\\" not in name and not path.is_absolute()
            and not any(p in ("", ".", "..") or ":" in p for p in name.split("/")))


def inspect_source(path: Path) -> Snapshot:
    path = path.resolve(strict=True)
    if path.is_dir():
        pages: list[Member] = []
        sidecars = []
        for child in sorted(path.iterdir(), key=lambda x: natural_key(x.name)):
            if is_link(child) or not child.is_file():
                continue
            stat = child.stat()
            if child.suffix.lower() in IMAGE_EXTENSIONS:
                if stat.st_size > MAX_MEMBER_BYTES:
                    raise ValueError(f"图片超过 512 MiB：{child.name}")
                pages.append(Member(child.name, stat.st_size, str(stat.st_mtime_ns)))
            elif child.name.casefold() == "comicinfo.xml":
                sidecars.append(child)
        if len(sidecars) > 1:
            raise ValueError("发现多个大小写不同的 ComicInfo.xml。")
        xml = b""
        if sidecars:
            with sidecars[0].open("rb") as handle:
                xml = handle.read(MAX_XML_BYTES + 1)
            if len(xml) > MAX_XML_BYTES:
                raise ValueError("ComicInfo.xml 超过 2 MiB。")
        result = Snapshot(path, "folder", pages, xml=xml)
        signature: object = [(m.name, m.size, m.stamp) for m in pages]
    elif path.suffix.lower() == ".cbz":
        stat = path.stat()
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise ValueError("CBZ 条目超过安全上限。")
            seen = set()
            pages, extras, xml_names = [], [], []
            for info in infos:
                if info.is_dir():
                    continue
                if not _safe_zip_name(info.orig_filename):
                    raise ValueError(f"CBZ 含不安全的内部路径：{info.filename}")
                key = info.filename.casefold()
                if key in seen:
                    raise ValueError(f"CBZ 含重名条目：{info.filename}")
                seen.add(key)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("CBZ 中不支持符号链接。")
                if info.flag_bits & 1:
                    raise ValueError("暂不支持加密 CBZ。")
                if info.file_size > MAX_MEMBER_BYTES:
                    raise ValueError(f"CBZ 单条目超过 512 MiB：{info.filename}")
                if PurePosixPath(info.filename).name.casefold() == "comicinfo.xml":
                    xml_names.append(info.filename)
                    continue
                member = Member(info.filename, info.file_size, str(info.CRC))
                if PurePosixPath(info.filename).suffix.lower() in IMAGE_EXTENSIONS:
                    pages.append(member)
                else:
                    extras.append(member)
            if len(xml_names) > 1:
                raise ValueError("CBZ 中含多个 ComicInfo.xml，请先消除歧义。")
            xml = b""
            if xml_names:
                if archive.getinfo(xml_names[0]).file_size > MAX_XML_BYTES:
                    raise ValueError("ComicInfo.xml 超过 2 MiB。")
                xml = archive.read(xml_names[0])
            pages.sort(key=lambda m: natural_key(m.name))
            extras.sort(key=lambda m: natural_key(m.name))
            result = Snapshot(path, "cbz", pages, extras, xml)
            signature = [stat.st_size, stat.st_mtime_ns, [(i.filename, i.file_size, i.CRC) for i in infos]]
    else:
        raise ValueError("请选择图片文件夹或 CBZ 文件。")
    if not result.pages:
        raise ValueError("没有找到支持的图片。")
    if len(result.pages) + len(result.extras) > MAX_MEMBERS:
        raise ValueError("文件数量超过安全上限。")
    if sum(m.size for m in result.pages + result.extras) > MAX_TOTAL_BYTES:
        raise ValueError("单本漫画解压后超过 64 GiB。")
    result.fingerprint = hashlib.sha256(json.dumps(
        [signature, hashlib.sha256(result.xml).hexdigest()], ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    return result


def discover(root: Path, *, exclude: list[Path] | None = None, cancel: Event | None = None,
             on_error: Callable[[Path, str], None] | None = None) -> Iterator[Snapshot]:
    root = root.resolve(strict=True)
    excluded = [p.resolve() for p in (exclude or [])]
    if not root.is_dir():
        raise ValueError("扫描根路径必须是一个目录。")

    def error(path: Path, message: str):
        if on_error:
            on_error(path, message)

    def walk_error(exc: OSError):
        error(Path(exc.filename or str(root)), str(exc))

    for current, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        check_cancel(cancel)
        folder = Path(current)
        if any(is_inside(folder, p) for p in excluded):
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d.casefold() not in SKIP_DIRS
                         and not is_link(folder / d)
                         and not any(is_inside(folder / d, p) for p in excluded))
        candidates = ([folder] if any(Path(f).suffix.lower() in IMAGE_EXTENSIONS for f in files) else [])
        candidates += [folder / f for f in sorted(files, key=natural_key)
                       if Path(f).suffix.lower() == ".cbz" and not is_link(folder / f)]
        for candidate in candidates:
            check_cancel(cancel)
            try:
                yield inspect_source(candidate)
            except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                error(candidate, str(exc))
