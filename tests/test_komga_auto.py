"""Strict automatic matching and entity-only reconciliation, with no real NAS."""
from copy import deepcopy
from email.message import Message
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from io import BytesIO
from threading import Event
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from comic_manage.komga_auto import AutoSyncIssue, match_book, synchronize_entities, target_entities
from comic_manage.komga_client import KomgaClient, KomgaError
from comic_manage.metadata import Metadata


def metadata(**fields):
    return Metadata(**{"title": "日本語と中文", "authors": ["作者"], "genre": "漫画", "age_rating": "Everyone",
                       "tags": ["日常"], "collection_mode": "oneshot", "characters": ["角色甲"],
                       "release_type": "同人发行", "work_group": "CaseＡ Work  作者Ｂ", "text_version": "有文字", **fields})


def remote(identity="book", **fields):
    return {"id": identity, "seriesId": "series", "libraryId": "actual-library", "oneshot": True,
            "name": "日本語と中文 [123]", "url": "/remote/nested/_oneshots/日本語と中文 [123].cbz",
            "readProgress": {"page": 3}, "metadata": {"title": "日本語と中文", "number": "1", "titleLock": False,
                "notableEntities": [{"kind": "character", "name": "旧角色"}, {"kind": "原作", "name": "旧原作"},
                                    {"kind": "TEAM", "name": " 外部小组 "}, {"kind": "team", "name": "外部小组"}],
                "notableEntitiesLock": False, "authors": [{"name": "原作者", "role": "writer"}],
                "tags": ["原始标签"], "authorsLock": False}, **fields}


class FakeClient:
    def __init__(self, book=None):
        self.cancel = Event()
        self.book = deepcopy(book if book is not None else remote())
        self.series = {"id": "series", "libraryId": "actual-library", "oneshot": False,
                       "metadata": {"title": "真正系列", "alternateTitles": [{"label": "en", "title": "Original Alias"}],
                                    "publisher": "出版方", "ageRating": 18}}
        self.requests = []
        self.books_calls = 0
        self.apply_patch = True
        self.on_request = lambda method, path: None

    def books(self, library):
        self.books_calls += 1
        return [deepcopy(self.book)]

    def request(self, method, path, body=None):
        self.requests.append((method, path, deepcopy(body)))
        self.on_request(method, path)
        target = self.book if "/books/" in path else self.series
        if method == "PATCH":
            if self.apply_patch:
                target["metadata"].update(deepcopy(body))
            return {}
        return deepcopy(target)

    def patches(self):
        return [entry for entry in self.requests if entry[0] == "PATCH"]


class AutoMatchTests(unittest.TestCase):
    relative = "nested/_oneshots/日本語と中文 [123].cbz"

    def assert_issue(self, books, code, state="NeedsManualReview"):
        with self.assertRaises(AutoSyncIssue) as caught:
            match_book(books, "actual-library", self.relative, "日本語と中文")
        self.assertEqual((caught.exception.code, caught.exception.state), (code, state))

    def test_full_relative_path_and_exact_title_take_priority(self):
        book = remote()
        decoy = remote("decoy", url="/else/日本語と中文 [123].cbz")
        self.assertEqual(match_book([book, decoy], "actual-library", self.relative, "日本語と中文")["id"], "book")
        self.assertEqual(book["metadata"]["title"], "日本語と中文")

    def test_extension_free_filename_title_fallback_and_not_title_alone(self):
        book = remote(url="/mapped/other/place.cbz")
        self.assertEqual(match_book([book], "actual-library", self.relative, "日本語と中文")["id"], "book")
        book["name"] = "unrelated"
        self.assert_issue([book], "KOMGA_AUTO_PENDING_INDEX", "PendingIndex")
        book["name"] = "日本語と中文 [123].cbz"
        self.assert_issue([book], "KOMGA_AUTO_PENDING_INDEX", "PendingIndex")

    def test_title_mismatch_never_uses_matching_path_or_name_alone(self):
        book = remote()
        book["metadata"]["title"] = "different title"
        self.assert_issue([book], "KOMGA_AUTO_TITLE")
        book["url"] = "/elsewhere/other.cbz"
        self.assert_issue([book], "KOMGA_AUTO_TITLE")

    def test_ambiguity_wrong_library_deleted_and_no_book(self):
        self.assert_issue([remote(), remote("duplicate")], "KOMGA_AUTO_AMBIGUOUS")
        self.assert_issue([remote(libraryId="foreign")], "KOMGA_AUTO_LIBRARY")
        self.assert_issue([remote(deleted=True)], "KOMGA_AUTO_PENDING_INDEX", "PendingIndex")
        self.assert_issue([], "KOMGA_AUTO_PENDING_INDEX", "PendingIndex")

    def test_full_path_ambiguity_is_not_hidden_by_a_remembered_id(self):
        client = FakeClient()
        client.books = lambda library: [remote(), remote("duplicate")]
        with self.assertRaisesRegex(AutoSyncIssue, "多本"):
            synchronize_entities(client, metadata(), "actual-library", self.relative, expected_book_id="book", backup=lambda _:None)
        self.assertEqual(client.patches(), [])


