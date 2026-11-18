"""Synthetic metadata only; tests never request site pages or image content."""
import copy
import json
import unittest
from urllib.parse import urlsplit

from comic_manage.scrape_galleries import fetch_gallery
from comic_manage.scrape_http import ScrapeError


def nh_detail(**updates):
    # Field names follow nhentai's official v2 GalleryDetailResponse schema.
    value = {
        "id": 42, "media_id": "123", "num_pages": 8, "num_favorites": 1,
        "title": {"japanese": "星の散歩", "pretty": "A Walk Among Stars", "english": "[Hana] A Walk Among Stars [English]"},
        "cover": {"path": "https://never-fetch.invalid/cover.jpg", "width": 200, "height": 300},
        "thumbnail": {"path": "https://never-fetch.invalid/thumb.jpg", "width": 100, "height": 150},
        "pages": [{"number": 1, "path": "http://127.0.0.1/never-fetch", "width": 200, "height": 300}],
        "upload_date": 1700000000,
        "tags": [
            {"id": 1, "type": "artist", "name": "Hana", "slug": "hana", "url": "/artist/hana", "count": 1},
            {"id": 2, "type": "group", "name": "Star Circle", "slug": "star-circle", "url": "/group/star-circle", "count": 1},
            {"id": 3, "type": "character", "name": "Mira", "slug": "mira", "url": "/character/mira", "count": 1},
            {"id": 4, "type": "parody", "name": "Star Stories", "slug": "star-stories", "url": "/parody/star-stories", "count": 1},
            {"id": 5, "type": "language", "name": "japanese", "slug": "japanese", "url": "/language/japanese", "count": 1},
            {"id": 6, "type": "category", "name": "doujinshi", "slug": "doujinshi", "url": "/category/doujinshi", "count": 1},
            {"id": 7, "type": "tag", "name": "space travel", "slug": "space-travel", "url": "/tag/space-travel", "count": 1},
        ],
    }
    value.update(updates)
    return value


