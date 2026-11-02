"""Verified, non-destructive CBZ exports; destination files are never overwritten."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from threading import Event
from typing import Callable
import zipfile

from .metadata import parse_xml, to_comicinfo
from .repository import Comic, now
from .sources import Snapshot, check_cancel, inspect_source, is_inside

CHUNK_SIZE = 1024 * 1024


def safe_segment(text: str, max_units: int = 90) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .") or "未命名"
    # Windows limits path segments in UTF-16 code units, not Python characters.
    text = text.encode("utf-16-le")[:max_units * 2].decode("utf-16-le", errors="ignore").rstrip(" .")
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", text, re.I):
        text = "_" + text
    return text or "未命名"


@dataclass
class ExportPlan:
    comic: Comic
    destination: Path
    snapshot: Snapshot
    xml: bytes


def make_plan(comic: Comic, output_root: Path) -> ExportPlan:
    if comic.status not in ("approved", "packaged"):
        raise ValueError("需要先人工审核通过，才能生成 CBZ。")
    snapshot = inspect_source(Path(comic.source))
    if snapshot.fingerprint != comic.fingerprint:
        raise ValueError("源文件已变化，请重新扫描并审核。")
    root = output_root.resolve()
    if snapshot.kind == "folder" and is_inside(root, snapshot.path):
        raise ValueError("成品目录不能位于原始图片文件夹内。请选择独立目录。")
    metadata = comic.metadata
    xml = to_comicinfo(metadata, len(snapshot.pages))
    author = safe_segment(" & ".join(metadata.authors), 60)
    name = safe_segment(metadata.display_title)
    destination = root / author / f"{name} [{comic.id[:8]}].cbz"
    if destination.exists():
        raise FileExistsError(f"目标已存在，v1 不覆盖文件：{destination}")
    if destination.resolve() == snapshot.path:
        raise ValueError("输出路径与源文件相同。")
    # Reserve headroom for temporary names and Windows installations without long paths.
    if os.name == "nt" and len(str(destination).encode("utf-16-le")) // 2 > 235:
        raise ValueError("输出完整路径过长，请选择层级更浅的成品目录。完整标题仍保存在 XML 中。")
    return ExportPlan(comic, destination, snapshot, xml)


def verify_archive(path: Path, expected: dict[str, str], xml: bytes,
                   cancel: Event | None = None) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected) | {"ComicInfo.xml"}:
            raise ValueError("校验失败：压缩包文件清单不一致。")
        # Reading every entry checks ZIP CRC as well as our stronger SHA-256 digest.
        for name, digest in expected.items():
            sha = hashlib.sha256()
            with archive.open(name) as handle:
                while chunk := handle.read(CHUNK_SIZE):
                    check_cancel(cancel)
                    sha.update(chunk)
            if sha.hexdigest() != digest:
                raise ValueError(f"校验失败：图片/附件内容不一致：{name}")
        if archive.read("ComicInfo.xml") != xml:
            raise ValueError("校验失败：ComicInfo.xml 与审核数据不一致。")
        page_count = int(parse_xml(xml).findtext("PageCount", "0"))
        if page_count != sum(name.startswith("pages/") for name in expected):
            raise ValueError("校验失败：页数不一致。")
    return {"verified_at": now(), "page_count": page_count, "sha256": expected,
            "comicinfo_sha256": hashlib.sha256(xml).hexdigest()}


def export_cbz(plan: ExportPlan, *, cancel: Event | None = None,
               progress: Callable[[str], None] | None = None) -> dict:
    check_cancel(cancel)
    if inspect_source(plan.snapshot.path).fingerprint != plan.snapshot.fingerprint:
        raise ValueError("源文件在预览后发生变化，请重新扫描并审核。")
    destination = plan.destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=".comic-", suffix=".partial", dir=destination.parent)
    temporary = Path(name)
    expected: dict[str, str] = {}
    try:
        with os.fdopen(handle, "w+b") as output:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                with plan.snapshot.reader() as open_member:
                    width = max(5, len(str(len(plan.snapshot.pages))))
                    members = [(m, f"pages/{i:0{width}d}{PurePosixPath(m.name).suffix.lower()}")
                               for i, m in enumerate(plan.snapshot.pages, 1)]
                    # Retain CBZ attachments in an isolated namespace; folders are image-only.
                    members += [(m, "extras/" + m.name) for m in plan.snapshot.extras]
                    for index, (member, archive_name) in enumerate(members, 1):
                        check_cancel(cancel)
                        if progress:
                            progress(f"{plan.comic.metadata.display_title} · {index}/{len(members)}")
                        sha = hashlib.sha256()
                        written = 0
                        with open_member(member) as source, archive.open(archive_name, "w", force_zip64=True) as target:
                            while chunk := source.read(CHUNK_SIZE):
                                check_cancel(cancel)
                                written += len(chunk)
                                if written > member.size:
                                    raise ValueError(f"源文件在读取时变长：{member.name}")
                                target.write(chunk)
                                sha.update(chunk)
                        if written != member.size:
                            raise ValueError(f"源文件在读取时变短：{member.name}")
                        expected[archive_name] = sha.hexdigest()
                archive.writestr("ComicInfo.xml", plan.xml)
            output.flush()
            os.fsync(output.fileno())
        if progress:
            progress(f"正在逐文件校验 · {plan.comic.metadata.display_title}")
        report = verify_archive(temporary, expected, plan.xml, cancel)
        if inspect_source(plan.snapshot.path).fingerprint != plan.snapshot.fingerprint:
            raise ValueError("源文件在生成期间发生变化，已放弃此次成品。请重新扫描。")
        check_cancel(cancel)
        # Same-directory rename is atomic; Windows rename refuses an existing destination.
        # POSIX uses link's exclusive creation instead of rename's replacement semantics.
        if os.name == "nt":
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
            temporary.unlink()
        return report
    finally:
        temporary.unlink(missing_ok=True)
