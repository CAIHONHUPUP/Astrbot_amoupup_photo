# -*- coding: utf-8 -*-
"""custom_endpoint 回归套件拆分母文件（断言与原文件逐字一致）。"""
import base64
import json
import threading
import types
import unittest

from _custom_endpoint_helpers import (ChainRunResult, FakeClientSession, OmniDrawPlugin, PLUGIN_DIR, PluginConfig, _long_b64, _normalize_api_type, extract_error_message, extract_image_url_from_response, is_complete_endpoint_url, main_module, summarize_payload_for_log, summarize_text_for_log, summarize_url_for_log)  # noqa: F401
from _custom_endpoint_helpers import _normalize_api_type  # noqa: F401


class CustomEndpointHelpersTest(unittest.TestCase):
    def test_custom_api_type_is_preserved(self):
        self.assertEqual(_normalize_api_type("custom_endpoint", is_video=False), "custom_endpoint")
        self.assertEqual(_normalize_api_type("自定义", is_video=False), "custom_endpoint")

    def test_gemini_official_api_type_is_preserved(self):
        self.assertEqual(_normalize_api_type("gemini_official", is_video=False), "gemini_official")
        self.assertEqual(_normalize_api_type("Gemini", is_video=False), "gemini_official")
        self.assertEqual(_normalize_api_type("Gemini 官方", is_video=False), "gemini_official")

    def test_video_api_type_normalization_preserves_legacy_aliases(self):
        for value in ("async_task", "async-task", "async task", "异步任务"):
            with self.subTest(value=value):
                self.assertEqual(_normalize_api_type(value, is_video=True), "async_task")

        for value in ("openai_sync", "openai-sync", "OpenAI Sync", "同步模式"):
            with self.subTest(value=value):
                self.assertEqual(_normalize_api_type(value, is_video=True), "openai_sync")

        for value in ("openai_chat", "OpenAI Chat Completions", "chat/completions", "对话模式"):
            with self.subTest(value=value):
                self.assertEqual(_normalize_api_type(value, is_video=True), "openai_chat")

    def test_extracts_gemini_inline_data_response(self):
        endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini:generateContent"
        first = base64.b64encode(b"first-image" * 20).decode("ascii")
        final = base64.b64encode(b"final-image" * 20).decode("ascii")
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "draft"},
                            {"inlineData": {"mimeType": "image/png", "data": first}},
                            {"inline_data": {"mime_type": "image/webp", "data": final}},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }

        self.assertEqual(
            extract_image_url_from_response(payload, endpoint),
            "data:image/webp;base64," + final,
        )

    def test_rejects_non_image_or_invalid_gemini_inline_data(self):
        endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini:generateContent"
        text_inline = {
            "candidates": [
                {"content": {"parts": [{"inlineData": {"mimeType": "text/plain", "data": _long_b64()}}]}}
            ]
        }
        invalid_image_inline = {
            "candidates": [
                {"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "not base64!"}}]}}
            ]
        }

        self.assertEqual(extract_image_url_from_response(text_inline, endpoint), "")
        self.assertEqual(extract_image_url_from_response(invalid_image_inline, endpoint), "")

    def test_complete_endpoint_validation_rejects_roots(self):
        self.assertTrue(is_complete_endpoint_url("https://api.example.com/v1/images/generations"))
        self.assertTrue(is_complete_endpoint_url("https://ark.cn-beijing.volces.com/api/v3/images/generations"))
        self.assertFalse(is_complete_endpoint_url("https://api.example.com"))
        self.assertFalse(is_complete_endpoint_url("https://api.example.com/v1"))
        self.assertFalse(is_complete_endpoint_url("https://api.example.com/api"))

    def test_extracts_common_image_shapes(self):
        endpoint = "https://api.example.com/v1/images/generations"
        self.assertEqual(
            extract_image_url_from_response({"data": [{"url": "https://cdn.example.com/a.png"}]}, endpoint),
            "https://cdn.example.com/a.png",
        )
        self.assertTrue(
            extract_image_url_from_response({"data": [{"b64_json": _long_b64()}]}, endpoint).startswith(
                "data:image/png;base64,"
            )
        )
        self.assertEqual(
            extract_image_url_from_response(
                {"choices": [{"message": {"content": "![image](https://cdn.example.com/chat.png)"}}]},
                endpoint,
            ),
            "https://cdn.example.com/chat.png",
        )
        self.assertEqual(
            extract_image_url_from_response(
                {"choices": [{"message": {"content": [{"type": "text", "text": "https://cdn.example.com/list.png"}]}}]},
                endpoint,
            ),
            "https://cdn.example.com/list.png",
        )
        self.assertTrue(
            extract_image_url_from_response({"output": [{"type": "image_generation_call", "result": _long_b64()}]}, endpoint).startswith(
                "data:image/png;base64,"
            )
        )
        self.assertTrue(
            extract_image_url_from_response({"image": _long_b64()}, endpoint).startswith("data:image/png;base64,")
        )
        self.assertEqual(
            extract_image_url_from_response({"images": [{"url": "/files/out.png"}]}, endpoint),
            "https://api.example.com/files/out.png",
        )
        self.assertEqual(
            extract_image_url_from_response({"image": "files/from-image-key.webp"}, endpoint),
            "https://api.example.com/files/from-image-key.webp",
        )

    def test_payload_log_summary_redacts_nested_data_urls(self):
        image_data_url = "data:image/jpeg;base64," + _long_b64()
        upper_image_data_url = "DATA:Image/PNG;base64," + _long_b64()
        raw_image = base64.b64encode(b"raw-image" * 40).decode("ascii")
        payload = {
            "model": "gpt-image-2",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                        {"type": "image_url", "image_url": {"url": upper_image_data_url}},
                        {"type": "text", "text": "x" * 220},
                    ],
                }
            ],
            "b64_json": raw_image,
            "image": raw_image,
            "api_key": "sk-test-should-not-log",
        }

        summary = summarize_payload_for_log(payload)

        image_summary = summary["messages"][0]["content"][0]["image_url"]["url"]
        upper_image_summary = summary["messages"][0]["content"][1]["image_url"]["url"]
        text_summary = summary["messages"][0]["content"][2]["text"]
        self.assertIn("<image_data_url", image_summary)
        self.assertIn("<image_data_url", upper_image_summary)
        self.assertIn("chars=", image_summary)
        self.assertNotIn(_long_b64()[:40], str(summary))
        self.assertNotIn(raw_image[:40], str(summary))
        self.assertEqual(summary["b64_json"], f"<image_base64 chars={len(raw_image)}>")
        self.assertEqual(summary["image"], f"<image_base64 chars={len(raw_image)}>")
        self.assertEqual(text_summary, "<text chars=220>")
        self.assertEqual(summary["api_key"], "<redacted>")

    def test_extract_error_message_sanitizes_echoed_payload(self):
        raw_image = base64.b64encode(b"raw-image" * 40).decode("ascii")
        payload = {
            "error": {
                "message": {
                    "api_key": "sk-test-should-not-log",
                    "image": raw_image,
                    "text": "x" * 300,
                }
            }
        }

        message = extract_error_message(json.dumps(payload))

        self.assertNotIn("sk-test-should-not-log", message)
        self.assertNotIn(raw_image[:40], message)
        self.assertIn("<redacted>", message)
        self.assertIn("<image_base64", message)

    def test_error_message_sanitizes_base64_in_result_fields(self):
        raw_image = base64.b64encode(b"result-image" * 40).decode("ascii")
        payload = {
            "error": {
                "message": {
                    "output": [
                        {"type": "image_generation_call", "result": raw_image},
                    ],
                    "data": raw_image,
                }
            }
        }

        summary = summarize_payload_for_log(payload)
        message = extract_error_message(json.dumps(payload))

        self.assertNotIn(raw_image[:40], str(summary))
        self.assertNotIn(raw_image[:40], message)
        self.assertIn("<image_base64", str(summary))
        self.assertIn("<image_base64", message)

    def test_plain_text_log_summary_redacts_embedded_secrets_and_data_urls(self):
        image_data_url = "data:image/png;base64," + _long_b64()
        text = "API key: AIzaSyExampleSecret123456789 failed for image " + image_data_url + ". prompt: draw a cat"

        summary = summarize_text_for_log(text, max_string_length=500)

        self.assertNotIn("AIzaSyExampleSecret123456789", summary)
        self.assertNotIn("draw a cat", summary)
        self.assertNotIn(_long_b64()[:40], summary)
        self.assertIn("API key=<redacted>", summary)
        self.assertIn("prompt=<redacted>", summary)
        self.assertIn("<image_data_url", summary)

    def test_url_summary_redacts_custom_endpoint_query_values(self):
        endpoint = (
            "https://api.example.com/v1/images/generations"
            "?api_key=AIzaSyExampleSecret123456789&token=plain-token&size=1024x1024"
        )

        summary = summarize_url_for_log(endpoint)

        self.assertIn("https://api.example.com/v1/images/generations", summary)
        self.assertIn("api_key=<redacted>", summary)
        self.assertIn("token=<redacted>", summary)
        self.assertIn("size=<redacted>", summary)
        self.assertNotIn("AIzaSyExampleSecret123456789", summary)
        self.assertNotIn("plain-token", summary)
        self.assertNotIn("1024x1024", summary)


