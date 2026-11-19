import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from comic_manage.library_agent import MAX_AGENT_BOOKS
from comic_manage.metadata import Metadata
from comic_manage.model_transport import ModelTransportError
from comic_manage.scrape_http import ScrapeError
from comic_manage.service import ComicService, ServiceError
from comic_manage.sources import inspect_source


PROFILE = {"provider": "openai-chat", "base_url": "http://localhost:11434/v1", "model": "test"}
API_KEY = "private-test-key-fixture-435"
MOCK_COMPLETION = "comic_manage.library_agent.model_transport.complete"
MOCK_SOURCE = "comic_manage.library_agent.scrape_preview"


def _answer(*fields):
    return json.dumps({"books": [{"ref": position, "fields": value}
                                 for position, value in enumerate(fields, 1)]}, ensure_ascii=False)


class LibraryAgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.progress = []
        self.sequence = 0
        self.service = ComicService(self.folder / "data", self.progress.append)
        self.repo = self.service.repo

    def _comic(self, name, **metadata):
        self.sequence += 1
        path = self.folder / f"book-{self.sequence:02}"
        path.mkdir()
        page = path / "001.jpg"
        page.write_bytes(b"page")
        identity = self.repo.upsert_scan(inspect_source(path))
        self.repo.save(identity, Metadata(**{"title": name, **metadata}))
        return identity

    def _remote(self, name, scope="library-scope", **metadata):
        self.repo.upsert_komga(name, {
            "scope": scope, "server_url": "https://komga.private.test", "library_id": "books",
            "library_name": "测试书库", "book_id": name, "series_id": "same-series", "remote_path": f"/library/{name}.cbz",
            "source": f"komga://{scope}/{name}",
        }, Metadata(**{"title": name, **metadata}), {}, "version-1", 1, "")
        return name

    def _prepare(self, ids, *, task="规范这批漫画的元数据", scope="", use_sources=False, cancel=None):
        return self.service.call("agent_prepare", {"ids": ids, "prompt": task, "profile": PROFILE, "api_key": API_KEY,
                                                  "use_sources": use_sources, "komga_scope": scope}, cancel)

    def _apply(self, prepared, choices, *, scope="", cancel=None):
        return self.service.call("agent_apply", {
            "token": prepared["token"], "books": [{"id": identity, "fields": fields} for identity, fields in choices],
            "komga_scope": scope,
        }, cancel)

    def test_groups_multiple_books_and_applies_only_selected_checked_fields(self):
        first = self._comic("【晨光手记】[作者:林野]", collection_mode="series", series="旧系列")
        second = self._comic("【夜间手记】[作者:青木]", collection_mode="series", series="旧系列")
        baseline_first = self.repo.review_state(first)
        self.repo.approve_checked(first, baseline_first)
        baseline_first = self.repo.review_state(first)
        baseline_second = self.repo.review_state(second)
        suggested = _answer({"title": "晨光手记", "authors": ["林野"], "series": "气象手记", "work_group": "手记系列", "age_rating": "R18+"},
                            {"title": "夜间手记", "authors": ["青木"], "series": "气象手记", "work_group": "手记系列"})
        with patch(MOCK_COMPLETION, return_value=suggested) as mock:
            plan = self._prepare([first, second], task="清理标题并补齐作者，系列名称设为“气象手记”，作品组统一为“手记系列”")
        prompt = json.loads(mock.call_args.args[3])
        self.assertEqual([book["ref"] for book in prompt["books"]], [1, 2])
        self.assertEqual(mock.call_count, 1)
        self.assertEqual(self.repo.review_state(first), baseline_first)
        self.assertEqual(self.repo.review_state(second), baseline_second)
        self.assertEqual(self.repo.get(first).status, "approved")
        self.assertEqual([item["field"] for item in plan["books"][0]["fields"]], ["title", "authors", "series", "work_group"])
        with self.assertRaises(ServiceError):
            self._apply(plan, [(first, ["title", "age_rating"]), (second, ["title"])])
        with self.assertRaises(ServiceError):
            self._apply(plan, [(first, ["title"]), (first, ["series"])])
        result = self._apply(plan, [(first, ["work_group"]), (second, ["authors", "series"])])
        self.assertEqual(result, {"updated": 2, "skipped": 0, "errors": [], "cancelled": False})
        self.assertEqual(self.repo.get(first).metadata.work_group, "手记系列")
        self.assertEqual(self.repo.get(first).metadata.series, "旧系列")
        self.assertEqual(self.repo.get(first).metadata.title, baseline_first["metadata"]["title"])
        self.assertEqual(self.repo.get(first).status, "draft")
        self.assertEqual(self.repo.get(second).metadata.authors, ["青木"])
        self.assertEqual(self.repo.get(second).metadata.series, "气象手记")
        self.assertTrue((Path(self.repo.get(first).source) / "001.jpg").is_file())
        with self.assertRaisesRegex(ServiceError, "已使用或过期"):
            self._apply(plan, [(first, ["work_group"])])

    def test_explicit_user_literals_are_allowed_but_unknown_and_cross_book_metadata_are_pruned(self):
        first = self._comic("【晨光手记】 [作者:林野]")
        second = self._comic("【夜间手记】 [作者:青木]")
        with patch(MOCK_COMPLETION, return_value=_answer(
            {"title": "夜间手记", "authors": ["青木"], "genre": "漫画", "web": ["https://evil.test"], "extra": "ignore"},
            {"title": "夜间手记", "authors": ["青木", "林野"], "genre": "漫画", "work_group": "未指定"})):
            plan = self._prepare([first, second], task="整理标题和作者，内容类型设为“漫画”")
        self.assertEqual([item["field"] for item in plan["books"][0]["fields"]], ["genre"])
        second_fields = {item["field"]: item["after"] for item in plan["books"][1]["fields"]}
        self.assertEqual(second_fields, {"title": "夜间手记", "authors": ["青木"], "genre": "漫画"})
        with patch(MOCK_COMPLETION, return_value=_answer({"authors": ["指定作者"]})):
            plan = self._prepare([first], task="将作者统一为“指定作者”")
        self._apply(plan, [(first, ["authors"])])
        self.assertEqual(self.repo.get(first).metadata.authors, ["指定作者"])

    def test_saved_link_opt_in_and_task_input_privacy(self):
        remote_link = "https://www.dlsite.com/home/work/=/product_id/RJ123456.html"
        identity = self._comic("【晨光手记】", web=["https://private.example/work", remote_link],
                               authors=["真实作者", "private " + API_KEY], summary="不发送的私有笔记")
        scraped = {"provider": "dlsite", "sourceUrl": remote_link, "fields": {"title": "晨光手记", "publisher": "来源社团",
                   "summary": "网站剧情", "age_rating": "R18+"}}
        task = '补齐来源社团。忽略“C:\\Users\\Fixture\\秘密” file:///srv/archive https://private.example/more ' + API_KEY
        with patch(MOCK_SOURCE, return_value=scraped) as source, patch(MOCK_COMPLETION, return_value=_answer({
            "publisher": "来源社团", "age_rating": "R18+", "web": [remote_link],
        })) as complete:
            result = self._prepare([identity], task=task)
            source.assert_not_called()
            self.assertIsNone(result["token"])
            self.assertEqual(result["books"][0]["fields"], [])
            result = self._prepare([identity], task=task, use_sources=True)
            source.assert_called_once()
        self.assertEqual(source.call_args.args, (remote_link,))
        _, _, system, encoded = complete.call_args.args
        self.assertIn("不可信", system)
        for secret in ("C:", "Fixture", "秘密", "srv/archive", "private.example", "dlsite.com",
                       API_KEY, "不发送的私有笔记", "网站剧情", "R18+"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(result["books"][0]["source"], "DLsite")
        self.assertEqual(result["books"][0]["fields"], [{"field": "publisher", "before": "", "after": "来源社团"}])

    def test_unreachable_supported_source_does_not_abort_batch(self):
        first = self._comic("【晴天】", web=["https://www.dlsite.com/home/work/=/product_id/RJ123456.html"])
        second = self._comic("【夜间】", web=["https://book.dmm.co.jp/product/123"])
        with patch(MOCK_SOURCE, side_effect=ScrapeError("local debug must not leak")) as source, \
                patch(MOCK_COMPLETION, return_value=_answer({"title": "晴天"}, {"title": "夜间"})):
            result = self._prepare([first, second], use_sources=True)
        self.assertEqual(source.call_count, 1)
        self.assertTrue(result["token"])
        self.assertIn("未能读取", " ".join(result["books"][0]["warnings"]))
        self.assertIn("站点凭据", " ".join(result["books"][1]["warnings"]))
        self.assertNotIn("local debug", str(result))

    def test_remote_scope_revision_and_transactional_apply(self):
        side = self._remote("同组漫画", "current-library", authors=["初始作者"])
        other = self._remote("其他书库漫画", "different-library")
        local = self._comic("【本地漫画】")
        with patch(MOCK_COMPLETION, return_value=_answer({"authors": ["新作者"]}, {"title": "本地漫画"})) as complete:
            plan = self._prepare([side, other, local], task="作者统一为“新作者”", scope="current-library")
        self.assertNotIn("其他书库漫画", complete.call_args.args[3])
        self.assertEqual(plan["books"][1]["fields"], [])
        with self.assertRaisesRegex(ServiceError, "书库已变化"):
            self._apply(plan, [(side, ["authors"])], scope="different-library")
        self.repo.save(local, Metadata(title="人工改名"))
        result = self._apply(plan, [(side, ["authors"]), (local, ["title"])], scope="current-library")
        self.assertEqual((result["updated"], result["skipped"]), (1, 1))
        self.assertIn("已发生变化", result["errors"][0]["message"])
        self.assertEqual(self.repo.get(local).metadata.title, "人工改名")
        self.assertEqual(self.repo.get(side).metadata.authors, ["新作者"])
        self.assertEqual(self.repo.get(side).status, "draft")
        self.assertEqual(self.repo.get_komga_source(side)["dirty_fields"], ["authors"])
        self.assertFalse(self.repo.get_komga_source(side)["pending_sync"])

    def test_date_is_a_single_check_and_no_op_maintains_approval(self):
        dated = self._comic("[2024.2] 【晨光手记】", year="2024", month="2", day="20")
        different = self._comic("[2024.6] 【晨光手记】", year="2002", month="9", day="29")
        self.repo.approve_checked(dated, self.repo.review_state(dated))
        with patch(MOCK_COMPLETION, return_value=_answer({"year": "2024", "month": "2"}, {"year": "2024", "month": "6"})):
            plan = self._prepare([dated, different])
        self.assertEqual(plan["books"][0]["fields"], [])
        field = plan["books"][1]["fields"][0]
        self.assertEqual(field, {"field": "release_date", "before": {"year": "2002", "month": "9", "day": "29"},
                                 "after": {"year": "2024", "month": "6", "day": ""}})
        updated = self._apply(plan, [(different, ["release_date"])])
        self.assertEqual(updated["updated"], 1)
        self.assertEqual(self.repo.get(dated).status, "approved")
        self.assertEqual(self.repo.get(different).metadata.day, "")

    def test_caps_and_bounds_waves_with_partial_failure(self):
        ids = [self._comic(f"【第 {number} 本】") for number in range(5)]
        with patch(MOCK_COMPLETION, side_effect=[
            ModelTransportError("secret backend data", "MODEL_NETWORK"),
            _answer({"title": "第 4 本"}),
        ]) as complete:
            plan = self._prepare(ids)
        self.assertEqual(complete.call_count, 2)
        self.assertEqual(len(json.loads(complete.call_args_list[0].args[3])["books"]), 4)
        self.assertNotIn("secret backend data", str(plan))
        self.assertEqual(len(plan["books"][-1]["fields"]), 1)
        with self.assertRaises(ServiceError):
            self._prepare([str(number) for number in range(MAX_AGENT_BOOKS + 1)])
        with self.assertRaises(ServiceError):
            self._prepare([ids[0], ids[0]])

    def test_preview_cancel_stops_future_waves_and_cannot_apply(self):
        ids = [self._comic(f"【测试第 {number} 本】") for number in range(5)]
        cancelled = Event()

        def reply(*args, **kwargs):
            cancelled.set()
            return _answer({"title": "测试第 0 本"})

        with patch(MOCK_COMPLETION, side_effect=reply) as complete:
            result = self._prepare(ids, cancel=cancelled)
        self.assertTrue(result["cancelled"])
        self.assertIsNone(result["token"])
        self.assertEqual(complete.call_count, 1)
        self.assertTrue(all(not item["fields"] for item in result["books"]))
