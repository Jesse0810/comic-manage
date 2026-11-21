"""Duplicate/edition checks only use an authoritative live Komga library response."""
from __future__ import annotations

from contextlib import contextmanager
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from comic_manage.komga_client import KomgaClient, KomgaError
from comic_manage.komga_compare import MAX_DISPLAY_MATCHES, compare_uploads
from comic_manage.metadata import Metadata
from comic_manage.packaging import ExportPlan
from comic_manage.repository import Comic
from comic_manage.sources import Cancelled, Member, Snapshot

LIBRARY_ID = "target"
OTHER_ID = "other"
URL = "https://komga.example.test"
KEY = "synthetic-test-key"


def plan(identity="current", title="春の旅", pages=12, destination=None, **fields):
    metadata = Metadata(**{"title": title, "authors": ["森野"], **fields})
    comic = Comic(identity, "fake/local", "folder", "test", pages, metadata, status="approved")
    snapshot = Snapshot(Path("fake/local"), "folder", [Member(f"{index}.png", 10, "") for index in range(pages)])
    return ExportPlan(comic, Path(destination or f"output/_oneshots/{title} [{identity}].cbz"), snapshot, b"")


def book(identity="remote", title="春の旅", pages=12, series_id="series", *, author="森野",
         library=LIBRARY_ID, deleted=False, oneshot=True, number="", links=(), entities=(), tags=(), url="",
         volume=""):
    writers = [{"name": author, "role": "writer"}] if author else []
    metadata = {"title": title, "authors": writers, "number": number, "volume": volume,
                "links": [{"label": "Link", "url": value} for value in links], "tags": list(tags),
                "notableEntities": list(entities)}
    return {"id": identity, "libraryId": library, "seriesId": series_id, "metadata": metadata,
            "media": {"pagesCount": pages}, "oneshot": oneshot, "deleted": deleted, "url": url}


def series(identity="series", title="春の旅", *, language="", aliases=(), library=LIBRARY_ID):
    return {"id": identity, "libraryId": library, "metadata": {"title": title, "language": language,
            "alternateTitles": [{"title": alias, "label": "ja"} for alias in aliases]}}


