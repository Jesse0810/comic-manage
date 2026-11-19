"""Tests for metadata-only discovery against synthetic provider payloads."""
from __future__ import annotations

import json
from threading import Event
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from comic_manage.scrape_http import ScrapeError, request_source
from comic_manage.search_galleries import search_gallery
from comic_manage.sources import Cancelled


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def eh_list(links: list[str], more: bool = False) -> bytes:
    html = '<html><body><input name="f_search" />'
    html += '<a href="/g/999/abcdef0123/">unrelated navigation</a>'
    if links:
        html += '<table class="itg gltc"><tr><td>'
        html += "".join(f'<a href="{href}"><div class="glink">Example</div></a>' for href in links)
        html += "</td></tr></table>"
    else:
        html += '<p style="text-align:center">No hits found</p>'
    html += '<a id="dnext" href="?f_search=book&amp;next=1">Next</a>' if more else '<a id="dnext">Next</a>'
    return (html + "</body></html>").encode("utf-8")


def eh_data(identity: int, token: str) -> dict:
    return {
        "gid": identity,
        "token": token,
        "title_jpn": "見本の旅行記",
        "title": "A Specimen Travel Diary",
        "tags": ["artist:Writer Name", "group:Example Press", "language:japanese"],
        "posted": "1780000000",
        "filecount": "31",
        "thumb": "https://images.invalid/ignored.png",
    }


def nh_item(identity: int, tags: list[int] | None = None) -> dict:
    return {
        "id": identity,
        "japanese_title": "見本の旅行記",
        "english_title": "A Specimen Travel Diary",
        "num_pages": 30,
        "tag_ids": tags if tags is not None else [2, 4, 11],
        "thumbnail": "https://images.invalid/ignored.png",
    }


def nh_response(*items: dict, pages: int | None = None) -> bytes:
    return encoded({"result": list(items), "num_pages": pages if pages is not None else int(bool(items))})