class ImageSuccessComponentsTest(unittest.TestCase):
    def _plugin(self, show_time=True, show_model=True):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.plugin_config = types.SimpleNamespace(
            show_generation_time=show_time,
            show_request_model=show_model,
        )
        plugin._create_image_component = lambda url: {"type": "image", "url": url}
        return plugin

    def test_image_success_components_show_metadata_before_image(self):
        plugin = self._plugin(show_time=True, show_model=True)
        result = ChainRunResult(
            image_url="https://cdn.example.com/out.png",
            provider_id="node_1",
            model="override-model",
            elapsed_seconds=1.2,
        )

        components = plugin._build_image_success_components(result, elapsed_seconds=3.4)

        self.assertEqual(len(components), 2)
        self.assertIn("生图耗时：3.4s", components[0].text)
        self.assertIn("请求模型：override-model", components[0].text)
        self.assertEqual(components[1], {"type": "image", "url": "https://cdn.example.com/out.png"})

    def test_image_success_components_can_hide_metadata_for_llm_tools(self):
        plugin = self._plugin(show_time=True, show_model=True)
        result = ChainRunResult(
            image_url="https://cdn.example.com/out.png",
            provider_id="node_1",
            model="override-model",
            elapsed_seconds=1.2,
        )

        components = plugin._build_image_success_components(result, include_metadata=False)

        self.assertEqual(components, [{"type": "image", "url": "https://cdn.example.com/out.png"}])

    def test_image_success_components_allow_independent_toggles(self):
        result = ChainRunResult(
            image_url="https://cdn.example.com/out.png",
            provider_id="node_1",
            model="actual-model",
            elapsed_seconds=1.2,
        )

        time_only = self._plugin(show_time=True, show_model=False)._build_image_success_components(result)
        model_only = self._plugin(show_time=False, show_model=True)._build_image_success_components(result)
        hidden = self._plugin(show_time=False, show_model=False)._build_image_success_components(result)

        self.assertIn("生图耗时", time_only[0].text)
        self.assertNotIn("请求模型", time_only[0].text)
        self.assertNotIn("生图耗时", model_only[0].text)
        self.assertIn("请求模型：actual-model", model_only[0].text)
        self.assertEqual(hidden, [{"type": "image", "url": "https://cdn.example.com/out.png"}])


