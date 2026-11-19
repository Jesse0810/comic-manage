"""Metadata-only storefront search against minimized public-response fixtures."""
from __future__ import annotations

import json
import unittest
from threading import Event
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from comic_manage.scrape_http import ScrapeError
from comic_manage.search_stores import search_store
from comic_manage.sources import Cancelled


def card(identity="RJ300123", title="春の旅", author="森野", publisher="季節社", *, kind="マンガ", url=None):
    detail = url or f"/home/work/=/product_id/{identity}.html?tracking=abc"
    return f"""<li data-list_item_product_id="{identity}" class="search_result_img_box_inner">
        <dl class="work_img_main">
          <dd><div class="work_category"><a>{kind}</a></div></dd>
          <dd class="work_name"><a href="{detail}">{title}</a></dd>
          <dd class="maker_name">
            <a href="/home/circle/profile/=/maker_id/RG12345.html">{publisher}</a>
            <span class="author"><a href="/home/fsr/=/keyword_creater/name">{author}</a></span>
          </dd>
          <img src="https://images.example.invalid/ignored.jpg">
        </dl>
      </li>"""


def search_page(*cards, total=None):
    count = len(cards) if total is None else total
    listing = f"<ul id='search_result_img_box'>{''.join(cards)}</ul>" if cards else ""
    return f"""<!doctype html><title>検索結果 | DLsite</title>
       <div id="search_result_list" class="loading_display_open">{listing}</div>
       <div data-vue-component="search-result-tracking" data-search-result-count="{count}"></div>""".encode()


def dmm_item(identity="comic_123", *, site="FANZA", service="ebook", **values):
    suffix = ".co.jp" if site == "FANZA" else ".com"
    url = "https://www.dmm" + suffix + f"/dc/doujin/-/detail/=/cid={identity}/" if service == "doujin" else (
        "https://book.dmm" + suffix + f"/detail/{identity}/"
    )
    return {"title": "春の旅", "content_id": identity, "URL": url, "service_code": service, "category_name": "コミック",
            "date": "2024-02-29 00:00:00", "iteminfo": {"author": [{"name": "森野"}], "maker": [{"name": "季節社"}]},
            "imageURL": {"large": "https://images.example.invalid/ignored.jpg"}, **values}


def dmm_response(*items, total=None, status=200):
    return json.dumps({"result": {"status": status, "result_count": len(items),
                                 "total_count": len(items) if total is None else total, "items": list(items)}}).encode()


