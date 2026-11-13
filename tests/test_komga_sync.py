"""Exercise real local HTTP requests without contacting a user's Komga server."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import zipfile

from comic_manage.komga_client import KomgaError, normalize_url, path_matches
from comic_manage.metadata import Metadata, to_comicinfo
from comic_manage.service import ComicService, error_payload
from comic_manage.sources import inspect_source


class KomgaSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = ComicService(self.root / "data")
        self.remote_books = {}
        self.remote_series = {}
        self.requests = []
        self.page_size = 1
        self.before_request = lambda method, path: None
        self.after_patch = lambda path: None
        self.forced = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def do_PATCH(self):
                self.handle_request()

            def handle_request(self):
                parsed = urlsplit(self.path)
                path = parsed.path.removeprefix("/proxy/komga")
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = json.loads(raw) if raw else None
                owner.requests.append((self.command, self.path, body, self.headers.get("X-API-Key")))
                owner.before_request(self.command, path)
                forced = owner.forced.get((self.command, path))
                if forced:
                    code, response = forced
                elif path == "/api/v1/libraries":
                    code, response = 200, [{"id": "lib", "name": "漫画库"}, {"id": "other", "name": "其他书库"}]
                elif path == "/api/v1/books/list":
                    library = body["condition"]["libraryId"]["value"]
                    books = [book for book in owner.remote_books.values() if book["libraryId"] == library]
                    page = int(parse_qs(parsed.query)["page"][0])
                    start = page * owner.page_size
                    code, response = 200, {"content": books[start:start + owner.page_size], "last": start + owner.page_size >= len(books)}
                else:
                    parts = path.strip("/").split("/")
                    records = owner.remote_books if parts[2] == "books" else owner.remote_series
                    if self.command == "PATCH":
                        records[parts[3]]["metadata"].update(body)
                        owner.after_patch(path)
                        code, response = 204, None
                    else:
                        code, response = 200, deepcopy(records[parts[3]])
                self.send_response(code)
                if 300 <= code < 400:
                    self.send_header("Location", "/credential-leak")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if response is not None:
                    self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/proxy/komga"
        self.key = "test-secret-never-in-results"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def comic(self, identity: str, *, series="系列", oneshot=False, **fields):
        source = self.root / identity
        source.mkdir()
        (source / "1.jpg").write_bytes(b"image")
        snapshot = inspect_source(source)
        comic_id = self.service.repo.upsert_scan(snapshot)
        values = {"title": f"新标题-{identity}", "authors": ["作者"], "genre": "漫画", "age_rating": "Everyone", "tags": ["日常"],
                  "series": series, "number": "1", "collection_mode": "oneshot" if oneshot else "series"}
        values.update(fields)
        metadata = Metadata(**values)
        self.service.repo.save(comic_id, metadata)
        self.service.repo.approve(comic_id, snapshot)
        parent = "_oneshots" if oneshot else "series-folder"
        output = self.root / "output" / parent / f"{identity}.cbz"
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET output=? WHERE id=?", (str(output), comic_id))
        return self.service.repo.get(comic_id)

    def remote(self, comic, book_id: str, series_id="s", *, library="lib", oneshot=False):
        output = Path(comic.output or comic.source)
        url = "/nas/漫画库/" + output.parent.name + "/" + output.name
        self.remote_books[book_id] = {"id": book_id, "seriesId": series_id, "libraryId": library,
                                     "url": url, "deleted": False, "metadata": {"title": "远端旧标题",
                                     "authors": [{"name": "旧作者", "role": "writer"}, {"name": "保留勾线", "role": "inker"}],
                                     "notableEntities": [{"kind": "team", "name": "保留团队"}, {"kind": "character", "name": "旧角色"}, {"kind": "原作", "name": "旧原作"}],
                                     "links": [{"label": "人工标签", "url": "https://example.org/page"}]}}
        self.remote_series[series_id] = {"id": series_id, "libraryId": library, "url": url if oneshot else url.rsplit("/", 1)[0],
                                        "booksCount": sum(book["seriesId"] == series_id and not book["deleted"] for book in self.remote_books.values()),
                                        "oneshot": oneshot, "metadata": {"title": "远端旧系列", "titleSort": "手工排序",
                                        "alternateTitles": [{"label": "zh-Hans", "title": "旧译名"}, {"label": "en", "title": "Retain English"}]}}

    def sync(self, *comics, cancel=None):
        return self.service.call("komga_sync", {"ids": [comic.id for comic in comics], "url": self.url,
                                 "apiKey": self.key, "libraryId": "lib"}, cancel)

    def patches(self, kind):
        return [request[2] for request in self.requests if request[0] == "PATCH" and f"/{kind}/" in request[1]]

    def test_connection_preserves_base_path_and_never_returns_credentials(self):
        response = self.service.call("komga_test", {"url": self.url + "/", "apiKey": self.key})
        self.assertEqual(response["url"], self.url)
        self.assertEqual(response["libraries"][0]["id"], "lib")
        self.assertNotIn(self.key, json.dumps(response))
        self.assertEqual(self.requests[0][1], "/proxy/komga/api/v1/libraries")
        self.assertEqual(self.requests[0][3], self.key)

    def test_native_book_and_complete_series_sync_preserve_unmanaged_values(self):
        comic = self.comic("first", summary="完整简介", characters=["本机角色"], publisher="发行方", genre="漫画",
                           web=["https://example.org/page"], translator="汉化组", year="2024", month="2", day="29",
                           manga="YesAndRightToLeft", reading_direction="RIGHT_TO_LEFT", localized_series="新译名")
        self.remote(comic, "b")
        response = self.sync(comic)
        self.assertEqual((response["updated"], response["seriesUpdated"]), (1, 1))
        patch = self.patches("books")[0]
        self.assertEqual(patch["title"], comic.metadata.title)
        self.assertEqual(patch["releaseDate"], "2024-02-29")
        self.assertTrue(patch["titleLock"])
        self.assertIn({"name": "保留勾线", "role": "inker"}, patch["authors"])
        self.assertNotIn({"name": "旧作者", "role": "writer"}, patch["authors"])
        self.assertIn({"kind": "team", "name": "保留团队"}, patch["notableEntities"])
        self.assertIn({"kind": "character", "name": "本机角色"}, patch["notableEntities"])
        self.assertNotIn({"kind": "character", "name": "旧角色"}, patch["notableEntities"])
        self.assertEqual(patch["links"][0]["label"], "人工标签")
        series = self.patches("series")[0]
        self.assertEqual(series["title"], "系列")
        self.assertNotIn("titleSort", series)
        self.assertEqual(series["readingDirection"], "RIGHT_TO_LEFT")
        self.assertEqual(series["alternateTitles"], [{"label": "en", "title": "Retain English"}, {"label": "zh-Hans", "title": "新译名"}])
        mapping = self.service.repo.komga_mapping(self.url, "lib", comic.id)
        self.assertEqual(mapping["book_id"], "b")
        with self.service.repo.connect() as db:
            self.assertNotIn(self.key, str(list(db.iterdump())))

    def test_partial_series_updates_books_and_skips_aggregate_fields(self):
        first, second = self.comic("first"), self.comic("second", number="2")
        self.remote(first, "b1")
        self.remote(second, "b2")
        response = self.sync(first)
        self.assertEqual(response["updated"], 1)
        self.assertEqual(response["seriesUpdated"], 0)
        self.assertIn("全选", response["series"][0]["message"])
        self.assertEqual(self.patches("series"), [])
        self.assertGreaterEqual(len([item for item in self.requests if item[0] == "POST"]), 4)

    def test_full_series_aggregates_mixed_age_genre_and_max_known_count(self):
        first = self.comic("first", genre="漫画", count="2", publisher="发行方")
        second = self.comic("second", number="2", genre="CG集", count="3", age_rating="R18+", publisher="")
        self.remote(first, "b1")
        self.remote(second, "b2")
        response = self.sync(first, second)
        self.assertEqual(response["seriesUpdated"], 1)
        payload = self.patches("series")[0]
        self.assertEqual(payload["ageRating"], 18)
        self.assertEqual(payload["genres"], ["漫画", "CG集"])
        self.assertEqual(payload["totalBookCount"], 3)
        self.assertEqual(payload["publisher"], "发行方")

    def test_tied_series_values_are_omitted_while_safe_aggregates_are_updated(self):
        first = self.comic("first", publisher="甲")
        second = self.comic("second", number="2", publisher="乙")
        self.remote(first, "b1")
        self.remote(second, "b2")
        response = self.sync(first, second)
        self.assertEqual(response["updated"], 2)
        self.assertEqual(response["seriesUpdated"], 1)
        self.assertNotIn("publisher", self.patches("series")[0])
        self.assertEqual(self.patches("series")[0]["ageRating"], 0)
        self.assertIn("平票", response["series"][0]["warnings"][0])

    def test_conflicting_aliases_do_not_block_other_series_aggregates(self):
        first = self.comic("first", localized_series="译名甲")
        second = self.comic("second", number="2", age_rating="R18+", localized_series="译名乙")
        self.remote(first, "b1")
        self.remote(second, "b2")
        response = self.sync(first, second)
        self.assertEqual(response["seriesUpdated"], 1)
        self.assertNotIn("alternateTitles", self.patches("series")[0])
        self.assertEqual(self.patches("series")[0]["ageRating"], 18)
        self.assertIn("同标签别名", response["series"][0]["warnings"][0])

    def test_series_count_is_rechecked_immediately_before_write(self):
        comic = self.comic("first")
        self.remote(comic, "b")
        self.before_request = lambda method, path: self.remote_series["s"].update(booksCount=2) if method == "GET" and path == "/api/v1/series/s" else None
        response = self.sync(comic)
        self.assertEqual(response["updated"], 1)
        self.assertEqual(response["seriesUpdated"], 0)
        self.assertIn("当前册数", response["series"][0]["message"])
        self.assertEqual(self.patches("series"), [])

    def test_mode_mismatch_only_skips_series_and_oneshot_uses_file_url(self):
        comic = self.comic("first", oneshot=True)
        self.remote(comic, "b", oneshot=False)
        response = self.sync(comic)
        self.assertEqual(response["updated"], 1)
        self.assertIn("组织不同", response["series"][0]["message"])
        self.remote_series["s"]["oneshot"] = True
        self.remote_series["s"]["url"] = self.remote_books["b"]["url"]
        response = self.sync(comic)
        self.assertEqual(response["seriesUpdated"], 1)
        self.assertEqual(self.patches("series")[-1]["totalBookCount"], 1)
        self.assertNotIn("number", self.patches("books")[-1])

    def test_drafts_and_ambiguous_paths_are_skipped_before_any_writes(self):
        comic = self.comic("first")
        self.remote(comic, "b1")
        self.remote(comic, "b2")
        response = self.sync(comic)
        self.assertEqual(response["skipped"], 1)
        self.assertEqual(self.patches("books"), [])
        self.service.repo.save(comic.id, comic.metadata)
        response = self.sync(comic)
        self.assertIn("审核", response["books"][0]["message"])

    def test_direct_source_cbz_matches_path_even_after_local_title_changes(self):
        source = self.root / "imports" / "source.cbz"
        source.parent.mkdir()
        metadata = Metadata(title="已改的新标题", authors=["作者"], genre="漫画", age_rating="Everyone", tags=["日常"])
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("1.jpg", b"image")
            archive.writestr("ComicInfo.xml", to_comicinfo(metadata, 1))
        snapshot = inspect_source(source)
        identity = self.service.repo.upsert_scan(snapshot)
        self.service.repo.save(identity, metadata)
        self.service.repo.approve(identity, snapshot)
        comic = self.service.repo.get(identity)
        self.remote(comic, "b", oneshot=True)
        self.assertEqual(self.sync(comic)["updated"], 1)
        self.assertEqual(self.remote_books["b"]["metadata"]["title"], "已改的新标题")

    def test_write_time_identity_check_prevents_cross_library_or_path_updates(self):
        comic = self.comic("first")
        self.remote(comic, "b")
        def change_identity(method, path):
            if method == "GET" and path == "/api/v1/books/b":
                self.remote_books["b"]["libraryId"] = "other"
        self.before_request = change_identity
        response = self.sync(comic)
        self.assertEqual(response["skipped"], 1)
        self.assertEqual(self.patches("books"), [])

    def test_cancel_retains_completed_book_and_stops_remaining_writes(self):
        first, second = self.comic("first"), self.comic("second", number="2")
        self.remote(first, "b1")
        self.remote(second, "b2")
        cancel = Event()
        self.after_patch = lambda path: cancel.set()
        response = self.sync(first, second, cancel=cancel)
        self.assertTrue(response["cancelled"])
        self.assertEqual((response["updated"], response["skipped"]), (1, 1))
        self.assertEqual(len(self.patches("books")), 1)
        self.assertEqual(self.patches("series"), [])

    def test_partial_dates_and_non_numeric_numbers_omit_unrepresentable_fields(self):
        comic = self.comic("first", year="2024", number="TPB1")
        self.remote(comic, "b")
        response = self.sync(comic)
        payload = self.patches("books")[0]
        self.assertNotIn("releaseDate", payload)
        self.assertNotIn("numberSort", payload)
        self.assertEqual(len(response["books"][0]["warnings"]), 2)
        self.assertIn("部分日期", response["books"][0]["warnings"][1])

    def test_explicit_clears_and_repeated_sync_use_reviewed_values_and_saved_mapping(self):
        comic = self.comic("first", tags=[], color="黑白")
        self.remote(comic, "b")
        self.remote_books["b"]["metadata"].update(summary="旧简介", tags=["旧标签"], releaseDate="2020-01-01")
        response = self.sync(comic)
        self.assertEqual(response["updated"], 1)
        payload = self.patches("books")[0]
        self.assertEqual(payload["summary"], "")
        self.assertEqual(payload["tags"], ["色彩:黑白"])
        self.assertEqual(payload["links"], [])
        self.assertIsNone(payload["releaseDate"])
        self.assertFalse(any(entity["kind"] == "character" for entity in payload["notableEntities"]))
        self.service.call("approve", {"id": comic.id, "metadata": {"title": "再次修改标题", "web": ["https://new.example/book"]}})
        response = self.sync(comic)
        self.assertEqual(response["updated"], 1)
        self.assertEqual(self.patches("books")[-1]["title"], "再次修改标题")
        self.assertEqual(self.patches("books")[-1]["links"], [{"label": "new.example", "url": "https://new.example/book"}])
        self.assertIsNone(self.service.repo.komga_mapping(self.url, "other", comic.id))

    def test_permission_failure_reports_partial_success_and_stops_further_writes(self):
        first, second, third = self.comic("first"), self.comic("second", number="2"), self.comic("third", number="3")
        self.remote(first, "b1")
        self.remote(second, "b2")
        self.remote(third, "b3")
        self.forced[("PATCH", "/api/v1/books/b2/metadata")] = (403, {"message": self.key})
        response = self.sync(first, second, third)
        self.assertEqual((response["updated"], response["failed"], response["skipped"]), (1, 1, 1))
        self.assertIn("权限", response["error"])
        self.assertNotIn(self.key, json.dumps(response))
        self.assertFalse(any(request[0] == "PATCH" and "/b3/" in request[1] for request in self.requests))
        self.assertEqual(self.patches("series"), [])

    def test_mapping_storage_failure_does_not_lose_acknowledged_remote_success(self):
        comic = self.comic("first")
        self.remote(comic, "b")
        with patch.object(self.service.repo, "save_komga_mapping", side_effect=sqlite3.OperationalError("disk full")):
            response = self.sync(comic)
        self.assertEqual(response["updated"], 1)
        self.assertEqual(response["failed"], 0)
        self.assertIn("映射保存失败", response["books"][0]["warnings"][0])
        self.assertEqual(self.remote_books["b"]["metadata"]["title"], comic.metadata.title)

    def test_authentication_and_redirect_errors_are_safe_and_do_not_follow(self):
        for status, code in ((401, "KOMGA_AUTH"), (403, "KOMGA_FORBIDDEN"), (302, "KOMGA_REDIRECT")):
            self.forced[("GET", "/api/v1/libraries")] = (status, {"message": self.key})
            with self.subTest(status=status), self.assertRaises(KomgaError) as raised:
                self.service.call("komga_test", {"url": self.url, "apiKey": self.key})
            payload = error_payload(raised.exception)
            self.assertEqual(payload["code"], code)
            self.assertNotIn(self.key, json.dumps(payload))
        self.assertFalse(any("credential-leak" in request[1] for request in self.requests))

    def test_invalid_urls_and_path_decoding_do_not_change_matching_scope(self):
        for url in ("file:///etc/passwd", "https://user:key@example.org", "http://example.org?token=secret", "http://example.org/../other"):
            with self.subTest(url=url), self.assertRaises(KomgaError):
                normalize_url(url)
        self.assertTrue(path_matches("file:///nas/series/a%20b.cbz", "series/a b.cbz"))
        self.assertFalse(path_matches("/nas/series/a%20b.cbz", "series/a b.cbz"))
        self.assertFalse(path_matches("/nas/Series/book.cbz", "series/book.cbz"))
        self.assertTrue(path_matches("C:\\NAS\\Series\\BOOK.cbz", "series/book.cbz"))


if __name__ == "__main__":
    unittest.main()