class AutoReconcileTests(unittest.TestCase):
    relative = "nested/_oneshots/日本語と中文 [123].cbz"

    def sync(self, client, value=None, **kwargs):
        return synchronize_entities(client, value or metadata(), "actual-library", self.relative, backup=kwargs.pop("backup", lambda _:None), **kwargs)

    def test_owned_kinds_replace_complete_character_set_and_preserve_unknown(self):
        client = FakeClient()
        before = deepcopy(client.book)
        backups = []
        result = self.sync(client, backup=backups.append)
        self.assertTrue(result["updated"])
        self.assertEqual(len(client.patches()), 1)
        request = client.patches()[0][2]
        self.assertEqual(set(request), {"notableEntities", "notableEntitiesLock"})
        self.assertIn({"kind": "TEAM", "name": "外部小组"}, request["notableEntities"])
        self.assertIn({"kind": "character", "name": "角色甲"}, request["notableEntities"])
        self.assertNotIn({"kind": "character", "name": "旧角色"}, request["notableEntities"])
        self.assertNotIn({"kind": "原作", "name": "旧原作"}, request["notableEntities"])
        self.assertIn({"kind": "作品组", "name": "CaseＡ Work  作者Ｂ"}, request["notableEntities"])
        self.assertEqual(client.book["readProgress"], before["readProgress"])
        for key in ("title", "titleLock", "number", "tags", "authors", "authorsLock"):
            self.assertEqual(client.book["metadata"][key], before["metadata"][key])
        self.assertEqual(backups[0]["before"], before["metadata"])
        self.assertEqual(backups[0]["request"], request)
        self.assertEqual(result["snapshot"], client.book)
        self.assertEqual(result["targetEntities"], request["notableEntities"])

    def test_legacy_publication_format_requires_review_without_patch_or_implicit_mapping(self):
        for legacy in ("独立短篇", "自定义旧词"):
            client = FakeClient()
            value = metadata(publication_format=legacy)
            before = value.to_dict()
            with self.assertRaises(AutoSyncIssue) as caught:
                self.sync(client, value)
            self.assertEqual((caught.exception.state, caught.exception.code), ("NeedsManualReview", "KOMGA_AUTO_PUBLICATION_FORMAT"))
            self.assertEqual(client.patches(), [])
            self.assertEqual(client.requests, [])
            self.assertEqual(value.to_dict(), before)

    def test_backup_is_persisted_before_mutation_and_failure_stops_patch(self):
        client = FakeClient()
        seen = []
        def backup(record):
            self.assertEqual(client.patches(), [])
            seen.append(record)
            raise ValueError("cannot persist backup")
        with self.assertRaisesRegex(ValueError, "cannot persist"):
            self.sync(client, backup=backup)
        self.assertEqual(len(seen), 1)
        self.assertEqual(client.patches(), [])

    def test_missing_persistent_backup_handler_never_writes(self):
        client = FakeClient()
        with self.assertRaises(AutoSyncIssue) as caught:
            synchronize_entities(client, metadata(), "actual-library", self.relative)
        self.assertEqual(caught.exception.code, "KOMGA_AUTO_BACKUP")
        self.assertEqual(client.patches(), [])

    def test_repeat_is_zero_patch_and_unlocked_equal_values_are_locked(self):
        client = FakeClient()
        self.sync(client)
        client.requests.clear()
        result = self.sync(client)
        self.assertEqual(client.patches(), [])
        self.assertFalse(result["updated"])
        client.book["metadata"]["notableEntitiesLock"] = False
        self.sync(client)
        self.assertEqual(len(client.patches()), 1)
        self.assertTrue(client.book["metadata"]["notableEntitiesLock"])

    def test_cleared_fields_remove_old_managed_values_instead_of_appending(self):
        client = FakeClient()
        self.sync(client)
        client.requests.clear()
        self.sync(client, metadata(characters=[], work_group="", text_version="", release_type=""))
        entities = client.book["metadata"]["notableEntities"]
        self.assertFalse(any(item["kind"] in ("character", "作品组", "文字版本", "发行性质") for item in entities))
        self.assertTrue(any(item["kind"] == "TEAM" for item in entities))
        self.assertEqual(len(client.patches()), 1)

    def test_readback_is_required_even_when_patch_returns_success(self):
        client = FakeClient()
        client.apply_patch = False
        with self.assertRaises(AutoSyncIssue) as caught:
            self.sync(client)
        self.assertEqual((caught.exception.state, caught.exception.code), ("RetryableFailure", "KOMGA_AUTO_READBACK"))
        self.assertEqual(len(client.patches()), 1)

    def test_duplicate_readback_is_not_success(self):
        client = FakeClient()
        def inject_duplicate(method, path):
            if method == "GET" and client.patches():
                client.book["metadata"]["notableEntities"].append({"kind": "character", "name": " 角色甲 "})
        client.on_request = inject_duplicate
        with self.assertRaises(AutoSyncIssue) as caught:
            self.sync(client)
        self.assertEqual(caught.exception.code, "KOMGA_AUTO_READBACK")

    def test_stored_id_and_live_identity_cannot_be_silently_replaced(self):
        client = FakeClient()
        with self.assertRaises(AutoSyncIssue) as caught:
            self.sync(client, expected_book_id="old-book")
        self.assertEqual(caught.exception.code, "KOMGA_AUTO_REPLACED")
        self.assertEqual(client.patches(), [])
        planned = deepcopy(client.book)
        client.book["url"] = "/elsewhere/replaced.cbz"
        with self.assertRaises(AutoSyncIssue):
            self.sync(client, book=planned)
        self.assertEqual(client.patches(), [])

    def test_guard_runs_for_mutation_and_zero_patch_success(self):
        client = FakeClient()
        calls = []
        self.sync(client, guard=lambda: calls.append(1))
        self.assertGreaterEqual(len(calls), 5)
        client.requests.clear()
        def invalid():
            raise ValueError("review changed")
        with self.assertRaisesRegex(ValueError, "review changed"):
            self.sync(client, guard=invalid)
        self.assertEqual(client.requests, [])

    def test_only_true_series_merges_aliases_without_touching_shared_native_fields(self):
        client = FakeClient(remote(oneshot=False))
        original_series = deepcopy(client.series)
        backups = []
        value = metadata(collection_mode="series", series="真正系列", localized_series="本地化系列", localized_series_label="zh-Hans")
        result = self.sync(client, value, backup=backups.append)
        self.assertTrue(result["seriesUpdated"])
        patches = client.patches()
        self.assertEqual(len(patches), 2)
        self.assertEqual(set(patches[1][2]), {"alternateTitles", "alternateTitlesLock"})
        self.assertIn({"label": "en", "title": "Original Alias"}, client.series["metadata"]["alternateTitles"])
        self.assertIn({"label": "zh-Hans", "title": "本地化系列"}, client.series["metadata"]["alternateTitles"])
        self.assertEqual(client.series["metadata"]["publisher"], original_series["metadata"]["publisher"])
        self.assertEqual(client.series["metadata"]["ageRating"], original_series["metadata"]["ageRating"])
        self.assertEqual(backups[1]["before"], original_series["metadata"])
        client.requests.clear()
        self.sync(client, value)
        self.assertEqual(client.patches(), [])

    def test_oneshot_version_group_never_creates_or_writes_series_alias(self):
        client = FakeClient()
        result = self.sync(client, metadata(localized_series="旧隐藏译名", localized_series_label="zh-Hans", series="隐藏旧系列"))
        self.assertFalse(result["seriesUpdated"])
        self.assertFalse(any("/series/" in item[1] for item in client.requests))
        self.assertNotIn("作品组", client.series["metadata"])

    def test_series_alias_readback_is_verified(self):
        client = FakeClient(remote(oneshot=False))
        def remove_alias(method, path):
            if method == "GET" and "/series/" in path and any("/series/" in p[1] for p in client.patches()):
                client.series["metadata"]["alternateTitles"] = []
        client.on_request = remove_alias
        value = metadata(collection_mode="series", series="真正系列", localized_series="译名", localized_series_label="zh-Hans")
        with self.assertRaises(AutoSyncIssue) as caught:
            self.sync(client, value)
        self.assertEqual(caught.exception.state, "RetryableFailure")


