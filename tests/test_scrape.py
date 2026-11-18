import io
import json
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from comic_manage.scrape import ScrapeError, scrape_preview, work_link
from comic_manage.scrape_http import request_source
from comic_manage.service import ComicService, ServiceError
from comic_manage.sources import Cancelled


GALLERY_URL = "https://e-hentai.org/g/123/0123456789/"
STORE_URL = "https://book.dmm.co.jp/product/example/"


class _Reply(io.BytesIO):
    def __init__(self, payload, content_type="application/json"):
        super().__init__(payload)
        self.headers = Message()
        self.headers["Content-Type"] = content_type


class ScrapeTests(unittest.TestCase):
    def test_unsupported_urls_are_rejected_before_network(self):
        with patch("comic_manage.scrape.request_source") as request:
            for url in (
                "https://e-hentai.org.example.test/g/1/0123456789/",
                "https://e-hentai.org@127.0.0.1/a",
                "https://e-hentai.org:443/g/1/0123456789/",
                "https://Ｅ-hentai.org/g/1/0123456789/",
                "https://e-hentai.org\\@example.test/a",
                "file:///C:/catalog.sqlite3",
            ):
                with self.subTest(url=url), self.assertRaises(ScrapeError):
                    scrape_preview(url)
            request.assert_not_called()
        self.assertEqual(work_link("http://e-hentai.org/g/1/0123456789/")[1].scheme, "https")

    def test_credentials_are_bound_to_provider(self):
        with patch("comic_manage.scrape.fetch_gallery") as gallery, patch("comic_manage.scrape.fetch_storefront") as store:
            for url, kwargs in [
                (GALLERY_URL, {"api_key": "dummy"}),
                ("https://nhentai.net/g/123/", {"affiliate_id": "dummy"}),
                (STORE_URL, {}),
                (STORE_URL, {"api_key": "dummy", "affiliate_id": "fake\r\nInjected: header"}),
                ("https://www.dlsite.com/home/work/=/product_id/RJ123456.html", {"api_key": "dummy"}),
            ]:
                with self.subTest(url=url), self.assertRaises(ScrapeError):
                    scrape_preview(url, **kwargs)
            gallery.assert_not_called()
            store.assert_not_called()

    def test_preview_sanitizes_candidates_and_does_not_write_catalog(self):
        fake = {
            "provider": "ehentai", "sourceUrl": GALLERY_URL,
            "fields": {
                "title": " 晨光\u0000手记 ", "authors": ["小山", " 小山 ", 7], "genre": "同人志",
                "tags": ["风景", "日常", "作品组：误归并", "发布平台:假平台", "原作:误识别", "生肉", "name,split"],
                "year": "2024", "month": "02", "day": "29", "cover_index": 99,
                "series": "不应出现", "original_works": ["甲", "乙"], "web": ["https://example.test/"],
            },
            "warnings": ["确认作者后再应用。"],
        }
        with TemporaryDirectory() as folder:
            service = ComicService(Path(folder))
            with patch("comic_manage.scrape.fetch_gallery", return_value=fake):
                result = service.call("scrape_preview", {"url": GALLERY_URL})
            self.assertEqual(result["fields"], {
                "title": "晨光手记", "authors": ["小山"], "tags": ["风景", "日常"],
                "year": "2024", "month": "2", "day": "29",
            })
            self.assertEqual(service.repo.all(), [])
            with self.assertRaises(ServiceError):
                service.call("scrape_preview", {"url": GALLERY_URL, "commit": True})

    def test_response_from_other_provider_is_refused(self):
        with patch("comic_manage.scrape.fetch_gallery", return_value={
            "sourceUrl": "https://nhentai.net/g/123/", "fields": {"title": "例"}, "warnings": [],
        }):
            with self.assertRaisesRegex(ScrapeError, "不属于当前站点"):
                scrape_preview(GALLERY_URL)

    def test_accepts_bounded_metadata_and_refuses_media_or_excess_data(self):
        with patch("comic_manage.scrape_http.build_opener") as factory:
            opener = factory.return_value
            opener.open.return_value = _Reply(b'{"title":"sample"}')
            self.assertEqual(json.loads(request_source("https://nhentai.net/api/v2/galleries/123")), {"title": "sample"})
            req = opener.open.call_args.args[0]
            self.assertEqual(req.get_method(), "GET")
            self.assertIn("ComicManage/", req.get_header("User-agent"))
            opener.open.return_value = _Reply(b"not metadata", "image/jpeg")
            with self.assertRaises(ScrapeError):
                request_source("https://nhentai.net/api/v2/galleries/123")
            opener.open.return_value = _Reply(b"x" * (2 * 1024 * 1024 + 1))
            with self.assertRaisesRegex(ScrapeError, "过大"):
                request_source("https://nhentai.net/api/v2/galleries/123")

    def test_redirects_and_rate_limits_return_safe_errors(self):
        url = "https://api.dmm.com/affiliate/v3/ItemList?api_id=private-test-value"
        for status, code in [(302, "SCRAPE_RESTRICTED"), (429, "SCRAPE_RATE_LIMIT"), (401, "SCRAPE_AUTH")]:
            with self.subTest(status=status), patch("comic_manage.scrape_http.build_opener") as factory:
                factory.return_value.open.side_effect = HTTPError(url, status, "remote body", {}, None)
                with self.assertRaises(ScrapeError) as error:
                    request_source(url)
                self.assertEqual(error.exception.code, code)
                self.assertNotIn("private-test-value", str(error.exception))
                self.assertIsNone(factory.call_args.args[0].redirect_request(None, None, 302, None, None, None))

    def test_cancelled_request_does_not_connect(self):
        cancel = Event()
        cancel.set()
        with patch("comic_manage.scrape_http.build_opener") as factory, self.assertRaises(Cancelled):
            request_source("https://nhentai.net/api/v2/galleries/123", cancel=cancel)
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
