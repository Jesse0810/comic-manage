import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from unittest import TestCase
from unittest.mock import patch

from comic_manage import model_transport as models
from comic_manage.sources import Cancelled


@contextmanager
def model_server(response, *, status=200, media_type="application/json", headers=None):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append((self.path, self.headers, json.loads(body)))
            raw = response if isinstance(response, bytes) else json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(raw)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            calls.append((self.path, self.headers, {}))
            self.send_error(404)

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", calls
        finally:
            server.shutdown()
            thread.join()


def profile(provider, base_url, model="sample-model"):
    return {"provider": provider, "base_url": base_url, "model": model}


class ModelTransportTests(TestCase):
    def test_normalizes_compatible_full_url_without_retaining_api_key(self):
        settings = models.validate_profile({
            **profile("openai", " https://API.OpenAI.com:443/v1/chat/completions/ ", " provider/engine "),
            "api_key": "mock-secret",
            "name": "My model",
        })
        self.assertEqual(settings, profile("openai", "https://api.openai.com/v1", "provider/engine"))
        settings = models.validate_profile(profile("gemini", "https://generativelanguage.googleapis.com", "models/gemini"))
        self.assertEqual(settings, profile("gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini"))

    def test_supports_lan_endpoints_under_http(self):
        for url in ("http://127.0.0.2:9000", "http://[::1]:9000", "http://192.168.0.1", "http://192.168.0.1:11434/ollama",
                    "http://host.local:11434", "http://nas.lan:11434", "http://[fd00::f]:1234"):
            with self.subTest(url=url):
                self.assertTrue(models.is_local_endpoint(url))
                self.assertEqual(models.validate_profile(profile("ollama", url))["base_url"], url)
        self.assertFalse(models.is_local_endpoint("https://models.example.com"))

    def test_rejects_unsafe_or_ambiguous_addresses(self):
        for url in ("http://models.example.com", "http://203.0.113.25", "file:///C:/models",
                    "https://Bearer:mock-secret@host.example", "https://models.example?apiKey=mock-secret",
                    "https://models.example#mock-secret", "https://models.example:65536", "http://localhost:",
                    "http://[::bad", "https://models.example//../v1", "https://models.example/%2e%2e",
                    "https://models.example\\.internal", "https://models.example/v1?"):
            with self.subTest(url=url), self.assertRaises(models.ModelTransportError) as raised:
                models.validate_profile(profile("openai", url))
            self.assertEqual(raised.exception.code, "MODEL_CONFIG")
            self.assertNotIn("mock-secret", str(raised.exception))
        with self.assertRaises(models.ModelTransportError):
            models.validate_profile(profile("gemini", "https://models.example", "other/path?secret"))

    def test_openai_compatible_completions(self):
        answer = {"choices": [{"finish_reason": "stop", "message": {"content": ' {"title":"春日"} '}}]}
        with model_server(answer) as (base_url, calls):
            with patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
                text = models.complete(profile("openai", base_url + "/base/v1/chat/completions"), "mock-secret", "仅输出 JSON", "春日")
        self.assertEqual(text, '{"title":"春日"}')
        self.assertEqual(len(calls), 1)
        path, headers, body = calls[0]
        self.assertEqual(path, "/base/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer mock-secret")
        self.assertEqual(body["model"], "sample-model")
        self.assertEqual(body["messages"], [{"role": "system", "content": "仅输出 JSON"}, {"role": "user", "content": "春日"}])
        self.assertFalse(body["stream"])
        self.assertNotIn("tools", body)

    def test_openai_responses_outputs_final_assistant_text_only(self):
        answer = {"status": "completed", "output": [
            {"type": "reasoning", "content": [{"type": "output_text", "text": "hidden thinking"}]},
            {"type": "function_call", "name": "ignored", "arguments": "{}"},
            {"type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": "result"}]},
        ]}
        with model_server(answer) as (base_url, calls):
            self.assertEqual(models.complete(profile("openai-responses", base_url + "/v1/responses"), "", "rules", "task"), "result")
        path, headers, body = calls[0]
        self.assertEqual(path, "/v1/responses")
        self.assertIsNone(headers["Authorization"])
        self.assertEqual(body["instructions"], "rules")
        self.assertEqual(body["input"], "task")
        self.assertFalse(body["store"])
        self.assertFalse(body["stream"])
        self.assertNotIn("tools", body)

    def test_ollama_responses_do_not_read_thinking(self):
        answer = {"done": True, "done_reason": "stop", "message": {"content": "result", "thinking": "hidden"}}
        with model_server(answer) as (base_url, calls):
            self.assertEqual(models.complete(profile("ollama", base_url + "/proxy/api"), "", "rules", "task"), "result")
        path, headers, body = calls[0]
        self.assertEqual(path, "/proxy/api/chat")
        self.assertIsNone(headers["Authorization"])
        self.assertFalse(body["stream"])

    def test_anthropic_uses_version_and_key_header(self):
        answer = {"stop_reason": "end_turn", "content": [
            {"type": "thinking", "text": "hidden"}, {"type": "text", "text": "result"},
        ]}
        with model_server(answer) as (base_url, calls):
            self.assertEqual(models.complete(profile("anthropic", base_url + "/v1"), "mock-secret", "rules", "task"), "result")
        path, headers, body = calls[0]
        self.assertEqual(path, "/v1/messages")
        self.assertEqual(headers["x-api-key"], "mock-secret")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(body["system"], "rules")
        self.assertEqual(body["messages"], [{"role": "user", "content": "task"}])

    def test_gemini_uses_key_header_and_final_text_only(self):
        answer = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"thought": True, "text": "not final"}, {"text": "result"},
        ]}}]}
        with model_server(answer) as (base_url, calls):
            settings = profile("gemini", base_url + "/v1beta/models/sample-model:generateContent")
            self.assertEqual(models.complete(settings, "mock-secret", "rules", "task"), "result")
        path, headers, body = calls[0]
        self.assertEqual(path, "/v1beta/models/sample-model:generateContent")
        self.assertEqual(headers["x-goog-api-key"], "mock-secret")
        self.assertEqual(body["systemInstruction"]["parts"], [{"text": "rules"}])
        self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "task"}]}])

    def test_refuses_to_follow_redirects_with_credentials(self):
        with model_server({}, status=307, headers={"Location": "/other"}) as (base_url, calls):
            with self.assertRaises(models.ModelTransportError) as raised:
                models.complete(profile("openai", base_url), "mock-secret", "rules", "task")
        self.assertEqual(len(calls), 1)
        self.assertEqual(raised.exception.code, "MODEL_CONFIG")
        self.assertNotIn("mock-secret", str(raised.exception))

    def test_http_errors_omit_raw_response(self):
        with model_server({"error": "mock-secret"}, status=429) as (base_url, _):
            with self.assertRaises(models.ModelTransportError) as raised:
                models.complete(profile("ollama", base_url), "mock-secret", "rules", "task")
        self.assertEqual(raised.exception.code, "MODEL_RATE_LIMIT")
        self.assertNotIn("mock-secret", str(raised.exception))

    def test_content_limits_and_invalid_json(self):
        cases = (
            (b"<html>mock-secret</html>", "text/html"),
            (b"not json mock-secret", "application/json"),
            ({"error": "mock-secret"}, "application/json"),
        )
        for response, media_type in cases:
            with self.subTest(media_type=media_type), model_server(response, media_type=media_type) as (base_url, _):
                with self.assertRaises(models.ModelTransportError) as raised:
                    models.complete(profile("ollama", base_url), "", "", "task")
                self.assertEqual(raised.exception.code, "MODEL_RESPONSE")
                self.assertNotIn("mock-secret", str(raised.exception))
        with model_server(b'"1234567890"') as (base_url, _), patch.object(models, "MAX_RESPONSE_BYTES", 5):
            with self.assertRaises(models.ModelTransportError) as raised:
                models.complete(profile("ollama", base_url), "", "", "task")
            self.assertEqual(raised.exception.code, "MODEL_RESPONSE")

    def test_truncated_or_refused_completions_are_not_reused(self):
        for provider, data in (
            ("openai", {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}),
            ("openai-responses", {"status": "incomplete", "output_text": "partial"}),
            ("ollama", {"done": False, "message": {"content": "partial"}}),
            ("anthropic", {"stop_reason": "refusal", "content": [{"type": "text", "text": "refused"}]}),
            ("gemini", {"candidates": [{"finishReason": "SAFETY", "content": {"parts": [{"text": "refused"}]}}]}),
        ):
            with self.subTest(provider=provider), model_server(data) as (base_url, _):
                with self.assertRaises(models.ModelTransportError) as raised:
                    models.complete(profile(provider, base_url), "", "", "task")
                self.assertEqual(raised.exception.code, "MODEL_RESPONSE")

    def test_official_profile_missing_key_and_cancelled_stay_off_network(self):
        cancelled = Event()
        cancelled.set()
        with patch.object(models, "build_opener") as opener:
            with self.assertRaises(models.ModelTransportError) as raised:
                models.complete(profile("openai", "https://api.openai.com"), "", "", "task")
            self.assertEqual(raised.exception.code, "MODEL_AUTH")
            with self.assertRaises(models.ModelTransportError):
                models.complete(profile("ollama", "http://localhost"), "key\nprivacy", "", "task")
            with self.assertRaises(Cancelled):
                models.complete(profile("ollama", "http://localhost"), "", "", "task", cancelled)
            opener.assert_not_called()
