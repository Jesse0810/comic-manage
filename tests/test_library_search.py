from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from comic_manage.library_search import _SEARCH
from comic_manage.metadata import Metadata
from comic_manage.scrape_http import ScrapeError
from comic_manage.service import ComicService, ServiceError
from comic_manage.sources import inspect_source


class LibrarySearchTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.service = ComicService(self.root / "catalog")
        self.one = self.add_book("当前标题", "春天漫步", ["白石"])
        self.two = self.add_book("第二个标题", "冬日笔记", ["北川"])

    def add_book(self, title, original, authors):
        folder = self.root / title
        folder.mkdir()
        (folder / "001.png").write_bytes(b"synthetic")
        identity = self.service.repo.upsert_scan(inspect_source(folder))
        metadata = Metadata(title=title, original_title=original, authors=authors, tags=["原有标签"])
        self.service.call("save", {"id": identity, "metadata": metadata.to_dict()})
        return identity

    def candidate(self, product="RJ120000", title="春天漫步", authors=None, **data):
        return {"title": title, "authors": authors if authors is not None else ["白石"],
                "url": f"https://www.dlsite.com/home/work/=/product_id/{product}.html", **data}

    def search(self, results, ids=None, **params):
        with patch.dict(_SEARCH, {"dlsite": lambda *a, **k: results}):
            return self.service.call("agent_search", {"ids": ids or [self.one], "providers": ["dlsite"], **params})

    def chosen(self, result, book=None, index=0):
        entry = book or result["books"][0]
        return {"id": entry["id"], "candidateId": entry["candidates"][index]["id"]}

    def detail(self, candidate, **fields):
        return {"provider": "dlsite", "sourceUrl": candidate["url"],
                "fields": {"title": candidate["title"], "authors": candidate["authors"], **fields}, "warnings": []}

    def test_original_title_and_exact_author_produce_a_single_preselected_match(self):
        queries = []
        hit = self.candidate(year="2022", pageCount=1)

        def provider(site, title, authors, credentials, cancel):
            queries.append((site, title, authors))
            return [hit, hit]

        with patch.dict(_SEARCH, {"dlsite": provider}):
            found = self.service.call("agent_search", {"ids": [self.one], "providers": ["dlsite"]})
        self.assertEqual(queries, [("dlsite", "春天漫步", ["白石"])])
        book = found["books"][0]
        self.assertEqual(book["status"], "matched")
        self.assertEqual(book["recommendedId"], book["candidates"][0]["id"])
        self.assertEqual(set(book["candidates"][0]["reasons"]), {"原名一致", "作者一致"})
        self.assertEqual(self.service.repo.get(self.one).metadata.title, "当前标题")

    def test_multiple_candidates_wrong_authors_variant_and_partial_results_require_choice(self):
        scenarios = [
            [self.candidate(), self.candidate("RJ120001")],
            [self.candidate(authors=["别人"])],
            [self.candidate(authors=[])],
            [self.candidate(pageCount=2)],
            [self.candidate(searchComplete=False)],
        ]
        for hits in scenarios:
            with self.subTest(hits=hits):
                book = self.search(hits)["books"][0]
                self.assertEqual(book["status"], "ambiguous")
                self.assertNotIn("recommendedId", book)
                self.assertEqual(len(book["candidates"]), len(hits))

    def test_unrelated_partial_results_still_prevent_preselection(self):
        unrelated = self.candidate(title="不相关作品", authors=["别人"], searchComplete=False)
        exact = self.candidate(url="https://nhentai.net/g/1234/")
        with patch.dict(_SEARCH, {"dlsite": lambda *a, **k: [unrelated], "nhentai": lambda *a, **k: [exact]}):
            book = self.service.call("agent_search", {
                "ids": [self.one], "providers": ["dlsite", "nhentai"],
            })["books"][0]
        self.assertEqual(book["status"], "ambiguous")
        self.assertNotIn("recommendedId", book)
        self.assertEqual(len(book["candidates"]), 1)
        self.assertTrue(any("部分结果" in notice for notice in book["warnings"]))

    def test_shared_alias_does_not_confirm_two_credits(self):
        comic = self.add_book("合著", "春天漫步", ["白井 (白石)", "山口 (白石)"])
        book = self.search([self.candidate()], [comic])["books"][0]
        self.assertEqual(book["status"], "ambiguous")
        self.assertNotIn("recommendedId", book)

    def test_not_matched_and_request_failures_are_distinct(self):
        unrelated = self.candidate(title="不相关作品", authors=["别人"])
        self.assertEqual(self.search([unrelated])["books"][0]["status"], "unmatched")

        def limited(*_args, **_kwargs):
            raise ScrapeError("raw-body-contained-secret", "SCRAPE_RATE_LIMIT")

        with patch.dict(_SEARCH, {"dlsite": limited}):
            found = self.service.call("agent_search", {"ids": [self.one, self.two], "providers": ["dlsite"]})
        self.assertEqual([row["status"] for row in found["books"]], ["error", "error"])
        for row in found["books"]:
            self.assertIn("请求频率", row["warnings"][0])
        self.assertNotIn("raw-body-contained-secret", str(found))

    def test_rejects_unsupported_links_and_cannot_choose_another_works_candidate(self):
        bad_link = self.candidate(url="https://third-party.test/product")
        wrong_path = self.candidate(url="https://www.dlsite.com/search")
        self.assertFalse(self.search([bad_link, wrong_path])["books"][0]["candidates"])
        found = self.search([self.candidate(), self.candidate("RJ120001", "冬日笔记", ["北川"])], [self.one, self.two])
        first = self.chosen(found)
        chosen_for_second = {**first, "id": self.two}
        with patch("comic_manage.library_search.scrape_preview") as reads:
            with self.assertRaisesRegex(ServiceError, "只能选用"):
                self.service.call("agent_search_preview", {"token": found["token"], "choices": [chosen_for_second]})
            reads.assert_not_called()

    def test_preview_and_partial_apply_append_verified_source_only_when_selected(self):
        candidate = self.candidate()
        found = self.search([candidate])
        with patch("comic_manage.library_search.scrape_preview", return_value=self.detail(
                candidate, publisher="测试发行方", tags=["新增标签"], year="2024", month="11", day="12")) as read:
            preview = self.service.call("agent_search_preview", {"token": found["token"], "choices": [self.chosen(found)]})
        read.assert_called_once()
        self.assertEqual(read.call_args.args[0], candidate["url"])
        self.assertEqual(self.service.repo.get(self.one).metadata.web, [])
        self.assertTrue(preview["token"])
        rows = {field["field"]: field for field in preview["books"][0]["fields"]}
        self.assertEqual(rows["tags"]["after"], ["原有标签", "新增标签"])
        self.assertEqual(rows["release_date"]["after"], {"year": "2024", "month": "11", "day": "12"})
        applied = self.service.call("agent_apply", {"token": preview["token"], "books": [
            {"id": self.one, "fields": ["publisher", "web"]}
        ]})
        self.assertEqual(applied["updated"], 1)
        comic = self.service.repo.get(self.one)
        self.assertEqual(comic.metadata.publisher, "测试发行方")
        self.assertEqual(comic.metadata.web, [candidate["url"]])
        self.assertEqual(comic.metadata.tags, ["原有标签"])
        self.assertEqual(comic.status, "draft")

    def test_changed_metadata_does_not_dispatch_a_stale_candidate(self):
        found = self.search([self.candidate()])
        self.service.call("save", {"id": self.one, "metadata": {"publisher": "手动更正"}})
        with patch("comic_manage.library_search.scrape_preview") as read:
            preview = self.service.call("agent_search_preview", {"token": found["token"], "choices": [self.chosen(found)]})
        read.assert_not_called()
        self.assertIsNone(preview["token"])
        self.assertIn("资料已变化", preview["books"][0]["warnings"][0])

    def test_detail_rejects_different_product_identity_and_ticket_has_no_credentials(self):
        candidate = self.candidate()
        found = self.search([candidate], credentials={"nhentai":{"apiKey":"synthetic-only-key"}})
        self.assertNotIn("synthetic-only-key", repr(self.service.library_search.tickets))
        with patch("comic_manage.library_search.scrape_preview", return_value=self.detail(self.candidate("RJ120777"))):
            preview = self.service.call("agent_search_preview", {"token": found["token"], "choices": [self.chosen(found)]})
        self.assertIsNone(preview["token"])
        self.assertTrue(preview["books"][0]["warnings"])

    def test_cancelled_search_never_issues_a_ticket(self):
        cancel = Event()

        def abort(*_args, **_kwargs):
            cancel.set()
            return [self.candidate()]
        with patch.dict(_SEARCH, {"dlsite": abort}):
            result = self.service.call("agent_search", {"ids": [self.one], "providers": ["dlsite"]}, cancel)
        self.assertTrue(result["cancelled"])
        self.assertIsNone(result["token"])

    def test_dmm_requirements_and_ticket_scope(self):
        with self.assertRaisesRegex(ServiceError, "Affiliate"):
            self.service.call("agent_search", {"ids": [self.one], "providers": ["dmm"]})
        found = self.search([self.candidate()])
        with self.assertRaisesRegex(ServiceError, "书库"):
            self.service.call("agent_search_preview", {"token": found["token"], "choices": [self.chosen(found)],
                                                        "komga_scope": "a" * 20})