class PluginImageReturnTest(unittest.IsolatedAsyncioTestCase):
    def _plugin(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.data_dir = str(PLUGIN_DIR / ".pytest_cache" / "plugin_return")
        plugin.plugin_config = types.SimpleNamespace(max_batch_count=4)
        plugin.cmd_parser = main_module.CommandParser()
        plugin.prompt_optimizer = types.SimpleNamespace(
            optimize=lambda prompt, count, session=None, **kwargs: self._optimize(prompt, count)
        )
        plugin.persona_manager = types.SimpleNamespace(
            build_persona_prompt=lambda action: (f"persona base, {action}", {"persona_ref": "persona-default.png"})
        )
        plugin._refresh_from_native_config_if_changed = lambda: None
        plugin._process_and_save_images = self._process_refs
        plugin._parse_extra_params = lambda extra: {"model": "override-model"} if extra else {}
        plugin._prune_cache_if_needed = lambda *args, **kwargs: None
        plugin._lease_cache_paths = lambda paths: list(paths or [])
        plugin._release_cache_paths = lambda paths: None
        plugin._recorded_count = 0
        plugin._record_generated_images = lambda event, count=1: setattr(plugin, "_recorded_count", count)
        plugin._permission_denied_message = lambda event: ""
        plugin._image_quota_error_message = lambda event, count=1: ""
        plugin._reserve_image_quota = lambda event, count=1: ("", 0)
        plugin._complete_image_quota = lambda event, reserved, count: setattr(plugin, "_recorded_count", count)
        plugin._release_image_quota = lambda event, reserved: None
        plugin._get_event_images = lambda event, **kwargs: []
        return plugin

    async def _optimize(self, prompt, count):
        return [f"{prompt} #{index}" for index in range(1, count + 1)]

    async def _process_refs(self, refs, session=None, allow_local_refs=False):
        return [f"safe:{ref}" for ref in refs]

    async def test_generate_images_for_plugin_returns_images_without_sending(self):
        plugin = self._plugin()
        calls = []
        original_client_session = main_module.aiohttp.ClientSession
        original_chain_manager = main_module.ChainManager

        class FakeChainManager:
            def __init__(self, config, session):
                self.config = config
                self.session = session

            async def run_chain_with_metadata(self, chain_name, prompt, **kwargs):
                calls.append((chain_name, prompt, kwargs))
                return ChainRunResult(
                    image_url=f"https://cdn.example.com/{len(calls)}.png",
                    provider_id="node_1",
                    model=kwargs.get("model", "model_a"),
                    elapsed_seconds=1.5,
                )

        main_module.aiohttp.ClientSession = FakeClientSession
        main_module.ChainManager = FakeChainManager
        try:
            result = await plugin.generate_images_for_plugin(
                prompt="draw a cat",
                count=2,
                size="1024x1024",
                extra_params="--model override-model",
                refs=["https://example.com/ref.png"],
            )
        finally:
            main_module.aiohttp.ClientSession = original_client_session
            main_module.ChainManager = original_chain_manager

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["ref_count"], 1)
        self.assertEqual(result["processed_ref_count"], 1)
        self.assertEqual([image["url"] for image in result["images"]], [
            "https://cdn.example.com/1.png",
            "https://cdn.example.com/2.png",
        ])
        self.assertEqual(result["images"][0]["provider_id"], "node_1")
        self.assertEqual(result["images"][0]["model"], "override-model")
        self.assertEqual(calls[0][2]["user_refs"], ["safe:https://example.com/ref.png"])
        self.assertEqual(calls[0][2]["size"], "1024x1024")
        self.assertEqual(calls[0][0], "text2img")

    async def test_generate_images_for_plugin_supports_selfie_mode(self):
        plugin = self._plugin()
        plugin.plugin_config.persona_ref_images = ["persona-ref.png"]
        plugin.plugin_config.chains = {"selfie": ["selfie_node_1"]}
        plugin.persona_manager.build_persona_prompt = lambda action: (
            f"persona base, {action}", {"persona_ref": "safe:persona-ref.png"}
        )
        calls = []
        original_client_session = main_module.aiohttp.ClientSession
        original_chain_manager = main_module.ChainManager

        class FakeChainManager:
            def __init__(self, config, session):
                pass

            async def run_chain_with_metadata(self, chain_name, prompt, **kwargs):
                calls.append((chain_name, prompt, kwargs))
                return ChainRunResult(
                    image_url="https://cdn.example.com/selfie.png",
                    provider_id="selfie_node_1",
                    model="selfie-model",
                    elapsed_seconds=2.0,
                )

        main_module.aiohttp.ClientSession = FakeClientSession
        main_module.ChainManager = FakeChainManager
        try:
            result = await plugin.generate_images_for_plugin(
                prompt="wearing a red hoodie",
                count=1,
                refs=[],
                mode="selfie",
            )
        finally:
            main_module.aiohttp.ClientSession = original_client_session
            main_module.ChainManager = original_chain_manager

        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "selfie")
        self.assertEqual(result["chain"], "selfie")
        self.assertEqual(result["processed_ref_count"], 1)
        self.assertEqual(calls[0][0], "selfie")
        self.assertIn("persona base, wearing a red hoodie #1", calls[0][1])
        self.assertEqual(calls[0][2]["user_refs"], ["safe:persona-ref.png"])
        self.assertNotIn("persona_ref", calls[0][2])

    async def test_generate_image_tool_can_return_json_when_requested(self):
        plugin = self._plugin()
        plugin.generate_images_for_plugin = self._fake_generate_images_for_plugin

        payload = await plugin.tool_generate_image(
            event=object(),
            prompt="draw a dog",
            count=1,
            return_result=True,
            refs="https://example.com/ref.png",
        )

        data = json.loads(payload)
        self.assertTrue(data["success"])
        self.assertEqual(data["images"][0]["image_url"], "https://cdn.example.com/out.png")

    async def test_generate_image_tool_returns_json_error_when_return_result_fails(self):
        plugin = self._plugin()

        async def fail_generate_images_for_plugin(**kwargs):
            raise RuntimeError("api_key=sk-secret1234567890 data:image/png;base64," + _long_b64())

        plugin.generate_images_for_plugin = fail_generate_images_for_plugin

        payload = await plugin.tool_generate_image(
            event=object(),
            prompt="draw a dog",
            return_result=True,
        )

        data = json.loads(payload)
        self.assertFalse(data["success"])
        self.assertEqual(data["mode"], "text2img")
        self.assertIn("api_key=<redacted>", data["message"])
        self.assertNotIn("sk-secret", data["message"])
        self.assertNotIn("data:image/png;base64", data["message"])

    async def _fake_generate_images_for_plugin(self, **kwargs):
        self.assertEqual(kwargs["prompt"], "draw a dog")
        self.assertIs(kwargs["record_usage"], True)
        self.assertEqual(kwargs["refs"], "https://example.com/ref.png")
        self.assertEqual(kwargs.get("mode", ""), "")
        return {
            "success": True,
            "message": "ok",
            "images": [{"image_url": "https://cdn.example.com/out.png"}],
        }

    async def test_generate_selfie_tool_can_return_json_when_requested(self):
        plugin = self._plugin()
        plugin.generate_images_for_plugin = self._fake_generate_selfie_for_plugin

        payload = await plugin.tool_generate_selfie(
            event=object(),
            action="look at camera",
            count=1,
            return_result=True,
            refs="https://example.com/selfie-ref.png",
        )

        data = json.loads(payload)
        self.assertTrue(data["success"])
        self.assertEqual(data["mode"], "selfie")
        self.assertEqual(data["images"][0]["image_url"], "https://cdn.example.com/selfie-out.png")

    async def test_generate_selfie_tool_returns_json_error_when_return_result_fails(self):
        plugin = self._plugin()

        async def fail_generate_images_for_plugin(**kwargs):
            raise RuntimeError("Bearer secret-token-value-1234567890")

        plugin.generate_images_for_plugin = fail_generate_images_for_plugin

        payload = await plugin.tool_generate_selfie(
            event=object(),
            action="look at camera",
            return_result=True,
        )

        data = json.loads(payload)
        self.assertFalse(data["success"])
        self.assertEqual(data["mode"], "selfie")
        self.assertIn("Bearer <redacted>", data["message"])
        self.assertNotIn("secret-token", data["message"])

    async def _fake_generate_selfie_for_plugin(self, **kwargs):
        self.assertEqual(kwargs["prompt"], "look at camera")
        self.assertEqual(kwargs["mode"], "selfie")
        self.assertIs(kwargs["record_usage"], True)
        self.assertEqual(kwargs["refs"], "https://example.com/selfie-ref.png")
        return {
            "success": True,
            "message": "ok",
            "mode": "selfie",
            "images": [{"image_url": "https://cdn.example.com/selfie-out.png"}],
        }

    async def test_existing_generate_selfie_tool_still_sends_images_through_shared_generation(self):
        plugin = self._plugin()
        plugin.plugin_config.persona_ref_images = ["persona-ref.png"]
        plugin.plugin_config.chains = {"selfie": ["selfie_node_1"]}
        send_calls = []
        original_client_session = main_module.aiohttp.ClientSession
        original_chain_manager = main_module.ChainManager

        class FakeChainManager:
            def __init__(self, config, session):
                pass

            async def run_chain_with_metadata(self, chain_name, prompt, **kwargs):
                return ChainRunResult(
                    image_url="https://cdn.example.com/old-selfie.png",
                    provider_id="selfie_node_1",
                    model="selfie-model",
                    elapsed_seconds=1.0,
                )

        async def fake_send_generated_images(event, results, *args, **kwargs):
            send_calls.extend(results)
            return len(results)

        main_module.aiohttp.ClientSession = FakeClientSession
        main_module.ChainManager = FakeChainManager
        plugin._send_generated_images = fake_send_generated_images
        try:
            message = await plugin.tool_generate_selfie(object(), "look at camera", count=1)
        finally:
            main_module.aiohttp.ClientSession = original_client_session
            main_module.ChainManager = original_chain_manager

        self.assertIn("已成功生成并下发了 1 张图", message)
        self.assertEqual(len(send_calls), 1)
        self.assertEqual(send_calls[0].image_url, "https://cdn.example.com/old-selfie.png")
        self.assertEqual(plugin._recorded_count, 1)

    async def test_existing_generate_image_tool_still_sends_images(self):
        plugin = self._plugin()
        send_calls = []
        original_client_session = main_module.aiohttp.ClientSession
        original_chain_manager = main_module.ChainManager

        class FakeChainManager:
            def __init__(self, config, session):
                pass

            async def run_chain_with_metadata(self, chain_name, prompt, **kwargs):
                return ChainRunResult(
                    image_url="https://cdn.example.com/old-tool.png",
                    provider_id="node_1",
                    model="model_a",
                    elapsed_seconds=1.0,
                )

        async def fake_send_generated_images(event, results, *args, **kwargs):
            send_calls.extend(results)
            return len(results)

        main_module.aiohttp.ClientSession = FakeClientSession
        main_module.ChainManager = FakeChainManager
        plugin._send_generated_images = fake_send_generated_images
        try:
            message = await plugin.tool_generate_image(object(), "draw a cat", count=1)
        finally:
            main_module.aiohttp.ClientSession = original_client_session
            main_module.ChainManager = original_chain_manager

        self.assertIn("已成功下发 1 张图", message)
        self.assertEqual(len(send_calls), 1)
        self.assertEqual(send_calls[0].image_url, "https://cdn.example.com/old-tool.png")
        self.assertEqual(plugin._recorded_count, 1)

    def test_plugin_refs_accept_json_object_and_dedupe(self):
        plugin = self._plugin()
        refs = plugin._as_plugin_image_refs(json.dumps({
            "url": "https://example.com/ref.png",
        }))

        self.assertEqual(refs, ["https://example.com/ref.png"])
        self.assertEqual(
            plugin._as_plugin_image_refs([
                {"image_url": "https://example.com/ref.png"},
                "https://example.com/ref.png",
                {"path": "local.png"},
            ]),
            ["https://example.com/ref.png", "local.png"],
        )

    def test_plugin_prompt_normalization_preserves_requested_count(self):
        plugin = self._plugin()

        self.assertEqual(
            plugin._normalize_plugin_prompts(["optimized"], "fallback", 3),
            ["optimized", "fallback", "fallback"],
        )
        self.assertEqual(
            plugin._normalize_plugin_prompts(["one", "two", "three"], "fallback", 2),
            ["one", "two"],
        )

    def test_plugin_response_keeps_results_when_prompt_list_is_short_and_redacts_errors(self):
        plugin = self._plugin()
        response, valid_results = plugin._plugin_generation_response({
            "prompts": ["first prompt"],
            "results": [
                ChainRunResult(
                    image_url="https://cdn.example.com/one.png",
                    provider_id="node_1",
                    model="model-a",
                    elapsed_seconds=1.0,
                ),
                ChainRunResult(
                    image_url="https://cdn.example.com/two.png",
                    provider_id="node_1",
                    model="model-a",
                    elapsed_seconds=1.0,
                ),
                RuntimeError("authorization=Bearer-secret-token-1234567890"),
            ],
            "requested_count": 3,
            "raw_refs": [],
            "safe_refs": [],
            "mode": "text2img",
            "chain": "text2img",
        })

        self.assertTrue(response["success"])
        self.assertEqual(response["count"], 2)
        self.assertEqual(len(valid_results), 2)
        self.assertEqual(response["images"][0]["prompt"], "first prompt")
        self.assertEqual(response["images"][1]["prompt"], "")
        self.assertIn("authorization=<redacted>", response["errors"][0])
        self.assertNotIn("Bearer-secret", response["errors"][0])


