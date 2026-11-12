"""Exercise scoped read-only remote previews through a local HTTP server."""
import base64
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from comic_manage.komga_client import KomgaClient, KomgaError
from comic_manage.komga_media import remote_page
from comic_manage.sources import Cancelled


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


class PreviewRepository:
    def __init__(self, url):
        self.comic = SimpleNamespace(kind="komga", page_count=999)
        self.source = {"server_url": url, "library_id": "library", "book_id": "book + 中文",
                       "series_id": "series", "unavailable": False}

    def get(self, identity):
        if identity != "local-id":
            raise ValueError("作品记录不存在或已移除。")
        return self.comic

    def get_komga_source(self, identity):
        return self.source


class KomgaMediaTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.book = {"id": "book + 中文", "libraryId": "library", "seriesId": "series", "deleted": False,
                     "media": {"pagesCount": 2}}
        self.page_data = PNG
        self.content_type = "image/png; charset=binary"
        self.page_status = 200
        self.after_page = lambda: None
        self.json_response = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                owner.requests.append((self.command, parsed, self.headers.get("X-API-Key"), self.headers.get("Accept")))
                path = unquote(parsed.path)
                if path == "/prefix/api/v1/books/book + 中文":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(owner.book).encode())
                elif path.startswith("/prefix/api/v1/books/book + 中文/pages/"):
                    self.send_response(owner.page_status)
                    if owner.page_status == 302:
                        self.send_header("Location", "/credential-leak")
                    self.send_header("Content-Type", owner.content_type)
                    self.end_headers()
                    self.wfile.write(owner.page_data)
                    owner.after_page()
                elif path == "/prefix/json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(owner.json_response)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_PATCH(self):
                owner.requests.append((self.command, urlsplit(self.path), self.headers.get("X-API-Key"), self.headers.get("Accept")))
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(204)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/prefix"
        self.key = "mock-key-never-returned"
        self.repo = PreviewRepository(self.url)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def preview(self, index=0, **changes):
        arguments = {"repo": self.repo, "identity": "local-id", "index": index,
                     "url": self.url, "api_key": self.key, "library_id": "library"}
        arguments.update(changes)
        return remote_page(**arguments)

    def test_live_pages_are_one_based_and_scoped_without_file_or_progress_writes(self):
        result = self.preview(1)
        self.assertEqual(result["dataUrl"], "data:image/png;base64," + base64.b64encode(PNG).decode())
        self.assertEqual((result["index"], result["total"]), (1, 2))
        self.assertEqual([request[0] for request in self.requests], ["GET", "GET"])
        self.assertTrue(self.requests[1][1].path.endswith("/pages/2"))
        self.assertEqual(parse_qs(self.requests[1][1].query), {"convert": ["jpeg"], "zero_based": ["false"], "contentNegotiation": ["false"]})
        self.assertTrue(all(request[2] == self.key for request in self.requests))
        self.assertNotIn(self.key, json.dumps(result))

    def test_local_identity_scope_and_page_index_reject_before_network(self):
        for changes in ({"index": -1}, {"index": True}, {"index": "0"}, {"identity": "removed"},
                        {"library_id": "other"}, {"url": self.url + "/other"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.preview(**changes)
        self.repo.source["unavailable"] = True
        with self.assertRaises(ValueError):
            self.preview()
        self.repo.source["unavailable"] = False
        self.repo.comic.kind = "folder"
        with self.assertRaises(ValueError):
            self.preview()
        self.assertEqual(self.requests, [])

    def test_remote_identity_and_live_page_count_are_rechecked_before_image(self):
        original = deepcopy(self.book)
        variants = [{"id": "other"}, {"libraryId": "other"}, {"seriesId": "other"}, {"deleted": True},
                    {"media": {"pagesCount": True}}, {"media": {"pagesCount": 0}}]
        for values in variants:
            self.book = {**original, **values}
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.preview()
        self.book = original
        with self.assertRaises(ValueError):
            self.preview(2)
        self.assertTrue(all("/pages/" not in request[1].path for request in self.requests))

    def test_content_type_and_magic_must_agree(self):
        for content_type, data in (("text/html", PNG), ("image/png", b"<html>login</html>"),
                                   ("image/jpeg", PNG), ("image/svg+xml", b"<svg/>")):
            self.content_type, self.page_data = content_type, data
            with self.subTest(content_type=content_type), self.assertRaisesRegex(KomgaError, "图片"):
                self.preview()
        for content_type, data in (("image/jpeg", b"\xff\xd8\xffsample"), ("image/gif", b"GIF89asample"),
                                   ("image/webp", b"RIFFxxxxWEBPsample")):
            self.content_type, self.page_data = content_type, data
            self.assertTrue(self.preview()["dataUrl"].startswith(f"data:{content_type};base64,"))

    def test_image_body_is_bounded_and_cancellation_is_observed_after_read(self):
        self.page_data = b"\x89PNG\r\n\x1a\n" + b"x" * 100
        with patch("comic_manage.komga_media.MAX_REMOTE_PAGE_BYTES", 32), self.assertRaisesRegex(KomgaError, "响应过大"):
            self.preview()
        cancelled = Event()
        self.after_page = cancelled.set
        self.page_data = PNG
        with self.assertRaises(Cancelled):
            self.preview(cancel=cancelled)
        before = len(self.requests)
        with self.assertRaises(Cancelled):
            self.preview(cancel=cancelled)
        self.assertEqual(len(self.requests), before)

    def test_auth_and_redirect_failures_do_not_return_secrets_or_follow(self):
        for status, code in ((401, "KOMGA_AUTH"), (403, "KOMGA_FORBIDDEN"),
                             (302, "KOMGA_REDIRECT"), (500, "KOMGA_HTTP")):
            self.page_status = status
            with self.subTest(status=status), self.assertRaises(KomgaError) as raised:
                self.preview()
            self.assertNotIn(self.key, str(raised.exception))
            self.assertEqual(raised.exception.code, code)
        self.assertTrue(all("credential-leak" not in request[1].path for request in self.requests))

    def test_json_request_contract_remains_intact_and_bytes_limit_is_validated(self):
        client = KomgaClient(self.url, self.key)
        self.json_response = b'{"hello":"world"}'
        self.assertEqual(client.request("GET", "json"), {"hello": "world"})
        self.assertEqual(client.request("PATCH", "metadata", {"summary": "value"}), {})
        self.json_response = b"not json"
        with self.assertRaisesRegex(KomgaError, "JSON"):
            client.request("GET", "json")
        self.json_response = b'{"long":"response"}'
        with patch("comic_manage.komga_client.MAX_RESPONSE_BYTES", 4), self.assertRaisesRegex(KomgaError, "响应过大"):
            client.request("GET", "json")
        before = len(self.requests)
        for limit in (0, -1, True):
            with self.assertRaises(ValueError):
                client.request_bytes("image", limit)
        self.assertEqual(len(self.requests), before)


if __name__ == "__main__":
    unittest.main()
