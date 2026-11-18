"""Synthetic text fixtures only; never contacts live storefronts or images."""
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from comic_manage.scrape_http import ScrapeError
from comic_manage.scrape_storefronts import fetch_storefront
from comic_manage.sources import Cancelled


DMM = "https://book.dmm.co.jp/detail/testcomic001/"
DL = "https://www.dlsite.com/home/work/=/product_id/RJ123456.html"
CREDENTIALS = {"apiKey": "synthetic-key", "affiliateId": "synthetic-990"}


def dmm_item(**changes):
    item = {"content_id": "testcomic001", "product_id": "different-product-number", "title": "合成図書", "URL": DMM,
            "service_code": "ebook", "floor_code": "comic", "category_name": "コミック", "date": "2024-02-29 00:00:00",
            "iteminfo": {"author": [{"id": "1", "name": "著者甲"}, {"id": "2", "name": "著者乙"}],
                         "maker": [{"id": "3", "name": "出版社甲"}], "genre": [{"id": "4", "name": "日常"}]},
            "comment": "Source descriptions must not be copied", "imageURL": {"large": "https://images.example.invalid/never-fetch.jpg"}}
    item.update(changes)
    return item


def dmm_json(items=None, status=200):
    return json.dumps({"result": {"status": status, "items": items if items is not None else [dmm_item()]}}, ensure_ascii=False).encode()


def dl_html(extra="", *, title="合成マンガ", form="マンガ", canonical=DL):
    return f"""<!doctype html><html><head><title>合成作品 | DLsite</title><link rel="canonical" href="{canonical}"></head>
    <body><h1 id="work_name">{title}</h1><table id="work_outline">
    <tr><th>作者</th><td><a>著者甲</a><a>著者乙</a></td></tr>
    <tr><th>出版社</th><td>出版社甲</td></tr>
    <tr><th>販売日</th><td><a>2024年2月29日</a></td></tr>
    <tr><th>作品形式</th><td><a><span>{form}</span></a><span>PDF</span></td></tr>
    <tr><th>ジャンル</th><td><a>日常</a><a>旅行</a></td></tr></table>
    <img src="https://images.example.invalid/never-fetch.jpg"><div>Descriptions are deliberately not returned</div>{extra}</body></html>""".encode()