class ErrorClassificationTests(unittest.TestCase):
    def fail(self, status, retry=None):
        headers = Message()
        if retry is not None:
            headers["Retry-After"] = retry
        error = HTTPError("http://localhost/api", status, "remote-private-body", headers, BytesIO(b"must-not-leak-secret"))
        client = KomgaClient("http://localhost", "synthetic-secret")
        with patch.object(client._opener, "open", side_effect=error):
            with self.assertRaises(KomgaError) as caught:
                client.libraries()
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("private", str(caught.exception))
        return caught.exception

    def test_http_status_and_retry_after_are_safe_and_retryable_codes_are_distinct(self):
        for status, code in ((401, "KOMGA_AUTH"), (403, "KOMGA_FORBIDDEN"), (400, "KOMGA_HTTP"), (429, "KOMGA_RATE_LIMIT"), (503, "KOMGA_HTTP")):
            error = self.fail(status, "120")
            self.assertEqual(error.http_status, status)
            self.assertEqual(error.code, code)
            if status in (429, 503):
                self.assertEqual(error.retry_after, 120)
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=100))
        self.assertGreater(self.fail(429, future).retry_after, 90)
        self.assertIsNone(self.fail(429, "invalid").retry_after)


if __name__ == "__main__":
    unittest.main()