class FastPresetListTest(unittest.TestCase):
    def _plugin(self, presets):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.plugin_config = types.SimpleNamespace(presets=presets)
        return plugin

    def _event(self, text, is_at_or_wake=True):
        return types.SimpleNamespace(
            message_str=text,
            message_obj=types.SimpleNamespace(message_str=text, message=[]),
            is_at_or_wake_command=is_at_or_wake,
        )

    def test_fast_preset_list_only_contains_preset_names(self):
        plugin = self._plugin(
            {
                "胶片少女": "35mm film portrait, golden hour, detailed private prompt",
                "机甲猫": "mecha cat, cinematic lighting, detailed private prompt",
            }
        )

        message = plugin._build_fast_preset_list_message()

        self.assertIn("✨ 预设列表", message)
        self.assertIn("1. 胶片少女", message)
        self.assertIn("2. 机甲猫", message)
        self.assertNotIn("35mm film portrait", message)
        self.assertNotIn("cinematic lighting", message)
        self.assertNotIn("detailed private prompt", message)

    def test_view_preset_detail_contains_only_selected_prompt(self):
        plugin = self._plugin(
            {
                "胶片少女": "35mm film portrait, golden hour",
                "机甲猫": "mecha cat, cinematic lighting",
            }
        )

        message = plugin._build_preset_view_message("胶片少女")

        self.assertIn("名称：胶片少女", message)
        self.assertIn("提示词：35mm film portrait, golden hour", message)
        self.assertNotIn("mecha cat", message)

    def test_view_preset_detail_accepts_compact_and_bracket_selector(self):
        plugin = self._plugin({"胶片少女": "35mm film portrait"})

        self.assertEqual(plugin._extract_compact_command_payload("/查看预设胶片少女", "查看预设"), "胶片少女")
        self.assertEqual(plugin._extract_compact_command_payload("/查看预设 胶片少女", "查看预设"), "")
        message = plugin._build_preset_view_message("[胶片少女]")

        self.assertIn("名称：胶片少女", message)
        self.assertIn("提示词：35mm film portrait", message)

    def test_compact_view_accepts_configured_astrbot_command_prefix(self):
        plugin = self._plugin({"胶片少女": "35mm film portrait"})
        plugin._configured_astrbot_command_prefixes = lambda: ["#"]

        self.assertEqual(plugin._extract_compact_command_payload("#查看预设胶片少女", "查看预设"), "胶片少女")
        self.assertEqual(plugin._extract_compact_command_payload("#查看预设 胶片少女", "查看预设"), "")
        self.assertEqual(
            plugin._extract_compact_command_payload("查看预设胶片少女", "查看预设", allow_bare=True),
            "胶片少女",
        )

    def test_preset_trigger_accepts_configured_astrbot_command_prefix(self):
        plugin = self._plugin(
            {
                "胶片少女": "35mm film portrait",
                "胶片": "film photo",
                "MECHA": "mecha cat",
            }
        )
        plugin._configured_astrbot_command_prefixes = lambda: ["#", "!!"]

        self.assertEqual(plugin._match_preset_trigger("#胶片少女"), "胶片少女")
        self.assertEqual(plugin._match_preset_trigger("#胶片少女 参考这张图"), "胶片少女")
        self.assertEqual(plugin._match_preset_trigger("!!胶片少女"), "胶片少女")
        self.assertEqual(plugin._match_preset_trigger("#mecha"), "MECHA")
        self.assertEqual(plugin._match_preset_trigger("#胶片少女风格"), "")
        self.assertEqual(plugin._match_preset_trigger("胶片少女"), "")
        self.assertEqual(plugin._parse_preset_trigger("胶片少女 皮肤白一点", allow_bare=True), ("胶片少女", "皮肤白一点"))
        self.assertEqual(plugin._match_preset_trigger("胶片少女", allow_bare=True), "胶片少女")

    def test_preset_trigger_accepts_actual_local_fullwidth_comma_prefix(self):
        plugin = self._plugin({"胶片少女": "35mm film portrait"})
        plugin._configured_astrbot_command_prefixes = lambda: ["，"]

        self.assertEqual(plugin._match_preset_trigger("，胶片少女"), "胶片少女")
        self.assertEqual(plugin._parse_preset_trigger("胶片少女", allow_bare=True), ("胶片少女", ""))

    def test_preset_trigger_keeps_legacy_slash_prefix_compatibility(self):
        plugin = self._plugin({"胶片少女": "35mm film portrait"})
        plugin._configured_astrbot_command_prefixes = lambda: ["#"]

        self.assertEqual(plugin._match_preset_trigger("/胶片少女"), "胶片少女")

    def test_preset_commands_accept_colon_payload(self):
        plugin = self._plugin({})

        self.assertEqual(
            plugin._extract_command_message_any(self._event("添加预设 胶片少女:35mm film portrait"), ("添加预设",)),
            "胶片少女:35mm film portrait",
        )
        self.assertEqual(plugin._parse_preset_add_payload("胶片少女:35mm film portrait"), ("胶片少女", "35mm film portrait"))
        self.assertEqual(plugin._parse_preset_add_payload("胶片少女：35mm film portrait"), ("胶片少女", "35mm film portrait"))
        self.assertIn("不能包含冒号", plugin._validate_preset_name("坏：名字"))

    def test_preset_generation_prompt_appends_extra_rules(self):
        plugin = self._plugin({})

        self.assertEqual(
            plugin._build_preset_generation_prompt("35mm film portrait", "皮肤白一点"),
            "35mm film portrait\nAdditional requirements: 皮肤白一点",
        )

    def test_config_presets_accept_fullwidth_colon_separator(self):
        config = PluginConfig.from_dict({"presets": ["胶片少女：35mm film portrait"]}, str(PLUGIN_DIR))

        self.assertEqual(config.presets["胶片少女"], "35mm film portrait")

    def test_fast_preset_list_handles_empty_presets(self):
        message = self._plugin({})._build_fast_preset_list_message()

        self.assertIn("当前没有配置极速宏预设", message)

    def test_preset_add_payload_requires_name_and_prompt(self):
        plugin = self._plugin({})

        self.assertEqual(plugin._parse_preset_add_payload("胶片少女 35mm film portrait"), ("胶片少女", "35mm film portrait"))
        self.assertEqual(plugin._parse_preset_add_payload("胶片少女"), ("胶片少女", ""))
        self.assertIn("不能包含冒号", plugin._validate_preset_name("坏:名字"))

    def test_upsert_and_delete_preset_update_runtime_presets(self):
        plugin = self._plugin({"旧预设": "old prompt"})
        plugin._config_lock = threading.RLock()
        plugin._native_config_path = ""

        def replace_presets(presets):
            plugin.plugin_config = types.SimpleNamespace(presets=dict(presets))

        plugin._replace_presets = replace_presets

        self.assertFalse(plugin._upsert_preset("新预设", "new prompt"))
        self.assertEqual(plugin.plugin_config.presets["新预设"], "new prompt")
        self.assertTrue(plugin._upsert_preset("新预设", "updated prompt"))
        self.assertEqual(plugin.plugin_config.presets["新预设"], "updated prompt")
        self.assertEqual(plugin._delete_preset("新预设"), "新预设")
        self.assertNotIn("新预设", plugin.plugin_config.presets)

