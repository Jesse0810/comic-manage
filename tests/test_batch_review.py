"""Two-stage batch review and synchronization against a local mock Komga."""
from copy import deepcopy
import json
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

from comic_manage.batch_review import MAX_PLANS, PLAN_TTL_SECONDS
from comic_manage.komga_client import KomgaError
from comic_manage.komga_sync import synchronize
from tests import test_komga_remote as remote_fixture
from tests import test_komga_sync as sync_fixture


class BatchReviewTests(unittest.TestCase):
    setUp = sync_fixture.KomgaSyncTests.setUp
    tearDown = sync_fixture.KomgaSyncTests.tearDown
    patches = sync_fixture.KomgaSyncTests.patches
    comic = sync_fixture.KomgaSyncTests.comic
    local_remote = sync_fixture.KomgaSyncTests.remote
    seed = remote_fixture.KomgaRemoteTests.seed
    records = remote_fixture.KomgaRemoteTests.records

    def call(self, method, *, cancel=None, **params):
        return self.service.call(method, {"url": self.url, "apiKey": self.key, "libraryId": "lib", **params}, cancel)

    def pull(self, count=1):
        for number in range(1, count + 1):
            self.seed(f"b{number}")
        self.call("komga_pull")
        return [self.records()[f"b{number}"].id for number in range(1, count + 1)]

    def prepare(self, identities, **params):
        return self.call("prepare_review_sync", ids=identities, **params)

    def commit(self, preview, **params):
        return self.call("commit_review_sync", token=preview["token"], **params)

    def dump(self):
        with self.service.repo.connect() as db:
            return list(db.iterdump())

    def test_preview_is_read_only_and_returned_rows_cannot_edit_frozen_selection(self):
        first, second = self.pull(2)
        before = self.dump()
        preview = self.prepare([first])
        self.assertEqual((preview["total"], preview["ready"], preview["blocked"], preview["cancelled"]), (1, 1, 0, False))
        self.assertEqual(self.dump(), before)
        self.assertEqual((self.patches("books"), self.patches("series")), ([], []))
        row = preview["items"][0]
        self.assertEqual((row["kind"], row["authors"], row["genre"], row["ageRating"], row["dirtyFields"]), ("komga", ["Writer"], "漫画", "Everyone", []))
        row.update(id=second, authors=["伪造作者"], title="篡改预览")
        result = self.commit(preview, ids=[second], items=[{"id": second}])
        self.assertEqual(result["items"][0]["id"], first)
        self.assertEqual(result["reviewed"], 1)
        self.assertEqual(result["sync"]["unchanged"], 1)
        self.assertEqual(self.service.repo.get(second).status, "draft")
        self.assertNotIn(self.key, json.dumps(preview) + json.dumps(result) + str(self.dump()))

    def test_full_series_is_reviewed_as_one_group_and_synchronize_is_called_once(self):
        identities = self.pull(2)
        self.service.call("batch_edit", {"ids": identities, "changes": {"publisher": "新发行方"}})
        for identity in identities:
            self.service.call("save", {"id": identity, "metadata": {"summary": "本次确认的简介"}})
        preview = self.prepare(identities)
        self.assertEqual(preview["ready"], 2)
        with patch("comic_manage.batch_review.synchronize", wraps=synchronize) as sync:
            result = self.commit(preview)
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(sync.call_args.args[1], identities)
        self.assertEqual((result["reviewed"], result["skipped"], result["failed"]), (2, 0, 0))
        self.assertEqual(result["sync"]["total"], 2)
        self.assertEqual(result["sync"]["seriesUpdated"], 1)
        self.assertEqual(self.patches("series"), [{"publisher": "新发行方", "publisherLock": True}])
        self.assertEqual(len(self.patches("books")), 2)

    def test_missing_v12_facts_block_individually_and_only_ready_subset_commits(self):
        identities = self.pull(6)
        for identity, changes in zip(identities[1:], ({"authors": []}, {"genre": ""}, {"age_rating": "Unknown"}, {"tags": [], "characters": [], "original_works": []}, {"original_works": ["A", "B"]})):
            comic = self.service.repo.get(identity)
            payload = {**comic.metadata.to_dict(), **changes}
            # Preserve a legacy multiple-original draft to exercise review, not edit validation.
            with self.service.repo.connect() as db:
                db.execute("UPDATE comics SET metadata=? WHERE id=?", (json.dumps(payload), identity))
        preview = self.prepare(identities)
        self.assertEqual((preview["ready"], preview["blocked"]), (1, 5))
        self.assertTrue(all(item["message"] for item in preview["items"]))
        result = self.commit(preview)
        self.assertEqual((result["total"], result["reviewed"], result["skipped"]), (6, 1, 5))
        self.assertEqual(result["sync"]["total"], 1)
        for identity in identities[1:]:
            self.assertEqual(self.service.repo.get(identity).status, "draft")

    def test_mixed_local_and_remote_records_keep_existing_packaged_and_approved_states(self):
        remote_id = self.pull()[0]
        local = self.comic("local-first", series="本地系列")
        self.local_remote(local, "local-book", series_id="local-series")
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET status='packaged' WHERE id=?", (local.id,))
        preview = self.prepare([remote_id, local.id])
        self.assertEqual(preview["ready"], 2)
        result = self.commit(preview)
        self.assertEqual(result["reviewed"], 2)
        self.assertEqual(self.service.repo.get(local.id).status, "packaged")
        self.assertEqual(result["sync"]["total"], 2)
        # A second confirmation includes already approved entries without downgrading them.
        again = self.prepare([remote_id])
        self.assertEqual(self.commit(again)["reviewed"], 1)
        self.assertEqual(self.service.repo.get(remote_id).status, "approved")

    def test_folder_without_cbz_target_and_duplicate_local_remote_targets_are_blocked(self):
        remote_id = self.pull()[0]
        local = self.comic("b1")
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET status='draft' WHERE id=?", (local.id,))
        duplicate = self.prepare([local.id, remote_id])
        self.assertEqual((duplicate["ready"], duplicate["blocked"], duplicate["token"]), (0, 2, ""))
        self.assertTrue(all("同一本" in item["message"] for item in duplicate["items"]))
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET output='' WHERE id=?", (local.id,))
        preview = self.prepare([local.id])
        self.assertEqual((preview["ready"], preview["token"]), (0, ""))
        self.assertIn("先生成", preview["items"][0]["message"])

    def test_local_zero_or_ambiguous_remote_path_match_never_becomes_ready(self):
        local = self.comic("local")
        zero = self.prepare([local.id])
        self.assertIn("0 本", zero["items"][0]["message"])
        self.local_remote(local, "local-a")
        self.remote_books["local-b"] = {**deepcopy(self.remote_books["local-a"]), "id": "local-b"}
        ambiguous = self.prepare([local.id])
        self.assertIn("2 本", ambiguous["items"][0]["message"])
        self.assertFalse(ambiguous["token"])

    def test_token_is_one_use_expires_is_bounded_and_is_bound_to_connection(self):
        identity = self.pull()[0]
        preview = self.prepare([identity])
        with self.assertRaisesRegex(KomgaError, "连接配置"):
            self.commit(preview, libraryId="other")
        with self.assertRaisesRegex(KomgaError, "已使用"):
            self.commit(preview)
        preview = self.prepare([identity])
        with self.assertRaisesRegex(KomgaError, "连接配置"):
            self.commit(preview, apiKey="changed-key")
        preview = self.prepare([identity])
        self.service.reviews.plans[preview["token"]].created_at -= PLAN_TTL_SECONDS + 1
        with self.assertRaisesRegex(KomgaError, "过期"):
            self.commit(preview)
        self.assertEqual(MAX_PLANS, 32)
        with patch("comic_manage.batch_review.MAX_PLANS", 2):
            older, middle, latest = [self.prepare([identity]) for _ in range(3)]
        self.assertEqual(len(self.service.reviews.plans), 2)
        with self.assertRaises(KomgaError):
            self.commit(older)
        self.assertIn(middle["token"], self.service.reviews.plans)
        self.assertIn(latest["token"], self.service.reviews.plans)
        self.assertEqual(self.service.repo.get(identity).status, "draft")

    def test_metadata_change_and_late_transaction_race_skip_without_review(self):
        first, second = self.pull(2)
        preview = self.prepare([first])
        self.service.call("save", {"id": first, "metadata": {"title": "预览后改名"}})
        result = self.commit(preview)
        self.assertEqual((result["reviewed"], result["skipped"], result["sync"]), (0, 1, None))
        self.assertEqual(self.service.repo.get(first).status, "draft")
        preview = self.prepare([second])
        original = self.service.repo.approve_checked
        def concurrent(identity, state, cancel):
            self.service.call("save", {"id": identity, "metadata": {"summary": "另一编辑的修改"}})
            return original(identity, state, cancel)
        with patch.object(self.service.repo, "approve_checked", side_effect=concurrent):
            result = self.commit(preview)
        self.assertEqual(result["reviewed"], 0)
        self.assertIn("本机资料", result["items"][0]["message"])
        self.assertEqual(self.service.repo.get(second).status, "draft")

    def test_local_source_change_and_remote_change_invalidate_confirmation(self):
        local = self.comic("local")
        self.local_remote(local, "local-book", series_id="local-series")
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET status='draft' WHERE id=?", (local.id,))
        preview = self.prepare([local.id])
        (Path(local.source) / "1.jpg").write_bytes(b"changed source")
        result = self.commit(preview)
        self.assertEqual(result["reviewed"], 0)
        self.assertIn("源文件", result["items"][0]["message"])
        identity = self.pull()[0]
        preview = self.prepare([identity])
        before = self.dump()
        self.remote_books["b1"]["metadata"]["title"] = "外部编辑改名"
        result = self.commit(preview)
        self.assertEqual((result["reviewed"], result["sync"]), (0, None))
        self.assertIn("远端资料", result["items"][0]["message"])
        self.assertEqual(self.dump(), before)

    def test_remote_scope_conflict_and_unavailable_cannot_be_reviewed(self):
        identities = self.pull(3)
        with self.service.repo.connect() as db:
            db.execute("UPDATE komga_sources SET conflict=1 WHERE comic_id=?", (identities[0],))
            db.execute("UPDATE komga_sources SET unavailable=1 WHERE comic_id=?", (identities[1],))
        preview = self.prepare(identities)
        self.assertEqual((preview["ready"], preview["blocked"]), (1, 2))
        different = self.prepare([identities[2]], libraryId="other")
        self.assertEqual(different["ready"], 0)
        self.assertIn("不属于当前", different["items"][0]["message"])
        self.assertEqual(self.patches("books"), [])

    def test_read_progress_and_volatile_timestamps_do_not_invalidate_confirmed_metadata(self):
        identity = self.pull()[0]
        preview = self.prepare([identity])
        self.remote_books["b1"]["readProgress"] = {"page": 99}
        self.remote_books["b1"]["metadata"]["lastModified"] = "later"
        self.remote_books["b1"]["metadata"]["authors"].reverse()
        self.assertEqual(self.commit(preview)["reviewed"], 1)
        self.assertEqual(self.patches("books"), [])

    def test_preview_and_commit_cancellation_are_explicit_and_do_not_replay_tokens(self):
        identity = self.pull()[0]
        cancel = Event()
        cancel.set()
        before = self.dump()
        preview = self.prepare([identity], cancel=cancel)
        self.assertTrue(preview["cancelled"])
        self.assertEqual((preview["token"], preview["ready"]), ("", 0))
        self.assertEqual(self.dump(), before)
        preview = self.prepare([identity])
        result = self.commit(preview, cancel=cancel)
        self.assertTrue(result["cancelled"])
        self.assertEqual((result["reviewed"], result["sync"]), (0, None))
        with self.assertRaises(KomgaError):
            self.commit(preview)
        self.assertEqual(self.dump(), before)

    def test_cancellation_during_review_keeps_only_completed_approvals_and_skips_sync(self):
        identities = self.pull(2)
        preview = self.prepare(identities)
        cancel = Event()
        original = self.service.repo.approve_checked
        def stop_after_first(identity, state, current_cancel):
            status = original(identity, state, current_cancel)
            cancel.set()
            return status
        with patch.object(self.service.repo, "approve_checked", side_effect=stop_after_first):
            result = self.commit(preview, cancel=cancel)
        self.assertTrue(result["cancelled"])
        self.assertEqual((result["reviewed"], result["skipped"], result["sync"]), (1, 1, None))
        self.assertEqual([self.service.repo.get(identity).status for identity in identities], ["approved", "draft"])
        self.assertEqual(self.patches("books"), [])

    def test_auth_failure_stops_later_checks_and_never_approves_a_partial_preflight(self):
        identities = self.pull(3)
        self.forced[("GET", "/api/v1/libraries")] = (401, {"error": "private"})
        preview = self.prepare(identities)
        self.assertEqual((preview["ready"], preview["token"]), (0, ""))
        self.assertIn("error", preview)
        self.forced.clear()
        preview = self.prepare(identities)
        before = self.dump()
        self.requests.clear()
        self.forced[("GET", "/api/v1/books/b2")] = (403, {"error": "private"})
        result = self.commit(preview)
        self.assertEqual((result["reviewed"], result["failed"], result["skipped"]), (0, 1, 2))
        self.assertIn("error", result)
        self.assertEqual(self.dump(), before)
        self.assertFalse(any("/books/b3" in request[1] for request in self.requests))
        self.assertEqual(self.patches("books"), [])

    def test_sync_auth_failure_retains_successful_reviews_and_reports_separate_error(self):
        identity = self.pull()[0]
        preview = self.prepare([identity])
        def fail_sync(*args, **kwargs):
            self.forced[("GET", "/api/v1/libraries")] = (401, {"error": "private"})
            return synchronize(*args, **kwargs)
        with patch("comic_manage.batch_review.synchronize", side_effect=fail_sync):
            result = self.commit(preview)
        self.assertEqual((result["reviewed"], result["sync"]), (1, None))
        self.assertIn("error", result)
        self.assertEqual(self.service.repo.get(identity).status, "approved")
        self.assertEqual(self.patches("books"), [])

    def test_preview_displays_the_exact_frozen_metadata_even_if_edited_during_list_request(self):
        identity = self.pull()[0]
        changed = False
        def edit_during_request(method, path):
            nonlocal changed
            if not changed and path == "/api/v1/libraries":
                changed = True
                self.service.call("save", {"id": identity, "metadata": {"title": "预览期间新标题", "authors": ["新作者"], "genre": "小说", "age_rating": "MA15+"}})
        self.before_request = edit_during_request
        preview = self.prepare([identity])
        self.assertEqual(preview["ready"], 1)
        row = preview["items"][0]
        self.assertEqual((row["title"], row["authors"], row["genre"], row["ageRating"]), ("预览期间新标题", ["新作者"], "小说", "MA15+"))
        self.assertEqual(set(row["dirtyFields"]), {"title", "authors", "genre", "age_rating"})
        frozen = self.service.reviews.plans[preview["token"]].checked[identity]
        self.assertEqual(row["title"], frozen.state["metadata"]["title"])

    def test_sync_cannot_rebind_confirmed_local_target_to_new_book_at_same_path(self):
        local = self.comic("local")
        self.local_remote(local, "original-book", series_id="local-series")
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET status='draft' WHERE id=?", (local.id,))
        preview = self.prepare([local.id])
        def replace_before_sync(*args, **kwargs):
            original = self.remote_books.pop("original-book")
            self.remote_books["replacement-book"] = {**original, "id": "replacement-book"}
            return synchronize(*args, **kwargs)
        with patch("comic_manage.batch_review.synchronize", side_effect=replace_before_sync):
            result = self.commit(preview)
        self.assertEqual(result["reviewed"], 1)
        self.assertEqual((result["sync"]["updated"], result["sync"]["skipped"]), (0, 1))
        self.assertIn("已确认", result["sync"]["books"][0]["message"])
        self.assertEqual((self.patches("books"), self.patches("series")), ([], []))

    def test_sync_preserves_external_book_and_series_edits_after_confirmation(self):
        local = self.comic("local")
        self.local_remote(local, "book", series_id="local-series")
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET status='draft' WHERE id=?", (local.id,))
        preview = self.prepare([local.id])
        def edit_before_sync(*args, **kwargs):
            self.remote_books["book"]["metadata"]["title"] = "确认后其他管理员改名"
            return synchronize(*args, **kwargs)
        with patch("comic_manage.batch_review.synchronize", side_effect=edit_before_sync):
            result = self.commit(preview)
        self.assertEqual(result["sync"]["skipped"], 1)
        self.assertEqual(self.patches("books"), [])
        preview = self.prepare([local.id])
        self.after_patch = lambda path: self.remote_series["local-series"]["metadata"].update(publisher="其他管理员新发行方")
        result = self.commit(preview)
        self.assertEqual(result["sync"]["updated"], 1)
        self.assertEqual(self.patches("series"), [])
        self.assertEqual(result["sync"]["series"][0]["status"], "skipped")
        self.assertEqual(self.remote_series["local-series"]["metadata"]["publisher"], "其他管理员新发行方")

    def test_partial_series_warning_only_appears_for_shared_changes(self):
        first, second = self.pull(2)
        self.service.call("save", {"id": first, "metadata": {"title": "仅改逐册名"}})
        self.assertEqual(self.prepare([first])["warnings"], [])
        self.service.call("save", {"id": first, "metadata": {"publisher": "新发行方"}})
        self.assertTrue(any("尚未选全" in value for value in self.prepare([first])["warnings"]))
        self.assertEqual(self.prepare([first, second])["warnings"], [])

    def test_sync_rechecks_live_local_source_after_successful_review(self):
        local = self.comic("local")
        self.local_remote(local, "book", series_id="local-series")
        with self.service.repo.connect() as db:
            db.execute("UPDATE comics SET status='draft' WHERE id=?", (local.id,))
        preview = self.prepare([local.id])
        def edit_source_before_sync(*args, **kwargs):
            (Path(local.source) / "1.jpg").write_bytes(b"modified after review")
            return synchronize(*args, **kwargs)
        with patch("comic_manage.batch_review.synchronize", side_effect=edit_source_before_sync):
            result = self.commit(preview)
        self.assertEqual(result["reviewed"], 1)
        self.assertEqual(result["sync"]["skipped"], 1)
        self.assertIn("源文件", result["sync"]["books"][0]["message"])
        self.assertEqual((self.patches("books"), self.patches("series")), ([], []))


if __name__ == "__main__":
    unittest.main()