class ComparisonTests(unittest.TestCase):
    def compare(self, plans, books, series_rows=None, libraries=None):
        if series_rows is None:
            series_rows = [series()]
        if libraries is None:
            libraries = [{"id": LIBRARY_ID, "name": "目标书库"}, {"id": OTHER_ID, "name": "另一个书库"}]
        with (patch.object(KomgaClient, "libraries", return_value=libraries) as libraries_call,
              patch.object(KomgaClient, "books", return_value=books) as books_call,
              patch.object(KomgaClient, "series", return_value=series_rows) as series_call):
            result = compare_uploads(plans, URL, KEY, LIBRARY_ID)
        libraries_call.assert_called_once_with()
        books_call.assert_called_once_with(LIBRARY_ID)
        if any(row.get("libraryId") == LIBRARY_ID and not row.get("deleted") for row in books):
            series_call.assert_called_once_with(LIBRARY_ID)
        else:
            series_call.assert_not_called()
        return result

    def test_canonical_file_still_matches_when_next_edition_uses_alternate_output(self):
        local = plan(destination="output/_oneshots/版名 [suffix] (版本 2).cbz")
        local.occupied_destination = Path("output/_oneshots/版名 [suffix].cbz")
        remote = book(title="服务器改过的独立标题", author="另一个作者", url="/library/_oneshots/版名 [suffix].cbz")
        result = self.compare([local], [remote], [series(title="不同系列")])["items"][0]
        self.assertNotEqual(result["status"], "new")
        self.assertEqual(result["matches"][0]["bookId"], "remote")

    def test_scopes_live_books_and_allows_new_without_local_history(self):
        result = self.compare([plan(), plan("fresh", "夏の原野")],
                              [book(), book("other", "夏の原野", library=OTHER_ID),
                               book("removed", "夏の原野", deleted=True)])
        first, second = result["items"]
        self.assertEqual((result["libraryId"], result["libraryName"]), (LIBRARY_ID, "目标书库"))
        self.assertEqual(first["status"], "existing")
        self.assertEqual(first["matches"][0]["bookId"], "remote")
        self.assertEqual(second["status"], "new")
        self.assertEqual(second["matches"], [])

    def test_book_group_can_match_different_titles_and_show_distinct_editions(self):
        local = plan(text_version="有文字", work_group="春の旅（森野）", language="zh-CN")
        remote = book("textless", "无字连载整理版", pages=11, entities=[
            {"kind": "作品组", "name": "春の旅(森野)"}, {"kind": "文字版本", "name": "无文字"}])
        item = self.compare([local], [remote], [series(language="zh-Hant")])["items"][0]
        self.assertEqual(item["status"], "possible_version")
        match = item["matches"][0]
        self.assertEqual((match["pageCount"], match["textVersion"], match["language"]), (11, "无文字", "zh-Hant"))
        self.assertTrue({"作品组一致", "文字版本不同", "语言不同", "页数不同"} <= set(match["reasons"]))

    def test_oneshot_aliases_are_book_evidence_but_series_aliases_need_matching_number(self):
        target = plan(title="春天的旅程")
        same = book("oneshot", title="The Spring Trip")
        match = self.compare([target], [same], [series(aliases=["春天的旅程"])])["items"][0]
        self.assertEqual(match["status"], "existing")
        first = plan(title="春日旅途", series="四季旅行", number="02.0", collection_mode="series")
        second_book = book("second", title="重译后的标题", oneshot=False, number="2")
        match = self.compare([first], [second_book], [series("series", "四季の旅", aliases=["四季旅行"])])["items"][0]
        self.assertEqual(match["status"], "existing")
        self.assertEqual(match["matches"][0]["series"], "四季の旅")
        self.assertIn("系列与册号一致", match["matches"][0]["reasons"])

    def test_distinct_numbered_volumes_of_same_series_are_not_duplicate_editions(self):
        target = plan(title="四季旅行", series="四季旅行", number="1", collection_mode="series",
                      web=["https://book.example.test/series/1"])
        volume_two = book("second", title="四季旅行", oneshot=False, number="2",
                          links=["https://book.example.test/series/1"], tags=["作品组:四季旅行"])
        result = self.compare([target], [volume_two], [series("series", "四季旅行")])
        self.assertEqual(result["items"][0]["status"], "new")

    def test_exact_link_can_indicate_possible_other_edition_without_same_title(self):
        target = plan(web=["http://www.dlsite.com/home/work/=/product_id/RJ123456.html/#summary"])
        source = book(title="A Different Localized Title", links=["https://dlsite.com/home/work/=/product_id/RJ123456.html"])
        item = self.compare([target], [source], [series(title="另一标题")])["items"][0]
        self.assertEqual(item["status"], "possible_version")
        self.assertIn("作品来源链接一致", item["matches"][0]["reasons"])

    def test_url_queries_that_identify_different_works_are_not_merged(self):
        target = plan(web=["https://example.test/book/view?cid=one"])
        source = book(title="另一本", links=["https://example.test/book/view?cid=two"])
        result = self.compare([target], [source], [series(title="别名")])
        self.assertEqual(result["items"][0]["status"], "new")

    def test_same_title_with_known_different_author_is_not_a_match_without_other_evidence(self):
        item = self.compare([plan()], [book(author="夏田")])["items"][0]
        self.assertEqual(item["status"], "new")

    def test_same_title_and_missing_author_or_page_is_only_possible(self):
        item = self.compare([plan()], [book(author="", pages=0)])["items"][0]
        self.assertEqual(item["status"], "possible_version")
        self.assertEqual(item["matches"][0]["pageCount"], 0)
        self.assertIn("资料不足以确认是同一版本", item["matches"][0]["reasons"])

    def test_explicit_filename_variant_suffix_is_used_without_missing_other_editions(self):
        local = plan(title="春の旅 [有文字]")
        remote = book(title="春の旅 [無文字]")
        item = self.compare([local], [remote])["items"][0]
        self.assertEqual(item["status"], "possible_version")
        self.assertEqual(item["matches"][0]["textVersion"], "无文字")

    def test_same_planned_relative_destination_when_metadata_changed_is_still_detected(self):
        target = plan(destination="output/_oneshots/new-content [abc].cbz")
        match = book(title="Title changed", author="", url="file:///E:/Komga/_oneshots/new-content%20%5Babc%5D.cbz")
        changed = self.compare([target], [match], [series(title="Independent")])["items"][0]
        self.assertIn("成品相对路径一致", changed["matches"][0]["reasons"])
        self.assertNotIn("url", changed["matches"][0])

    def test_visible_list_is_bounded_but_fingerprint_covers_all_matches(self):
        remote = [book(str(n).zfill(2)) for n in range(MAX_DISPLAY_MATCHES + 2)]
        original = self.compare([plan()], remote)["items"][0]
        shuffled = self.compare([plan()], remote[::-1])["items"][0]
        self.assertEqual(original, shuffled)
        self.assertEqual(original["matchCount"], MAX_DISPLAY_MATCHES + 2)
        self.assertEqual(len(original["matches"]), MAX_DISPLAY_MATCHES)
        remote[-1]["media"]["pagesCount"] = 13
        updated = self.compare([plan()], remote)["items"][0]
        self.assertEqual(original["matches"], updated["matches"])
        self.assertNotEqual(original["matchFingerprint"], updated["matchFingerprint"])

    def test_mismatched_parent_library_and_unknown_library_stop_confirmation(self):
        with self.assertRaisesRegex(KomgaError, "系列已变化"):
            self.compare([plan()], [book()], [series(library=OTHER_ID)])
        with self.assertRaisesRegex(KomgaError, "书库不存在"):
            self.compare([plan()], [], libraries=[{"id": OTHER_ID, "name": "另一库"}])

    def test_cancel_before_catalog_reads(self):
        cancelled = Event()
        cancelled.set()
        with self.assertRaises(Cancelled):
            compare_uploads([plan()], URL, KEY, LIBRARY_ID, cancelled)