class StoreSearchTests(unittest.TestCase):
    credentials = {"apiKey": "test-api-id", "affiliateId": "test-site-990"}

    @patch("comic_manage.search_stores.request_source")
    def test_dlsite_public_cards_dedupe_and_keep_authors_distinct_from_circle(self, transport):
        first = search_page(card("RJ300123"), card("RJ300124", author="別人"), card("RJ300125", kind="アドベンチャー"))
        alternate = search_page(card("RJ300123", title="Spring Journey"))
        transport.side_effect = [first, alternate, search_page()]
        rows = search_store("dlsite", "春の旅", ["森野"], {})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"url": "https://www.dlsite.com/home/work/=/product_id/RJ300123.html",
                                   "title": "春の旅", "authors": ["森野"], "publisher": "季節社",
                                   "titles": ["Spring Journey"], "searchComplete": True})
        self.assertEqual(rows[1]["authors"], ["別人"])
        self.assertEqual(transport.call_count, 3)
        for args, kwargs in transport.call_args_list:
            url = urlsplit(args[0])
            self.assertEqual(url.netloc, "www.dlsite.com")
            self.assertIn(unquote(url.path).split("/")[1], {"home", "maniax", "girls"})
            self.assertTrue(unquote(url.path).endswith("/keyword/春の旅"))
            self.assertEqual(kwargs["accept"], "text/html")
            self.assertNotIn("headers", kwargs)

    @patch("comic_manage.search_stores.request_source")
    def test_dlsite_rejects_nonmatching_identity_and_untrusted_anchors(self, transport):
        transport.return_value = search_page(
            card("RJ300123", url="https://other.invalid/home/work/=/product_id/RJ300123.html"),
            card("RJ300124", url="https://www.dlsite.com/home/work/=/product_id/RJ666666.html"),
            card("RJ300125", url="https://www.dlsite.com/home/cart/=/product_id/RJ300125.html"),
        )
        self.assertEqual(search_store("dlsite", "春の旅", [], {}), [])

    @patch("comic_manage.search_stores.request_source")
    def test_dlsite_marks_unseen_pages_and_partial_access_incomplete(self, transport):
        transport.side_effect = [search_page(card(), total=99),
                                 ScrapeError("blocked", "SCRAPE_RESTRICTED"), search_page()]
        rows = search_store("dlsite", "春の旅", [], {})
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["searchComplete"])

    @patch("comic_manage.search_stores.request_source")
    def test_dlsite_block_or_malformed_page_is_not_an_empty_search(self, transport):
        for payload, code in [(b"<title>Verify you are human</title>", "SCRAPE_BLOCKED"),
                              (b"<title>Placeholder</title><a href='https://www.dlsite.com/'>ad</a>", "SCRAPE_RESPONSE")]:
            transport.return_value = payload
            with self.assertRaises(ScrapeError) as exc:
                search_store("dlsite", "春の旅", [], {})
            self.assertEqual(exc.exception.code, code)

    @patch("comic_manage.search_stores.request_source")
    def test_dmm_scoped_authorized_itemlist_search_ignores_untrusted_info(self, transport):
        transport.side_effect = [
            dmm_response(dmm_item("d_11111", service="doujin"), dmm_item("game_5", service="doujin", category_name="ゲーム")),
            dmm_response(dmm_item("fake", URL="https://outside.invalid/detail/fake/"),
                         dmm_item("fake_scope", site="DMM.com"),
                         dmm_item("fake_identity", URL="https://book.dmm.co.jp/detail/other/")),
            dmm_response(dmm_item("book_1", site="DMM.com")),
        ]
        rows = search_store("dmm", "春 & 夏", ["森野"], self.credentials)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(item["searchComplete"] for item in rows))
        self.assertTrue(all(item["authors"] == ["森野"] and item["year"] == "2024" for item in rows))
        self.assertNotIn("images", repr(rows))
        self.assertNotIn("test-api-id", repr(rows))
        self.assertNotIn("test-site-990", repr(rows))
        scopes = []
        for args, kwargs in transport.call_args_list:
            endpoint = urlsplit(args[0])
            self.assertEqual((endpoint.scheme, endpoint.netloc, endpoint.path),
                             ("https", "api.dmm.com", "/affiliate/v3/ItemList"))
            params = parse_qs(endpoint.query)
            self.assertEqual(params["api_id"], ["test-api-id"])
            self.assertEqual(params["affiliate_id"], ["test-site-990"])
            self.assertEqual(params["keyword"], ["春 & 夏"])
            scopes.append((params["site"], params["service"]))
            self.assertEqual(kwargs["accept"], "application/json")
        self.assertEqual(scopes, [(["FANZA"], ["doujin"]), (["FANZA"], ["ebook"]), (["DMM.com"], ["ebook"])])

    @patch("comic_manage.search_stores.request_source")
    def test_dmm_missing_credentials_are_rejected_without_network(self, transport):
        with self.assertRaises(ScrapeError) as exc:
            search_store("dmm", "春の旅", [], {"apiKey": "fake", "affiliateId": "ordinary-affiliate-001"})
        self.assertEqual(exc.exception.code, "SCRAPE_CREDENTIALS")
        transport.assert_not_called()

    @patch("comic_manage.search_stores.request_source")
    def test_dmm_rejects_api_error_and_stops_after_throttle(self, transport):
        transport.return_value = dmm_response(status=401)
        with self.assertRaises(ScrapeError) as exc:
            search_store("dmm", "春の旅", [], self.credentials)
        self.assertEqual(exc.exception.code, "SCRAPE_AUTH")
        transport.assert_called_once()
        transport.reset_mock()
        transport.side_effect = ScrapeError("no token in message", "SCRAPE_RATE_LIMIT")
        with self.assertRaises(ScrapeError) as exc:
            search_store("dmm", "春の旅", [], self.credentials)
        self.assertEqual(exc.exception.code, "SCRAPE_RATE_LIMIT")
        transport.assert_called_once()

    @patch("comic_manage.search_stores.request_source")
    def test_dmm_total_count_direction_never_marks_partial_search_complete(self, transport):
        transport.side_effect = [dmm_response(dmm_item("d_11111", service="doujin"), total=80),
                                 dmm_response(), dmm_response()]
        rows = search_store("dmm", "春の旅", [], self.credentials)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["searchComplete"])

    @patch("comic_manage.search_stores.request_source")
    def test_dmm_partial_page_with_no_supported_items_is_not_complete_empty(self, transport):
        unrelated = dmm_item("d_11111", service="doujin", category_name="音声", iteminfo={})
        transport.side_effect = [dmm_response(unrelated, total=80), dmm_response(), dmm_response()]
        with self.assertRaises(ScrapeError) as exc:
            search_store("dmm", "春の旅", [], self.credentials)
        self.assertEqual(exc.exception.code, "SCRAPE_RESPONSE")

        transport.side_effect = [dmm_response(unrelated), dmm_response(), dmm_response()]
        self.assertEqual(search_store("dmm", "春の旅", [], self.credentials), [])

    @patch("comic_manage.search_stores.request_source")
    def test_input_is_bounded_and_cancel_is_checked_before_network(self, transport):
        event = Event()
        event.set()
        with self.assertRaises(Cancelled):
            search_store("dlsite", "春の旅", [], {}, event)
        for title in ("", "a" * 241, "line\nother", "\ud834"):
            with self.assertRaises(ScrapeError):
                search_store("dlsite", title, [], {})
        transport.assert_not_called()
