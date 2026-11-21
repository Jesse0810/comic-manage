"""Read-only page previews from the currently configured Komga scope."""
from __future__ import annotations

import base64
from threading import Event
from urllib.parse import quote

from .komga_client import KomgaClient, KomgaError, normalize_url
from .sources import check_cancel

MAX_REMOTE_PAGE_BYTES = 32 * 1024 * 1024


def image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def remote_page(repo, identity: str, index: int, url: str, api_key: str,
                library_id: str, cancel: Event | None = None) -> dict:
    if type(index) is not int or index < 0:
        raise ValueError("页码必须是非负整数。")
    check_cancel(cancel)
    comic = repo.get(identity)
    source = repo.get_komga_source(identity)
    if comic.kind != "komga" or not source:
        raise ValueError("此记录不是 Komga 远端漫画。")
    if source.get("unavailable"):
        raise ValueError("远端漫画已不可用，请重新读取书库后重试。")
    normalized = normalize_url(url)
    if source.get("server_url") != normalized or source.get("library_id") != library_id:
        raise KomgaError("此漫画属于其他 Komga 连接或书库，请切回原连接后预览。", "KOMGA_SCOPE")
    book_id = source.get("book_id")
    if not isinstance(book_id, str) or not book_id:
        raise ValueError("远端漫画标识无效，请重新读取书库。")
    client = KomgaClient(normalized, api_key, cancel=cancel)
    escaped_id = quote(book_id, safe="")
    book = client.request("GET", f"api/v1/books/{escaped_id}")
    if (not isinstance(book, dict) or book.get("id") != book_id
            or book.get("libraryId") != library_id or book.get("deleted")
            or book.get("seriesId") != source.get("series_id")):
        raise KomgaError("远端漫画身份或书库归属已变化，请重新读取书库。", "KOMGA_SCOPE")
    media = book.get("media")
    total = media.get("pagesCount") if isinstance(media, dict) else None
    if type(total) is not int or total < 1:
        raise ValueError("远端漫画尚无可预览的图片页，请等待 Komga 分析完成。")
    if index >= total:
        raise ValueError("页码超出远端漫画的实际页数，请重新选择。")
    page_number = index + 1
    data, declared = client.request_bytes(
        f"api/v1/books/{escaped_id}/pages/{page_number}?convert=jpeg&zero_based=false&contentNegotiation=false",
        MAX_REMOTE_PAGE_BYTES,
    )
    detected = image_mime(data)
    if declared == "image/jpg":
        declared = "image/jpeg"
    if detected is None or declared != detected:
        raise KomgaError("Komga 返回的页面不是受支持的图片，已停止预览。", "KOMGA_MEDIA")
    check_cancel(cancel)
    return {
        "dataUrl": f"data:{detected};base64,{base64.b64encode(data).decode('ascii')}",
        "name": f"Komga 第 {page_number} 页", "index": index, "total": total,
    }
