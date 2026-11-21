from collections import OrderedDict
import json
from threading import Event
import unittest
from unittest.mock import patch

from comic_manage.model_catalog import discover_models
from comic_manage.model_scheduler import estimated_input_tokens, wait_for_model_slot
from comic_manage.sources import Cancelled


class ModelRuntimeTests(unittest.TestCase):
    def test_discovery_keeps_supported_models_and_pages_same_origin(self):
        replies = [
            {"models": [
                {"name": "models/gemini-3-flash", "displayName": "Gemini Flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]},
            ], "nextPageToken": "fixture cursor"},
            {"models": [{"name": "models/gemini-3-flash"}, {"name": "models/gemini-3.1-pro"}]},
        ]
        profile = {"provider": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta", "model": "catalog"}
        with patch("comic_manage.model_catalog._read_response", side_effect=[json.dumps(reply).encode() for reply in replies]) as request_model:
            found = discover_models(profile, "synthetic-api-key")
        self.assertEqual(found["models"], [{"slug": "gemini-3-flash", "name": "Gemini Flash"},
                                           {"slug": "gemini-3.1-pro", "name": "gemini-3.1-pro"}])
        requests = [call.args[0] for call in request_model.call_args_list]
        self.assertTrue(all(req.get_method() == "GET" for req in requests))
        self.assertTrue(all(req.get_header("X-goog-api-key") == "synthetic-api-key" for req in requests))
        self.assertIn("pageToken=fixture+cursor", requests[1].full_url)

    def test_model_discovery_rejects_credential_echo(self):
        profile = {"provider": "openai", "base_url": "https://example.test/v1", "model": "catalog"}
        payload = b'{"data":[{"id":"text","display_name":"secret-key"},{"id":"text2"}]}'
        with patch("comic_manage.model_catalog._read_response", return_value=payload):
            result = discover_models(profile, "secret-key")
        self.assertEqual(result["models"], [{"slug": "text2", "name": "text2"}])

    def test_active_request_count_is_limited_per_minute_and_cancellation_works(self):
        profile = {"provider": "ollama", "base_url": "http://localhost:11434", "model": "fixture", "options": {"rpm": 1}}
        clock = [5.0]

        def advance(seconds):
            clock[0] += seconds

        with (
            patch("comic_manage.model_scheduler._RESERVATIONS", OrderedDict()),
            patch("comic_manage.model_scheduler.monotonic", side_effect=lambda: clock[0]),
            patch("comic_manage.model_scheduler.sleep", side_effect=advance),
        ):
            wait_for_model_slot(profile)
            wait_for_model_slot({**profile, "model": "independent"})
            self.assertEqual(clock[0], 5)
            wait_for_model_slot(profile)
            self.assertEqual(clock[0], 65)
            cancelled = Event()
            cancelled.set()
            with self.assertRaises(Cancelled):
                wait_for_model_slot(profile, cancelled)

    def test_budget_estimate_includes_instructions_and_framing(self):
        self.assertGreater(estimated_input_tokens("上下文", "测试任务"), estimated_input_tokens("", "测试任务"))