class Request:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class StorefrontTests(unittest.TestCase):
    def dmm(self, data=None, url=DMM, credentials=None):
        request = Request(data if data is not None else dmm_json())
        result = fetch_storefront("dmm", urlsplit(url), CREDENTIALS if credentials is None else credentials, request)
        return result, request

    def dl(self, data=None, url=DL):
        request = Request(dl_html() if data is None else data)
        result = fetch_storefront("dlsite", urlsplit(url), {"apiKey": "must-not-be-sent"}, request)
        return result, request

    def test_dmm_exact_ebook_identity_and_explicit_text_fields_only(self):
        result, request = self.dmm()
        self.assertEqual(result["fields"], {"title": "合成図書", "authors": ["著者甲", "著者乙"], "publisher": "出版社甲",
                                           "tags": ["日常"], "genre": "漫画", "year": "2024", "month": "2", "day": "29"})
        self.assertEqual(result["sourceUrl"], DMM)
        self.assertEqual(len(request.calls), 1)
        url, options = request.calls[0]
        self.assertEqual((urlsplit(url).hostname, urlsplit(url).path), ("api.dmm.com", "/affiliate/v3/ItemList"))
        self.assertEqual(parse_qs(urlsplit(url).query), {"api_id": ["synthetic-key"], "affiliate_id": ["synthetic-990"],
                                                     "site": ["FANZA"], "cid": ["testcomic001"], "hits": ["1"], "output": ["json"]})
        self.assertEqual(options, {"accept": "application/json"})
        self.assertNotIn("summary", result["fields"])
        self.assertNotIn("image", str(result))

    def test_dmm_doujin_must_have_explicit_supported_text_category(self):
        url = "https://www.dmm.co.jp/dc/doujin/-/detail/=/cid=testcomic001/"
        item = dmm_item(URL=url, service_code="doujin")
        result, _ = self.dmm(dmm_json([item]), url)
        self.assertEqual(result["fields"]["release_platforms"], ["FANZA同人"])
        for category in ("音声", "ゲーム", ""):
            with self.subTest(category=category), self.assertRaises(ScrapeError):
                self.dmm(dmm_json([dmm_item(URL=url, service_code="doujin", category_name=category)]), url)

    def test_dmm_general_books_site_is_not_fanza(self):
        link = "https://book.dmm.com/detail/testcomic001/"
        result, request = self.dmm(dmm_json([dmm_item(URL=link)]), link)
        self.assertEqual(result["sourceUrl"], link)
        self.assertEqual(parse_qs(urlsplit(request.calls[0][0]).query)["site"], ["DMM.com"])

    def test_dmm_rejects_video_search_other_hosts_and_credentials_in_url(self):
        bad = ["https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=testcomic001/", "https://book.dmm.co.jp/list/?cid=testcomic001",
               "https://book.dmm.co.jp.evil.invalid/detail/testcomic001/", "https://user:secret@book.dmm.co.jp/detail/testcomic001/",
               "https://book.dmm.co.jp:444/detail/testcomic001/", "http://book.dmm.co.jp/detail/testcomic001/"]
        for url in bad:
            request = Request(dmm_json())
            with self.subTest(url=url), self.assertRaises(ScrapeError):
                fetch_storefront("dmm", urlsplit(url), CREDENTIALS, request)
            self.assertFalse(request.calls)

    def test_dmm_unique_cid_is_required_instead_of_title_search(self):
        for url in [DMM + "?cid=another", "https://book.dmm.co.jp/detail/?cid=one&cid=two", "https://book.dmm.co.jp/detail/no-id!/"]:
            with self.subTest(url=url), self.assertRaises(ScrapeError):
                self.dmm(url=url)

    def test_dmm_bad_credentials_never_make_requests(self):
        for credentials in ({}, {"apiKey": "x", "affiliateId": "x-001"}, {"apiKey": "x\nsecret", "affiliateId": "x-999"}):
            request = Request(dmm_json())
            with self.subTest(credentials=credentials), self.assertRaises(ScrapeError):
                fetch_storefront("dmm", urlsplit(DMM), credentials, request)
            self.assertFalse(request.calls)

    def test_dmm_missing_multiple_and_wrong_identity_never_choose_first(self):
        for items in ([], [dmm_item(), dmm_item()], [dmm_item(content_id="other")]):
            with self.subTest(items=len(items)), self.assertRaises(ScrapeError):
                self.dmm(dmm_json(items))

    def test_dmm_response_link_must_match_supported_host_site_and_cid(self):
        links = ["https://evil.invalid/detail/testcomic001/", "https://book.dmm.co.jp/detail/other/",
                 "https://book.dmm.com/detail/testcomic001/", "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=testcomic001/"]
        for link in links:
            with self.subTest(link=link), self.assertRaises(ScrapeError):
                self.dmm(dmm_json([dmm_item(URL=link)]))

    def test_dmm_rejects_other_services_and_ignores_invalid_calendar_dates(self):
        with self.assertRaises(ScrapeError):
            self.dmm(dmm_json([dmm_item(service_code="digital")]))
        result, _ = self.dmm(dmm_json([dmm_item(date="2023-02-29 00:00:00")]))
        self.assertNotIn("year", result["fields"])

    def test_dmm_error_payload_and_transport_never_expose_credentials_or_raw_body(self):
        secret = "synthetic-key-private-raw"
        payload = json.dumps({"result": {"status": 401, "errors": {"message": secret}}}).encode()
        for source in [payload, b"not json " + secret.encode(), OSError(secret), ScrapeError(secret, "SCRAPE_NETWORK")]:
            with self.subTest(source=type(source).__name__), self.assertRaises(ScrapeError) as captured:
                self.dmm(source)
            self.assertNotIn(secret, str(captured.exception))

    def test_cancellation_propagates_and_rate_limit_code_is_preserved_without_raw_message(self):
        with self.assertRaises(Cancelled):
            self.dmm(Cancelled("cancelled"))
        with self.assertRaises(ScrapeError) as captured:
            self.dmm(ScrapeError("private-url", "SCRAPE_RATE_LIMIT"))
        self.assertEqual(captured.exception.code, "SCRAPE_RATE_LIMIT")
        self.assertNotIn("private-url", str(captured.exception))

    def test_dlsite_explicit_rows_do_not_scrape_body_description_or_images(self):
        result, request = self.dl()
        self.assertEqual(result["fields"], {"title": "合成マンガ", "authors": ["著者甲", "著者乙"], "publisher": "出版社甲",
                                           "genre": "漫画", "tags": ["日常", "旅行"], "year": "2024", "month": "2", "day": "29",
                                           "release_platforms": ["DLsite"]})
        self.assertEqual(request.calls, [(DL, {"accept": "text/html"})])
        self.assertNotIn("summary", result["fields"])
        self.assertNotIn("must-not-be-sent", str(request.calls))

    def test_dlsite_matching_schema_book_is_accepted_without_guessing_fields(self):
        schema = {"@context": "https://schema.org", "@type": "Book", "url": DL, "name": "合成書籍",
                  "author": [{"@type": "Person", "name": "著者甲"}], "datePublished": "2024-03-01", "keywords": "旅行,風景",
                  "description": "Must not be returned", "image": "https://images.example.invalid/never-fetch.jpg"}
        html = ('<html><head><title>合成書籍 | DLsite</title></head><body><script type="application/ld+json">'
                + json.dumps(schema) + '</script></body></html>').encode()
        result, _ = self.dl(html)
        self.assertEqual(result["fields"]["title"], "合成書籍")
        self.assertEqual(result["fields"]["authors"], ["著者甲"])
        self.assertEqual(result["fields"]["tags"], ["旅行", "風景"])
        self.assertNotIn("genre", result["fields"])
        self.assertNotIn("summary", result["fields"])

    def test_dlsite_current_product_definition_list_schema_identifies_illustration_book(self):
        # Synthetic equivalent of the public all-ages work's current markup;
        # no copied source HTML or source images belong in this fixture.
        schema = {"@context": "https://schema.org", "@type": "Product", "sku": "RJ123456", "name": "合成イラスト集",
                  "brand": {"@type": "Brand", "name": "合成サークル"}}
        rows = {"サークル名": "合成サークル", "作者": "著者甲", "販売日": "2024年2月29日",
                "作品形式": "CG・イラスト", "ジャンル": "イラスト集"}
        html = '<html><title>合成作品 | DLsite</title><h1 id="work_name">合成イラスト集</h1>'
        html += '<script type="application/ld+json">' + json.dumps(schema) + '</script><dl>'
        html += ''.join('<div class="c-productInfo__item"><dt>'+key+'</dt><dd><a>'+value+'</a></dd></div>' for key,value in rows.items())
        html += '</dl></html>'
        result, _ = self.dl(html.encode())
        self.assertEqual(result["fields"]["genre"], "插画集")
        self.assertEqual(result["fields"]["publisher"], "合成サークル")
        self.assertEqual(result["fields"]["authors"], ["著者甲"])
        self.assertEqual(result["fields"]["day"], "29")

    def test_dlsite_merged_cg_illustration_format_does_not_guess_a_narrower_content_type(self):
        result, _ = self.dl(dl_html(form="CG・イラスト"))
        self.assertNotIn("genre", result["fields"])

    def test_dlsite_does_not_pick_unrelated_recommendations_or_ambiguous_schema(self):
        other = {"@type": "Book", "url": DL.replace("123456", "654321"), "name": "不要な関連作品"}
        result, _ = self.dl(dl_html('<script type="application/ld+json">' + json.dumps(other) + '</script>'))
        self.assertEqual(result["fields"]["title"], "合成マンガ")
        matching = {"@type": "Book", "url": DL, "name": "Duplicate"}
        script = '<script type="application/ld+json">' + json.dumps([matching, matching]) + '</script>'
        with self.assertRaises(ScrapeError):
            self.dl(dl_html(script))

    def test_dlsite_canonical_url_must_remain_on_the_same_work(self):
        for canonical in (DL.replace("123456", "654321"), "https://evil.invalid/work/=/product_id/RJ123456.html"):
            with self.subTest(canonical=canonical), self.assertRaises(ScrapeError):
                self.dl(dl_html(canonical=canonical))

    def test_dlsite_schema_conflicting_sku_and_url_cannot_supply_candidates(self):
        schema = {"@type": "Book", "sku": "RJ123456", "name": "Conflicting", "url": DL.replace("123456", "654321")}
        with self.assertRaises(ScrapeError):
            self.dl(dl_html('<script type="application/ld+json">' + json.dumps(schema) + '</script>'))

    def test_dlsite_age_login_and_challenge_pages_are_not_work_metadata(self):
        for heading, extra in [("年齢確認", ""), ("Just a moment", ""), ("ログイン", '<input type="password">')]:
            html = f"<html><title>{heading}</title><h1>{heading}</h1>{extra}</html>".encode()
            with self.subTest(heading=heading), self.assertRaises(ScrapeError) as captured:
                self.dl(html)
            self.assertEqual(captured.exception.code, "SCRAPE_BLOCKED")

    def test_dlsite_plain_title_or_og_title_cannot_be_used_as_fabricated_metadata(self):
        with self.assertRaises(ScrapeError):
            self.dl(b'<html><title>Unverified</title><meta property="og:title" content="Unverified"><body>Other text</body></html>')
        with self.assertRaises(ScrapeError):
            self.dl(dl_html(form="ゲーム"))

    def test_dlsite_bare_host_and_tracking_are_canonicalized_without_redirect(self):
        result, request = self.dl(url="https://dlsite.com/home/work/=/product_id/RJ123456.html?affiliate=private#fragment")
        self.assertEqual(result["sourceUrl"], DL)
        self.assertEqual(request.calls[0][0], DL)

    def test_dlsite_parser_bounds_and_bad_encoding_fail_without_echoing_source(self):
        for data in (b"\xff\xfeinvalid", b"<div>" * 100 + b"secret", b"x" * (4 * 1024 * 1024 + 1)):
            with self.subTest(size=len(data)), self.assertRaises(ScrapeError) as captured:
                self.dl(data)
            self.assertNotIn("secret", str(captured.exception))

    def test_unsupported_provider_does_not_make_a_request(self):
        request = Request(b"")
        with self.assertRaises(ScrapeError):
            fetch_storefront("unsupported", urlsplit(DL), {}, request)
        self.assertFalse(request.calls)


if __name__ == "__main__":
    unittest.main()
