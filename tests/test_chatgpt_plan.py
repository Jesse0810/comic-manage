"""Mocked tests of the fixed ChatGPT plan Responses transport; no real accounts."""
from __future__ import annotations

from email.message import Message
from io import BytesIO
import json
from threading import Event
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler

from comic_manage.chatgpt_plan import RESPONSES_URL, complete_chatgpt_plan
from comic_manage.model_transport import ModelTransportError, _NoRedirects
from comic_manage.sources import Cancelled

TOKEN = "fake-oauth-access.token.for-response-tests"
MODEL = "test-model-slug"
SYSTEM = "仅保留有依据的字段"
PROMPT = "合成文本"


def frame(kind, payload, *, newline="\n", event=None):
    body = json.dumps({"type": kind, **payload}, ensure_ascii=False)
    return f"event: {event or kind}{newline}data: {body}{newline}{newline}".encode("utf-8")


def delta(text, **position):
    return frame("response.output_text.delta", {"delta": text, **position}, newline="\r\n")


def done(text, **position):
    return frame("response.output_text.done", {"text": text, **position})


def completed(*content, output=True):
    response = {"status": "completed", "error": None}
    if output:
        response["output"] = [{"type": "reasoning"}, {
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": item} for item in content],
        }]
    return frame("response.completed", {"response": response})


class FragmentedStream(BytesIO):
    def __init__(self, data, media_type="text/event-stream; charset=utf-8", cancelled=None):
        super().__init__(data)
        self.headers = Message()
        if media_type is not None:
            self.headers["Content-Type"] = media_type
        self.cancelled = cancelled

    def read1(self, size):
        if self.cancelled is not None:
            self.cancelled.set()
        return self.read(min(13, size))


