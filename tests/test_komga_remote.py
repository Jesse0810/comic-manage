"""Remote-only pull, review, differential writeback and conflict handling over real HTTP."""
from copy import deepcopy
import json
import struct
from threading import Event
import unittest

from comic_manage.komga_client import KomgaError
from comic_manage.komga_remote import komga_scope, snapshot_fingerprint
from tests import test_komga_sync as sync_fixture


class KomgaRemoteTests(unittest.TestCase):
    setUp = sync_fixture.KomgaSyncTests.setUp
    tearDown = sync_fixture.KomgaSyncTests.tearDown
    patches = sync_fixture.KomgaSyncTests.patches

    def seed(self, book_id="b1", *, oneshot=False, entities=None):
        path = f"/nas/library/{'_oneshots' if oneshot else 'series-folder'}/{book_id}.cbz"
        self.remote_books[book_id] = {
            "id": book_id, "libraryId": "lib", "seriesId": "s", "deleted": False,
            "url": path, "oneshot": oneshot, "media": {"pagesCount": 3}, "readProgress": {"page": 1},
            "metadata": {"title": "远端标题 " + book_id, "summary": "原简介", "number": "1",
                         "numberSort": 1.0, "isbn": "unknown-preserved", "releaseDate": "2020-02-03",
                         "authors": [{"name": "Writer", "role": "writer"}, {"name": "Pencil", "role": "penciller"}, {"name": "Inker", "role": "inker"}],
                         "tags": ["原作:OldIP", "全彩", "其他:保留"],
                         "notableEntities": entities if entities is not None else [{"kind": "team", "name": "保留团队"}, {"kind": "character", "name": "原角色"}],
                         "links": [{"label": "标签一", "url": "https://example.org/a"}, {"label": "标签二", "url": "https://example.org/a"}]}}
        self.remote_series["s"] = {
            "id": "s", "libraryId": "lib", "deleted": False, "oneshot": oneshot,
            "url": path if oneshot else path.rsplit("/", 1)[0], "booksCount": len(self.remote_books),
            "metadata": {"title": "共享系列", "ageRating": 17, "genres": ["奇幻"], "publisher": "旧发行方",
                         "language": "ja", "readingDirection": "WEBTOON", "totalBookCount": 8,
                         "alternateTitles": [{"label": "zh-Hans", "title": "中文别名"}, {"label": "en", "title": "English"}]}}

    def call(self, method, **params):
        return self.service.call(method, {"url": self.url, "apiKey": self.key, "libraryId": "lib", **params})

    def records(self):
        return {comic.remote["bookId"]: comic for comic in self.service.repo.all() if comic.kind == "komga"}

    def approve(self, identity, **metadata):
        return self.call("approve", id=identity, metadata=metadata)

    def test_pull_creates_scoped_persistent_remote_records_without_local_files(self):
        self.seed("b1")
        self.seed("b2")
        result = self.call("komga_pull")
        self.assertEqual(result["added"], 2)
        self.assertEqual(result["scope"], komga_scope(self.url + "/", "lib"))
        record = self.records()["b1"]
        self.assertEqual((record.kind, record.page_count, record.output), ("komga", 3, ""))
        self.assertTrue(record.source.startswith("komga://"))
        self.assertEqual(record.metadata.age_rating, "Unknown")
        self.assertEqual(record.metadata.genre, "")
        self.assertEqual(record.remote["sharedMetadata"]["ageRating"], 17)
        self.assertEqual(record.remote["sharedMetadata"]["genres"], ["奇幻"])
        self.assertEqual(self.call("komga_pull")["added"], 0)
        self.assertEqual(self.records()["b1"].id, record.id)
        source = self.service.repo.get_komga_source(record.id)
        self.assertEqual(source["snapshot"]["book"]["metadata"]["isbn"], "unknown-preserved")
        self.assertNotIn(self.key, json.dumps(self.service.call("bootstrap")))
        self.assertEqual(self.service.call("source_context", {"id": record.id})["kind"], "komga")

    def test_unchanged_approval_and_sync_issue_zero_patches_and_preserve_unknown_values(self):
        self.seed()
        self.remote_books["b1"]["metadata"]["authors"] = []
        self.call("komga_pull")
        record = self.records()["b1"]
        before_book, before_series = deepcopy(self.remote_books), deepcopy(self.remote_series)
        self.approve(record.id)
        result = self.call("komga_sync", ids=[record.id])
        self.assertEqual((result["updated"], result["unchanged"], result["seriesUpdated"]), (0, 1, 0))
        self.assertEqual(self.patches("books"), [])
        self.assertEqual(self.patches("series"), [])
        self.assertEqual(self.remote_books, before_book)
        self.assertEqual(self.remote_series, before_series)
        self.assertFalse(self.service.repo.get(record.id).remote["hasLocalChanges"])

    def test_title_and_writer_only_changes_preserve_other_roles_links_and_series_values(self):
        self.seed("b1")
        self.seed("b2")
        self.call("komga_pull")
        record = self.records()["b1"]
        self.approve(record.id, title="新标题")
        result = self.call("komga_sync", ids=[record.id])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self.patches("books")[0], {"title": "新标题", "titleLock": True})
        self.assertEqual(self.patches("series"), [])
        self.approve(record.id, authors=["Pencil"])
        self.call("komga_sync", ids=[record.id])
        authors = self.remote_books["b1"]["metadata"]["authors"]
        self.assertIn({"name": "Pencil", "role": "penciller"}, authors)
        self.assertIn({"name": "Inker", "role": "inker"}, authors)
        self.assertIn({"name": "Pencil", "role": "writer"}, authors)
        self.assertEqual(len(self.remote_books["b1"]["metadata"]["links"]), 2)
        self.assertNotIn("notableEntities", self.patches("books")[-1])

    def test_shared_batch_change_uses_explicit_value_and_keeps_unmodified_age_genre_direction(self):
        self.seed("b1")
        self.seed("b2")
        self.call("komga_pull")
        identities = [record.id for record in self.records().values()]
        self.service.call("batch_edit", {"ids": identities, "changes": {"publisher": "新发行方", "publication_format": "合集"}})
        for identity in identities:
            self.approve(identity)
        result = self.call("komga_sync", ids=identities)
        self.assertEqual((result["updated"], result["seriesUpdated"]), (2, 1))
        self.assertEqual(self.patches("series")[0], {"publisher": "新发行方", "publisherLock": True})
        shared = self.remote_series["s"]["metadata"]
        self.assertEqual((shared["ageRating"], shared["genres"], shared["readingDirection"]), (17, ["奇幻"], "WEBTOON"))
        for payload in self.patches("books"):
            self.assertEqual(set(payload), {"notableEntities", "notableEntitiesLock", "tags", "tagsLock"})
            self.assertFalse(any(item["kind"] in ("年龄分级", "内容类型") for item in payload["notableEntities"]))

    def test_refresh_conflict_and_keep_local_rebase_preserve_only_actual_edits(self):
        self.seed()
        self.call("komga_pull")
        record = self.records()["b1"]
        self.service.call("save", {"id": record.id, "metadata": {"summary": "本机草稿"}})
        self.remote_books["b1"]["metadata"].update(title="另一个管理员的新标题", summary="远端简介")
        self.call("komga_pull")
        conflicted = self.service.repo.get(record.id)
        self.assertTrue(conflicted.remote["conflict"])
        self.assertEqual(conflicted.metadata.summary, "本机草稿")
        with self.assertRaises(KomgaError):
            self.approve(record.id)
        resolved = self.call("komga_resolve", id=record.id, resolution="keep_local")
        self.assertEqual(resolved["metadata"]["title"], "另一个管理员的新标题")
        self.assertEqual(resolved["metadata"]["summary"], "本机草稿")
        self.assertEqual(resolved["status"], "draft")
        self.approve(record.id)
        self.call("komga_sync", ids=[record.id])
        self.assertEqual(self.patches("books")[-1], {"summary": "本机草稿", "summaryLock": True})

    def test_use_remote_resolution_discards_local_edits_and_audits_previous_values(self):
        self.seed()
        self.call("komga_pull")
        record = self.records()["b1"]
        self.service.call("save", {"id": record.id, "metadata": {"title": "待舍弃本机标题"}})
        self.remote_books["b1"]["metadata"]["title"] = "远端最新标题"
        self.call("komga_pull")
        resolved = self.call("komga_resolve", id=record.id, resolution="use_remote")
        self.assertEqual(resolved["metadata"]["title"], "远端最新标题")
        self.assertFalse(resolved["remote"]["hasLocalChanges"])
        with self.service.repo.connect() as db:
            detail = db.execute("SELECT detail FROM events WHERE comic_id=? AND action='komga_resolve_use_remote'", (record.id,)).fetchone()[0]
        self.assertIn("待舍弃本机标题", detail)

    def test_missing_remote_record_is_retained_unavailable_and_wrong_scope_cannot_write(self):
        self.seed()
        self.call("komga_pull")
        record = self.records()["b1"]
        with self.assertRaises(KomgaError):
            self.service.call("approve", {"id": record.id, "metadata": {}, "url": self.url, "apiKey": self.key, "libraryId": "other"})
        self.remote_books.clear()
        self.call("komga_pull")
        self.assertTrue(self.service.repo.get(record.id).remote["unavailable"])
        with self.assertRaises(KomgaError):
            self.approve(record.id)
        self.assertEqual(self.patches("books"), [])

    def test_unrelated_progress_and_list_order_changes_do_not_cause_conflicts(self):
        self.seed()
        self.call("komga_pull")
        record = self.records()["b1"]
        self.service.call("save", {"id": record.id, "metadata": {"summary": "本机修改"}})
        self.remote_books["b1"]["readProgress"] = {"page": 99}
        self.remote_books["b1"]["metadata"]["authors"].reverse()
        self.remote_books["b1"]["metadata"]["lastModified"] = "new timestamp"
        self.call("komga_pull")
        self.assertFalse(self.service.repo.get(record.id).remote["conflict"])
        self.approve(record.id)

    def test_alias_label_change_keeps_other_aliases_and_finishes_without_pending_loop(self):
        self.seed()
        self.call("komga_pull")
        record = self.records()["b1"]
        self.approve(record.id, localized_series_label="custom", localized_series="新标签别名")
        response = self.call("komga_sync", ids=[record.id])
        self.assertEqual(response["seriesUpdated"], 1)
        aliases = self.remote_series["s"]["metadata"]["alternateTitles"]
        self.assertIn({"label": "en", "title": "English"}, aliases)
        self.assertIn({"label": "custom", "title": "新标签别名"}, aliases)
        current = self.service.repo.get(record.id)
        self.assertEqual(current.metadata.localized_series_label, "custom")
        self.assertFalse(current.remote["hasLocalChanges"])

    def test_post_write_concurrent_edit_is_a_conflict_and_never_silently_advances_baseline(self):
        self.seed()
        self.call("komga_pull")
        record = self.records()["b1"]
        self.approve(record.id, title="本机修改标题")
        self.after_patch = lambda path: self.remote_books["b1"]["metadata"].update(title="另一个管理员的新名")
        result = self.call("komga_sync", ids=[record.id])
        current = self.service.repo.get(record.id)
        self.assertTrue(current.remote["conflict"])
        self.assertEqual(current.status, "draft")
        self.assertEqual(current.metadata.title, "本机修改标题")
        self.assertTrue(any("冲突" in item for item in result["books"][0]["warnings"]))

    def test_server_float32_number_sort_rounding_does_not_create_a_false_conflict(self):
        self.seed()
        self.call("komga_pull")
        record = self.records()["b1"]
        self.approve(record.id, number="1.23456789")
        def round_server_number(path):
            if "/books/" in path:
                value = self.remote_books["b1"]["metadata"]["numberSort"]
                rounded = struct.unpack("!f", struct.pack("!f", value))[0]
                self.remote_books["b1"]["metadata"]["numberSort"] = float(format(rounded, ".8g"))
        self.after_patch = round_server_number
        result = self.call("komga_sync", ids=[record.id])
        current = self.service.repo.get(record.id)
        self.assertEqual(result["updated"], 1)
        self.assertFalse(current.remote["conflict"])
        self.assertFalse(current.remote["hasLocalChanges"])
        snapshot = deepcopy(self.service.repo.get_komga_source(record.id)["snapshot"])
        snapshot["book"]["metadata"]["numberSort"] = 10 ** 1000
        self.assertEqual(len(snapshot_fingerprint(snapshot)), 64)
        self.assertEqual(snapshot["book"]["metadata"]["numberSort"], 10 ** 1000)

    def test_age_change_clears_derived_correction_copies_but_keeps_local_unchecked_flag(self):
        self.seed(entities=[{"kind": "年龄分级", "name": "R18+"}, {"kind": "修正状态", "name": "无修正"}, {"kind": "team", "name": "保留"}])
        self.remote_books["b1"]["metadata"]["tags"].append("修正状态:无修正")
        self.call("komga_pull")
        record = self.records()["b1"]
        self.approve(record.id, age_rating="Everyone")
        self.call("komga_sync", ids=[record.id])
        payload = self.patches("books")[0]
        self.assertFalse(any(item["kind"] == "修正状态" for item in payload["notableEntities"]))
        self.assertNotIn("修正状态:无修正", payload["tags"])
        self.assertTrue(self.service.repo.get(record.id).metadata.uncensored)
        self.assertFalse(self.service.repo.get(record.id).remote["hasLocalChanges"])

    def test_remote_remove_export_and_organization_changes_are_rejected(self):
        self.seed(oneshot=True)
        self.call("komga_pull")
        record = self.records()["b1"]
        for method, params in (("remove", {"ids": [record.id]}), ("plan", {"ids": [record.id], "outputRoot": str(self.root / "out")}),
                               ("save", {"id": record.id, "metadata": {"collection_mode": "series"}}),
                               ("batch_edit", {"ids": [record.id], "changes": {"series": "不适用的OneShot系列"}})):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.service.call(method, params)


if __name__ == "__main__":
    unittest.main()