class GallerySearchTests(unittest.TestCase):
    def setUp(self):
        pacer = patch("comic_manage.search_galleries._pace")
        request = patch("comic_manage.search_galleries.request_source")
        self.addCleanup(pacer.stop)
        self.addCleanup(request.stop)
        self.mock_pace = pacer.start()
        self.request = request.start()

    def query(self, index: int, key: str) -> str:
        url = self.request.call_args_list[index].args[0]
        return parse_qs(urlsplit(url).query)[key][0]

    def test_eh_search_posts_only_same_site_gallery_links_for_verification(self):
        self.request.side_effect = [
            eh_list(["https://e-hentai.org/g/12/aabbccddee/", "/g/12/aabbccddee/",
                     "https://outside.invalid/g/77/aabbccddee/", "/s/abcdef/12-1"]),
            encoded({"gmetadata": [eh_data(12, "aabbccddee"), eh_data(999, "abcdef0123")]}),
        ]
        result = search_gallery("ehentai", "見本の旅行記", ["Writer Name", "Other Writer"], {})
        self.assertEqual(self.query(0, "f_search"), 'title:"見本の旅行記" artist:"Writer Name"')
        self.assertEqual(self.request.call_args_list[0].kwargs["accept"], "text/html")
        self.assertEqual(self.request.call_args_list[1].kwargs["body"]["gidlist"], [[12, "aabbccddee"]])
        self.assertEqual(self.request.call_count, 2)
        self.assertEqual(result, [{
            "url": "https://e-hentai.org/g/12/aabbccddee/",
            "title": "見本の旅行記",
            "titles": ["見本の旅行記", "A Specimen Travel Diary"],
            "authors": ["Writer Name"],
            "publisher": "Example Press",
            "language": "ja",
            "pageCount": 31,
            "searchComplete": True,
        }])

    def test_eh_title_fallback_and_pagination_are_explicit(self):
        self.request.side_effect = [
            eh_list([]),
            eh_list(["/g/12/aabbccddee/"], more=True),
            encoded({"gmetadata": [eh_data(12, "aabbccddee")]}),
        ]
        result = search_gallery("ehentai", "見本の旅行記", ["Different Credit"], {})
        self.assertEqual(self.query(1, "f_search"), 'title:"見本の旅行記"')
        self.assertFalse(result[0]["searchComplete"])
        self.assertEqual(self.mock_pace.call_count, 2)

    def test_eh_does_not_call_gdata_for_an_empty_result(self):
        self.request.return_value = eh_list([])
        self.assertEqual(search_gallery("ehentai", "見本の旅行記", [], {}), [])
        self.request.assert_called_once()

    def test_eh_rejects_unrecognized_page_and_mismatching_gallery_token(self):
        self.request.return_value = b"<title>Login required</title><p>No matches</p>"
        with self.assertRaisesRegex(ScrapeError, "可识别的搜索结果"):
            search_gallery("ehentai", "見本の旅行記", [], {})
        self.request.side_effect = [eh_list(["/g/12/aabbccddee/"]), encoded({"gmetadata": [eh_data(12, "1122334455")]})]
        with self.assertRaisesRegex(ScrapeError, "详情核对"):
            search_gallery("ehentai", "見本の旅行記", [], {})

    def test_eh_partial_detail_is_marked_uncertain(self):
        self.request.side_effect = [
            eh_list(["/g/12/aabbccddee/", "/g/14/1122334455/"]),
            encoded({"gmetadata": [eh_data(12, "aabbccddee"), {"gid": 14, "error": "unavailable"}]}),
        ]
        result = search_gallery("ehentai", "見本の旅行記", [], {})
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["searchComplete"])

    def test_nh_search_uses_public_api_and_verifies_author_tags(self):
        self.request.side_effect = [
            nh_response(nh_item(13)),
            encoded([{"id": 2, "type": "artist", "name": "Writer Name"},
                     {"id": 4, "type": "group", "name": "Example Press"},
                     {"id": 11, "type": "language", "name": "japanese"},
                     {"id": 99, "type": "artist", "name": "Unexpected Credit"}]),
        ]
        result = search_gallery("nhentai", "見本の旅行記", ["Writer Name"], {"apiKey": "example-test-key"})
        search, tags = self.request.call_args_list
        self.assertEqual(self.query(0, "query"), '"見本の旅行記" artist:"Writer Name"')
        self.assertEqual(self.query(1, "ids"), "2,4,11")
        self.assertEqual(search.kwargs["headers"], {"Authorization": "Key example-test-key"})
        self.assertNotIn("headers", tags.kwargs)
        self.assertEqual(result[0]["url"], "https://nhentai.net/g/13/")
        self.assertEqual(result[0]["authors"], ["Writer Name"])
        self.assertEqual(result[0]["publisher"], "Example Press")
        self.assertEqual(result[0]["language"], "ja")
        self.assertEqual(result[0]["pageCount"], 30)
        self.assertTrue(result[0]["searchComplete"])

    def test_nh_limits_lookups_and_drops_extra_results(self):
        self.request.side_effect = [
            nh_response(*[nh_item(number, list(range(1, 250))) for number in range(1, 18)], pages=3),
            encoded([{"id": 2, "type": "artist", "name": "Writer Name"}]),
            encoded([]),
        ]
        result = search_gallery("nhentai", "見本の旅行記", [], {})
        self.assertEqual(len(result), 12)
        self.assertTrue(all(not item["searchComplete"] for item in result))
        self.assertEqual(len(self.query(1, "ids").split(",")), 100)
        self.assertEqual(len(self.query(2, "ids").split(",")), 100)
        self.assertEqual(self.request.call_count, 3)

    def test_nh_honors_blacklisted_results_and_access_failures(self):
        self.request.return_value = nh_response({**nh_item(13), "blacklisted": True})
        self.assertEqual(search_gallery("nhentai", "見本の旅行記", [], {}), [])
        self.assertEqual(self.request.call_count, 1)
        self.request.reset_mock()
        self.request.side_effect = ScrapeError("temporarily limited", "SCRAPE_RATE_LIMIT")
        with self.assertRaises(ScrapeError) as error:
            search_gallery("nhentai", "見本の旅行記", ["Writer Name"], {})
        self.assertEqual(error.exception.code, "SCRAPE_RATE_LIMIT")
        self.request.assert_called_once()

    def test_query_and_credential_validation_reject_sensitive_or_malformed_input(self):
        for title in ("file:///C:/private/book", r"C:\private\book", "/home/user/private/book", "https://example.invalid/book"):
            with self.subTest(title=title), self.assertRaises(ScrapeError):
                search_gallery("ehentai", title, [], {})
        with self.assertRaises(ScrapeError):
            search_gallery("nhentai", "見本", [], {"apiKey": "malformed\r\ncredential"})
        self.request.assert_not_called()

    def test_query_syntax_does_not_accept_extra_metadata_operators(self):
        self.request.return_value = nh_response()
        self.assertEqual(search_gallery("nhentai", 'Title" -artist:Other', ['Author" language:en'], {}), [])
        query = self.query(0, "query")
        self.assertEqual(query.count('"'), 4)
        self.assertEqual(query.count(":"), 1)


    def test_title_fallback_keeps_incomplete_artist_search_uncertain(self):
        self.request.side_effect = [
            eh_list([], more=True),
            eh_list(["/g/12/aabbccddee/"]),
            encoded({"gmetadata": [eh_data(12, "aabbccddee")]}),
        ]
        result = search_gallery("ehentai", "見本の旅行記", ["Different Credit"], {})
        self.assertFalse(result[0]["searchComplete"])
        self.request.side_effect = [
            nh_response(pages=2),
            nh_response(nh_item(13)),
            encoded([{"id": ident, "type": "artist", "name": f"Author {ident}"} for ident in (2, 4, 11)]),
        ]
        result = search_gallery("nhentai", "見本の旅行記", ["Different Credit"], {})
        self.assertFalse(result[0]["searchComplete"])

    def test_empty_incomplete_search_is_reported_without_a_no_match_claim(self):
        for provider, replies in (
            ("ehentai", [eh_list([], more=True), eh_list([])]),
            ("nhentai", [nh_response(pages=2), nh_response()]),
            ("nhentai", [nh_response({**nh_item(13), "blacklisted": True}, pages=2)]),
        ):
            with self.subTest(provider=provider):
                self.request.side_effect = replies
                with self.assertRaises(ScrapeError) as error:
                    search_gallery(provider, "見本の旅行記", ["Different Credit"], {})
                self.assertEqual(error.exception.code, "SCRAPE_RESPONSE")


class GallerySearchTransportTests(unittest.TestCase):
    def test_transport_rejects_non_search_requests_to_public_site(self):
        for url, options in [
            ("https://e-hentai.org/g/12/aabbccddee/", {"accept": "text/html"}),
            ("https://e-hentai.org/?f_search=term", {}),
            ("https://e-hentai.org/?f_search=term&next=12", {"accept": "text/html"}),
            ("https://e-hentai.org/?f_search=term", {"accept": "text/html", "headers": {"Authorization": "secret"}}),
            ("http://e-hentai.org/?f_search=term", {"accept": "text/html"}),
        ]:
            with self.subTest(url=url), self.assertRaises(ScrapeError):
                request_source(url, **options)

    def test_cancelled_search_does_not_open_network(self):
        cancel = Event()
        cancel.set()
        with patch("comic_manage.search_galleries.request_source") as request, self.assertRaises(Cancelled):
            search_gallery("ehentai", "見本の旅行記", [], {}, cancel)
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