class ChatGptPlanTests(unittest.TestCase):
    def call(self, opener, token=TOKEN, **params):
        with patch("comic_manage.chatgpt_plan.build_opener", return_value=opener):
            return complete_chatgpt_plan(MODEL, token, SYSTEM, PROMPT, **params)

    def with_stream(self, data, **stream_args):
        with patch("comic_manage.chatgpt_plan.build_opener") as factory:
            factory.return_value.open.return_value = FragmentedStream(data, **stream_args)
            return complete_chatgpt_plan(MODEL, TOKEN, SYSTEM, PROMPT)

    def test_fixed_endpoint_and_complete_stream_with_multibyte_chunks(self):
        text = '{"title":"春の旅"}'
        stream = FragmentedStream(b": heartbeat\n\n" + delta('{"title":"') + delta('春の旅"}') + done(text) + completed(text))
        with patch("comic_manage.chatgpt_plan.build_opener") as factory:
            factory.return_value.open.return_value = stream
            self.assertEqual(complete_chatgpt_plan(MODEL, TOKEN, SYSTEM, PROMPT), text)
        handlers = factory.call_args.args
        self.assertIsInstance(handlers[0], ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], _NoRedirects)
        request = factory.return_value.open.call_args.args[0]
        self.assertEqual((request.full_url, request.method), (RESPONSES_URL, "POST"))
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(request.get_header("Accept"), "text/event-stream")
        self.assertEqual(json.loads(request.data), {
            "model": MODEL, "instructions": SYSTEM, "input": [{"role": "user", "content": PROMPT}],
            "store": False, "stream": True,
        })

    def test_plan_thinking_and_speed_are_sent_to_official_responses(self):
        for speed, tier in (("standard", "default"), ("fast", "priority"), ("ultrafast", "ultrafast")):
            with self.subTest(speed=speed), patch("comic_manage.chatgpt_plan.build_opener") as factory:
                factory.return_value.open.return_value = FragmentedStream(completed("ok"))
                self.assertEqual(complete_chatgpt_plan(MODEL, TOKEN, SYSTEM, PROMPT,
                    options={"reasoning": "max", "speed": speed, "timeout_seconds": 125, "rpm": 0}), "ok")
                request = factory.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, RESPONSES_URL)
                self.assertEqual(factory.return_value.open.call_args.kwargs["timeout"], 125)
                self.assertEqual(json.loads(request.data), {
                    "model": MODEL, "instructions": SYSTEM, "input": [{"role": "user", "content": PROMPT}],
                    "store": False, "stream": True, "reasoning": {"effort": "max"}, "service_tier": tier,
                })

    def test_plan_forbidden_generation_fields_are_rejected_before_network(self):
        with patch("comic_manage.chatgpt_plan.build_opener") as factory:
            for options in ({"output_tokens": 2000}, {"temperature": .2}, {"top_p": .8}, {"top_k": 12}):
                with self.subTest(options=options), self.assertRaises(ModelTransportError) as raised:
                    complete_chatgpt_plan(MODEL, TOKEN, SYSTEM, PROMPT, options=options)
                self.assertEqual(raised.exception.code, "MODEL_CONFIG")
        factory.assert_not_called()

    def test_distinct_text_parts_keep_order_and_reasoning_is_ignored(self):
        self.assertEqual(self.with_stream(delta("后", output_index=2) + delta("前", output_index=1) + completed("前", "后")), "前\n后")
        self.assertEqual(self.with_stream(completed("完整文本")), "完整文本")
        self.assertEqual(self.with_stream(delta("仅增量") + completed(output=False)), "仅增量")

    def test_incomplete_or_interrupted_reply_cannot_return_partial_text(self):
        for terminal in (b"", b"data: [DONE]\n\n", frame("response.incomplete", {"response": {"status": "incomplete"}}),
                         frame("response.completed", {"response": {"status": "failed"}})):
            with self.subTest(terminal=terminal), self.assertRaises(ModelTransportError) as error:
                self.with_stream(delta("看似完整的文本") + terminal)
            self.assertEqual(error.exception.code, "MODEL_RESPONSE")

    def test_scope_limit_can_arrive_after_beginning_and_never_leaks_raw_payload(self):
        failure = frame("response.failed", {"response": {"error": {
            "code": "subscription_sharing_usage_limit_exceeded", "message": TOKEN + " private provider text"
        }}})
        with self.assertRaises(ModelTransportError) as error:
            self.with_stream(delta("部分结果") + failure)
        self.assertEqual(error.exception.code, "MODEL_CHATGPT_USAGE_LIMIT")
        self.assertNotIn(TOKEN, str(error.exception))

    def test_http_error_codes_are_classified_without_echoing_bodies(self):
        for status, body, expected in (
                (401, {"detail": TOKEN}, "MODEL_AUTH"),
                (403, {"error": {"code": "subscription_sharing_user_not_eligible", "message": TOKEN}}, "MODEL_CHATGPT_INELIGIBLE"),
                (503, {"error": {"code": "subscription_sharing_usage_unavailable", "message": TOKEN}}, "MODEL_NETWORK"),
                (302, {"detail": TOKEN}, "MODEL_HTTP")):
            with self.subTest(status=status), patch("comic_manage.chatgpt_plan.build_opener") as factory:
                headers = Message()
                headers["Location"] = "https://never-follow.test/"
                factory.return_value.open.side_effect = HTTPError(RESPONSES_URL, status, TOKEN, headers,
                                                                    BytesIO(json.dumps(body).encode()))
                with self.assertRaises(ModelTransportError) as error:
                    complete_chatgpt_plan(MODEL, TOKEN, SYSTEM, PROMPT)
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn(TOKEN, str(error.exception))
                factory.return_value.open.assert_called_once()

    def test_malformed_refused_mismatched_and_tool_only_events_stop(self):
        unexpected = {"response": {"status": "completed", "output": [{"type": "function_call"}]}}
        for data in (
                b"data: invalid-json\n\n", frame("response.output_text.delta", {"delta": 123}),
                frame("response.output_text.delta", {"delta": "文本"}, event="other"),
                frame("response.refusal.done", {"refusal": "拒绝原因"}) + completed("文本"),
                delta("wrong") + completed("正文"),
                delta("正文") + done("different") + completed("正文"),
                frame("response.completed", unexpected)):
            with self.subTest(data=data), self.assertRaises(ModelTransportError) as error:
                self.with_stream(data)
            self.assertEqual(error.exception.code, "MODEL_RESPONSE")

    def test_cancel_and_invalid_credentials_do_not_open_connection(self):
        cancelled = Event()
        cancelled.set()
        with patch("comic_manage.chatgpt_plan.build_opener") as factory:
            with self.assertRaises(Cancelled):
                complete_chatgpt_plan(MODEL, TOKEN, SYSTEM, PROMPT, cancelled)
            with self.assertRaises(ModelTransportError):
                complete_chatgpt_plan(MODEL, TOKEN + "\nforged-header", SYSTEM, PROMPT)
            with self.assertRaises(ModelTransportError):
                complete_chatgpt_plan("https://other.test", TOKEN, SYSTEM, PROMPT)
            factory.assert_not_called()

    def test_cancel_during_stream_drops_result(self):
        cancelled = Event()
        with patch("comic_manage.chatgpt_plan.build_opener") as factory:
            factory.return_value.open.return_value = FragmentedStream(completed("正文"), cancelled=cancelled)
            with self.assertRaises(Cancelled):
                complete_chatgpt_plan(MODEL, TOKEN, SYSTEM, PROMPT, cancelled)

    def test_valid_sse_may_omit_content_type(self):
        text = '{"status":"ok"}'
        self.assertEqual(self.with_stream(delta(text) + completed(text), media_type=None), text)

    def test_missing_content_type_still_requires_completed_sse(self):
        for payload in (b'{"status":"ok"}', delta('{"status":"ok"}')):
            with self.subTest(payload=payload), self.assertRaises(ModelTransportError) as error:
                self.with_stream(payload, media_type=None)
            self.assertEqual(error.exception.code, "MODEL_RESPONSE")

    def test_bounds_sse_wire_and_output_text_and_rejects_other_content_types(self):
        with patch("comic_manage.chatgpt_plan.MAX_RESPONSE_BYTES", 50):
            with self.assertRaisesRegex(ModelTransportError, "过大"):
                self.with_stream(delta("测试") + completed("测试"))
        with patch("comic_manage.chatgpt_plan.MAX_OUTPUT_CHARS", 3):
            with self.assertRaisesRegex(ModelTransportError, "过长"):
                self.with_stream(delta("测试正文") + completed("测试正文"))
        with self.assertRaises(ModelTransportError):
            self.with_stream(completed("文本"), media_type="application/json")

    def test_network_errors_and_secret_echo_return_safe_errors(self):
        with patch("comic_manage.chatgpt_plan.build_opener") as factory:
            factory.return_value.open.side_effect = URLError(TOKEN)
            with self.assertRaises(ModelTransportError) as error:
                complete_chatgpt_plan(MODEL, TOKEN, SYSTEM, PROMPT)
            self.assertEqual(error.exception.code, "MODEL_NETWORK")
            self.assertNotIn(TOKEN, str(error.exception))
        with self.assertRaises(ModelTransportError) as error:
            self.with_stream(delta(TOKEN) + completed(TOKEN))
        self.assertNotIn(TOKEN, str(error.exception))


if __name__ == "__main__":
    unittest.main()