@contextmanager
def fixture(*, bad_paging=False, service_error=False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def reply(self, payload, status=200):
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            requests.append(("GET", self.path, None, self.headers["X-API-Key"]))
            if service_error:
                self.reply({"internal": "never wind up in a UI error"}, status=503)
            else:
                self.reply([{"id": LIBRARY_ID, "name": "测试库"}])

        def do_POST(self):
            params = urlsplit(self.path)
            number = int(parse_qs(params.query)["page"][0])
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(("POST", params.path, payload, self.headers["X-API-Key"]))
            if params.path == "/api/v1/books/list":
                content = [book("first", "春の旅")] if number == 0 else [book("second", "夏の旅")]
                self.reply({"content": content, "number": number, "last": number == 1,
                            "totalElements": 3 if bad_paging else 2})
            elif params.path == "/api/v1/series/list":
                self.reply({"content": [series(aliases=["Spring Trip"])], "number": number, "last": True})
            else:
                self.reply({}, status=404)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            worker.join()


class LiveApiTests(unittest.TestCase):
    def test_catalog_requests_are_authenticated_read_only_paginated_in_the_selected_library(self):
        with fixture() as (url, requests):
            result = compare_uploads([plan()], url, KEY, LIBRARY_ID)
        self.assertEqual(result["items"][0]["status"], "existing")
        self.assertEqual(requests[0], ("GET", "/api/v1/libraries", None, KEY))
        self.assertEqual([path for _, path, _, _ in requests[1:]],
                         ["/api/v1/books/list", "/api/v1/books/list", "/api/v1/series/list"])
        for method, _, payload, key in requests[1:]:
            self.assertEqual((method, key), ("POST", KEY))
            self.assertEqual(payload["condition"], {"libraryId": {"operator": "is", "value": LIBRARY_ID}})

    def test_incomplete_page_or_unavailable_server_is_an_error_not_new(self):
        with fixture(bad_paging=True) as (url, _):
            with self.assertRaisesRegex(KomgaError, "分页结果不完整"):
                compare_uploads([plan()], url, KEY, LIBRARY_ID)
        with fixture(service_error=True) as (url, _):
            with self.assertRaises(KomgaError) as error:
                compare_uploads([plan()], url, KEY, LIBRARY_ID)
            self.assertEqual(error.exception.code, "KOMGA_HTTP")
            self.assertNotIn("never wind up", str(error.exception))


if __name__ == "__main__":
    unittest.main()