class GalleryScrapeTests(unittest.TestCase):
    def request_for(self, payload):
        self.calls = []

        def request(url, **kwargs):
            self.calls.append((url, kwargs))
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

        return request

    def test_nh_v2_japanese_title_and_namespaced_fields_without_image_requests(self):
        payload = nh_detail()
        original = copy.deepcopy(payload)
        result = fetch_gallery("nhentai", urlsplit("https://nhentai.net/g/42/?external=1#page=3"), {}, self.request_for(payload))
        self.assertEqual(result["sourceUrl"], "https://nhentai.net/g/42/")
        self.assertEqual(result["fields"], {
            "title": "星の散歩", "authors": ["Hana"], "publisher": "Star Circle",
            "characters": ["Mira"], "original_works": ["Star Stories"], "language": "ja",
            "genre": "漫画", "release_type": "同人发行", "tags": ["tag:space travel"],
        })
        self.assertEqual(self.calls, [("https://nhentai.net/api/v2/galleries/42", {"headers": {}})])
        self.assertEqual(payload, original)
        self.assertTrue(any("上传时间" in value for value in result["warnings"]))
        self.assertFalse({"year", "month", "day", "age_rating", "color", "uncensored", "cover_index", "work_group", "text_version"} & result["fields"].keys())

    def test_optional_api_key_only_uses_official_nh_json_endpoint(self):
        fetch_gallery("nhentai", urlsplit("https://nhentai.net/g/42/"), {"apiKey": " test-secret ", "cookie": "unused"}, self.request_for(nh_detail()))
        self.assertEqual(self.calls, [("https://nhentai.net/api/v2/galleries/42", {"headers": {"Authorization": "Key test-secret"}})])

    def test_api_key_header_injection_is_rejected_without_request(self):
        request = self.request_for(nh_detail())
        with self.assertRaises(ScrapeError):
            fetch_gallery("nhentai", urlsplit("https://nhentai.net/g/42/"), {"apiKey": "key\r\nInjected: value"}, request)
        self.assertEqual(self.calls, [])

    def test_eh_official_gdata_namespaces_and_clean_title(self):
        payload = {"gmetadata": [{
            "gid": 42, "title": "English fallback", "title_jpn": "(C101) [Star Circle (Hana)] 星の散歩 [Digital]",
            "category": "Manga", "posted": "1700000000", "thumb": "http://127.0.0.1/never-fetch",
            "tags": ["artist:Hana", "artist:hana", "group:Star Circle", "character:Mira", "parody:Star Stories", "language:japanese", "other:space travel"],
            "filecount": "8", "rating": "4.5", "torrents": [{"url": "https://never-fetch.invalid/torrent"}],
        }]}
        result = fetch_gallery("ehentai", urlsplit("https://e-hentai.org/g/42/ABCDEF1234/?ignore=1#p=2"), {"apiKey": "never-send"}, self.request_for(payload))
        self.assertEqual(self.calls, [("https://api.e-hentai.org/api.php", {"body": {"method": "gdata", "gidlist": [[42, "abcdef1234"]], "namespace": 1}})])
        self.assertEqual(result["sourceUrl"], "https://e-hentai.org/g/42/abcdef1234/")
        fields = result["fields"]
        self.assertEqual(fields["title"], "星の散歩")
        self.assertEqual(fields["authors"], ["Hana"])
        self.assertEqual(fields["release_type"], "商业出版")
        self.assertEqual(fields["tags"], ["other:space travel"])
        self.assertNotIn("year", fields)

    def test_exact_paths_required_before_any_request(self):
        for provider, path in [
            ("nhentai", "/g/0/"), ("nhentai", "/g/42/1/"), ("nhentai", "/g/%34%32/"),
            ("nhentai", "/g/42//"), ("nhentai", "/api/v2/galleries/42"),
            ("ehentai", "/g/42/token/"), ("ehentai", "/g/42/abcdef1234/extra"),
            ("ehentai", "/s/abcdef1234/42-1"), ("unrecognized", "/g/42/"),
        ]:
            with self.subTest(provider=provider, path=path):
                request = self.request_for({})
                with self.assertRaises(ScrapeError):
                    fetch_gallery(provider, urlsplit("https://nhentai.net" + path), {}, request)
                self.assertEqual(self.calls, [])

    def test_parody_original_does_not_create_an_original_work_entity(self):
        for names in [["original"], ["Series A", "Series B"], ["original", "Series A"]]:
            with self.subTest(names=names):
                value = nh_detail(tags=[{"type": "parody", "name": name} for name in names])
                result = fetch_gallery("nhentai", urlsplit("https://nhentai.net/g/42/"), {}, self.request_for(value))
                self.assertNotIn("original_works", result["fields"])
                if len(names) > 1:
                    self.assertTrue(any("原作" in text for text in result["warnings"]))
                    self.assertIn("parody:Series A", result["fields"]["tags"])

    def test_ambiguous_art_category_does_not_invent_content_type(self):
        result = fetch_gallery("nhentai", urlsplit("https://nhentai.net/g/42/"), {}, self.request_for(nh_detail(tags=[{"type": "category", "name": "artistcg"}])))
        self.assertNotIn("genre", result["fields"])
        self.assertNotIn("release_type", result["fields"])
        self.assertEqual(result["fields"]["tags"], ["category:artistcg"])

    def test_chinese_language_does_not_guess_script(self):
        result = fetch_gallery("nhentai", urlsplit("https://nhentai.net/g/42/"), {}, self.request_for(nh_detail(tags=[{"type": "language", "name": "chinese"}, {"type": "language", "name": "translated"}])))
        self.assertEqual(result["fields"]["language"], "zh")
        self.assertEqual(result["fields"]["tags"], ["language:translated"])
        self.assertNotIn("translator", result["fields"])

    def test_multiple_languages_do_not_pick_one_arbitrarily(self):
        tags = [{"type": "language", "name": name} for name in ["japanese", "english"]]
        result = fetch_gallery("nhentai", urlsplit("https://nhentai.net/g/42/"), {}, self.request_for(nh_detail(tags=tags)))
        self.assertNotIn("language", result["fields"])
        self.assertTrue(any("多种正文语言" in warning for warning in result["warnings"]))

    def test_unbounded_source_title_or_tag_values_are_rejected_before_parsing(self):
        for payload in [
            nh_detail(title={"japanese": "[x]" * 2000}),
            nh_detail(tags=[{"type": "artist", "name": "x" * 1025}]),
            nh_detail(tags=[{"type": "tag", "name": "space"}] * 1025),
        ]:
            with self.subTest(payload=payload):
                with self.assertRaises(ScrapeError):
                    fetch_gallery("nhentai", urlsplit("https://nhentai.net/g/42/"), {}, self.request_for(payload))

    def test_nh_title_fallback_and_unknown_bracket_note_preservation(self):
        for title, expected in [
            ({"japanese": None, "pretty": "A Walk Among Stars", "english": "English fallback"}, "A Walk Among Stars"),
            ({"japanese": "", "pretty": "", "english": "[Hana] A Walk Among Stars [English]"}, "A Walk Among Stars"),
            ({"japanese": "星の散歩 [Moon chapter]", "pretty": "English fallback", "english": "English fallback"}, "星の散歩 [Moon chapter]"),
        ]:
            with self.subTest(title=title):
                result = fetch_gallery("nhentai", urlsplit("https://nhentai.net/g/42/"), {}, self.request_for(nh_detail(title=title)))
                self.assertEqual(result["fields"]["title"], expected)

    def test_bad_or_mismatched_json_metadata_is_rejected(self):
        values = [nh_detail(id=99), nh_detail(id=True), nh_detail(title="wrong"), nh_detail(tags={}), nh_detail(title={})]
        for payload in values:
            with self.subTest(payload=payload):
                with self.assertRaises(ScrapeError):
                    fetch_gallery("nhentai", urlsplit("https://nhentai.net/g/42/"), {}, self.request_for(payload))
        for payload in [{"gmetadata": []}, {"gmetadata": [{"gid": 42, "error": "Invalid key"}]}, {"gmetadata": [{"gid": 99, "title": "Different work"}]}]:
            with self.subTest(payload=payload):
                with self.assertRaises(ScrapeError):
                    fetch_gallery("ehentai", urlsplit("https://e-hentai.org/g/42/abcdef1234/"), {}, self.request_for(payload))


if __name__ == "__main__":
    unittest.main()
