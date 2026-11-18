# -*- coding: utf-8 -*-
"""custom_endpoint 回归套件拆分母文件（断言与原文件逐字一致）。"""
import base64
import unittest

from _custom_endpoint_helpers import (CustomEndpointProvider, FakeResponse, FakeSession, GeminiOfficialProvider, OpenAIChatProvider, OpenAIProvider, ProviderConfig, _long_b64, fake_logger)  # noqa: F401


class GeminiOfficialProviderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fake_logger.messages.clear()

    def _provider(self, response_payload, base_url="", status=200):
        config = ProviderConfig(
            id="gemini_node",
            api_type="gemini_official",
            base_url=base_url,
            api_keys=["gemini-key"],
            model="gemini-3.1-flash-image-preview",
            timeout=120.0,
        )
        session = FakeSession(FakeResponse(response_payload, status=status))
        return GeminiOfficialProvider(config, session), session

    def _gemini_response(self, mime_type="image/png"):
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "created"},
                            {"inlineData": {"mimeType": mime_type, "data": _long_b64()}},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }

    async def test_posts_generate_content_to_default_google_endpoint(self):
        provider, session = self._provider(self._gemini_response("image/webp"))

        result = await provider.generate_image("draw a cat", size="1024x1024")

        self.assertTrue(result.startswith("data:image/webp;base64,"))
        self.assertEqual(
            session.posts[0]["url"],
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image-preview:generateContent",
        )
        headers = session.posts[0]["headers"]
        self.assertEqual(headers["x-goog-api-key"], "gemini-key")
        self.assertNotIn("Authorization", headers)
        payload = session.posts[0]["json"]
        self.assertEqual(payload["contents"][0]["role"], "user")
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "draw a cat")
        self.assertEqual(payload["generationConfig"]["responseModalities"], ["TEXT", "IMAGE"])
        self.assertEqual(payload["generationConfig"]["imageConfig"]["aspectRatio"], "1:1")
        self.assertNotIn("size", payload)

    async def test_size_mapping_supports_wide_official_aspect_ratios(self):
        provider, session = self._provider(self._gemini_response())

        await provider.generate_image("wide scene", size="2560x1080")

        payload = session.posts[0]["json"]
        self.assertEqual(payload["generationConfig"]["imageConfig"]["aspectRatio"], "21:9")

    async def test_reference_image_uses_official_inline_data_part(self):
        provider, session = self._provider(self._gemini_response())
        jpeg_b64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"jpeg-bytes" * 20).decode("ascii")
        reference = "data:image/jpeg;base64," + jpeg_b64

        await provider.generate_image("edit a cat", user_refs=[reference])

        parts = session.posts[0]["json"]["contents"][0]["parts"]
        self.assertEqual(parts[0]["text"], "edit a cat")
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/jpeg")
        self.assertEqual(parts[1]["inlineData"]["data"], jpeg_b64)

    async def test_preserves_full_generate_content_endpoint(self):
        endpoint = "https://generativelanguage.googleapis.com/v1beta/models/custom-image:generateContent"
        provider, session = self._provider(self._gemini_response(), base_url=endpoint)

        await provider.generate_image("draw a cat")

        self.assertEqual(session.posts[0]["url"], endpoint)

    async def test_text_only_gemini_response_is_reported_as_missing_image(self):
        provider, _ = self._provider(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "I cannot create that image."}]},
                        "finishReason": "SAFETY",
                    }
                ]
            }
        )

        with self.assertRaisesRegex(ValueError, "未返回图片数据"):
            await provider.generate_image("draw a cat")


class CustomEndpointProviderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fake_logger.messages.clear()

    def _provider(self, endpoint, response_payload, provider_cls=CustomEndpointProvider, status=200):
        if provider_cls is CustomEndpointProvider:
            api_type = "custom_endpoint"
        elif provider_cls is GeminiOfficialProvider:
            api_type = "gemini_official"
        elif provider_cls is OpenAIChatProvider:
            api_type = "openai_chat"
        else:
            api_type = "openai_image"
        config = ProviderConfig(
            id="custom_node",
            api_type=api_type,
            base_url=endpoint,
            api_keys=["test-key"],
            model="image-model",
            timeout=30.0,
        )
        session = FakeSession(FakeResponse(response_payload, status=status))
        return provider_cls(config, session), session

    def _log_text(self):
        return "\n".join(message for _, message in fake_logger.messages)

    def _prompt_log_text(self):
        return "\n".join(message for _, message in fake_logger.messages if "核心提示词" in message)

    def _request_summary_log_text(self):
        return "\n".join(
            message
            for _, message in fake_logger.messages
            if "请求体摘要" in message or "高级参数" in message or "透传摘要" in message
        )

    def _non_prompt_log_text(self):
        return "\n".join(message for _, message in fake_logger.messages if "核心提示词" not in message)

    async def test_posts_exact_image_endpoint(self):
        endpoint = "https://api.example.com/v1/images/generations"
        provider, session = self._provider(endpoint, {"data": [{"url": "https://cdn.example.com/out.png"}]})

        result = await provider.generate_image("draw a cat", size="1024x1024")

        self.assertEqual(result, "https://cdn.example.com/out.png")
        self.assertEqual(session.posts[0]["url"], endpoint)
        self.assertEqual(session.posts[0]["json"]["prompt"], "draw a cat")
        self.assertEqual(session.posts[0]["json"]["size"], "1024x1024")

    async def test_custom_image_payload_uses_siliconflow_reference_fields(self):
        endpoint = "https://api.example.com/v1/images/generations"
        provider, session = self._provider(endpoint, {"images": [{"url": "https://cdn.example.com/out.png"}]})
        ref = "data:image/png;base64," + _long_b64()
        ref2 = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"ref-2" * 30).decode("ascii")
        ref3 = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"ref-3" * 30).decode("ascii")

        await provider.generate_image("edit a cat", user_refs=[ref, ref2, ref3])

        self.assertEqual(session.posts[0]["url"], endpoint)
        self.assertEqual(session.posts[0]["json"]["image"], ref)
        self.assertEqual(session.posts[0]["json"]["image2"], ref2)
        self.assertEqual(session.posts[0]["json"]["image3"], ref3)
        self.assertNotIn("images", session.posts[0]["json"])

    async def test_preserves_exact_custom_endpoint_url(self):
        endpoint = "https://api.example.com/v1/images/generations/"
        provider, session = self._provider(endpoint, {"data": [{"url": "https://cdn.example.com/out.png"}]})

        await provider.generate_image("draw a cat")

        self.assertEqual(session.posts[0]["url"], endpoint)

    async def test_rejects_edits_endpoint_without_reference_image(self):
        endpoint = "https://api.example.com/v1/images/edits"
        provider, session = self._provider(endpoint, {"data": [{"url": "unused"}]})

        with self.assertRaisesRegex(ValueError, "至少一张参考图"):
            await provider.generate_image("edit a cat")

        self.assertEqual(session.posts, [])

    async def test_edits_endpoint_uses_multipart_image_array_for_multiple_references(self):
        endpoint = "https://api.example.com/v1/images/edits"
        provider, session = self._provider(endpoint, {"data": [{"url": "https://cdn.example.com/out.png"}]})
        ref = "data:image/png;base64," + _long_b64()
        ref2 = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"ref-2" * 30).decode("ascii")

        await provider.generate_image("edit a cat", user_refs=[ref, ref2])

        self.assertEqual(session.posts[0]["url"], endpoint)
        form = session.posts[0]["data"]
        image_field_names = [
            field[0]["name"]
            for field in getattr(form, "_fields", [])
            if field and field[0].get("name", "").startswith("image")
        ]
        self.assertEqual(image_field_names, ["image[]", "image[]"])

    async def test_posts_exact_chat_endpoint(self):
        endpoint = "https://api.example.com/v1/chat/completions"
        provider, session = self._provider(
            endpoint,
            {"choices": [{"message": {"content": "https://cdn.example.com/chat-out.png"}}]},
        )

        result = await provider.generate_image("draw a cat")

        self.assertEqual(result, "https://cdn.example.com/chat-out.png")
        self.assertEqual(session.posts[0]["url"], endpoint)
        self.assertIn("messages", session.posts[0]["json"])

    async def test_posts_exact_responses_endpoint(self):
        endpoint = "https://api.example.com/v1/responses"
        provider, session = self._provider(
            endpoint,
            {"output": [{"type": "image_generation_call", "result": _long_b64()}]},
        )

        result = await provider.generate_image("draw a cat")

        self.assertTrue(result.startswith("data:image/png;base64,"))
        self.assertEqual(session.posts[0]["url"], endpoint)
        self.assertIn("tools", session.posts[0]["json"])

    async def test_rejects_incomplete_custom_endpoint(self):
        provider, session = self._provider("https://api.example.com/v1", {"data": [{"url": "unused"}]})

        with self.assertRaisesRegex(ValueError, "完整请求路径"):
            await provider.generate_image("draw a cat")

        self.assertEqual(session.posts, [])

    async def test_local_reference_missing_does_not_post(self):
        endpoint = "https://api.example.com/v1/images/generations"
        provider, session = self._provider(endpoint, {"data": [{"url": "unused"}]})

        with self.assertRaisesRegex(RuntimeError, "本地参考图不存在"):
            await provider.generate_image("edit a cat", user_refs=["C:/definitely/missing.png"])

        self.assertEqual(session.posts, [])


    async def test_provider_logs_are_summarized_without_mutating_json_payloads(self):
        raw_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"provider-raw-image" * 35).decode("ascii")
        ref_data_url = "data:image/png;base64," + raw_b64
        secret = "sk-provider-secret-should-not-log"
        long_prompt = "draw " + ("very detailed " * 30) + "PROMPT_TAIL_SHOULD_NOT_LOG"

        cases = [
            (
                CustomEndpointProvider,
                "https://api.example.com/v1/images/generations",
                {"data": [{"url": "https://cdn.example.com/custom.png"}]},
                {"user_refs": [ref_data_url], "api_key": secret, "b64_json": raw_b64},
            ),
            (
                OpenAIProvider,
                "https://api.example.com/v1",
                {"data": [{"url": "https://cdn.example.com/openai.png"}]},
                {"api_key": secret, "b64_json": raw_b64},
            ),
            (
                OpenAIChatProvider,
                "https://api.example.com/v1",
                {"choices": [{"message": {"content": "https://cdn.example.com/chat.png"}}]},
                {"api_key": secret, "b64_json": raw_b64},
            ),
        ]

        for provider_cls, endpoint, response_payload, kwargs in cases:
            with self.subTest(provider_cls=provider_cls.__name__):
                fake_logger.messages.clear()
                provider, session = self._provider(endpoint, response_payload, provider_cls=provider_cls)

                await provider.generate_image(long_prompt, **kwargs)

                sent_payload = session.posts[0]["json"]
                if provider_cls is CustomEndpointProvider:
                    self.assertEqual(sent_payload["prompt"], long_prompt)
                    self.assertEqual(sent_payload["image"], ref_data_url)
                elif provider_cls is OpenAIProvider:
                    self.assertEqual(sent_payload["prompt"], long_prompt)
                else:
                    self.assertIn(long_prompt, sent_payload["messages"][0]["content"][0]["text"])
                self.assertEqual(sent_payload["api_key"], secret)
                self.assertEqual(sent_payload["b64_json"], raw_b64)

                logs = self._log_text()
                self.assertNotIn(secret, logs)
                self.assertNotIn(raw_b64[:40], logs)
                self.assertIn(long_prompt, self._prompt_log_text())
                self.assertIn("PROMPT_TAIL_SHOULD_NOT_LOG", self._prompt_log_text())
                self.assertIn("<redacted>", logs)
                self.assertIn("<image_base64", logs)
                summary_logs = self._request_summary_log_text()
                self.assertNotIn(long_prompt, summary_logs)
                self.assertNotIn("PROMPT_TAIL_SHOULD_NOT_LOG", summary_logs)
                if provider_cls is not OpenAIChatProvider:
                    self.assertIn("<prompt chars=", summary_logs)

    async def test_short_prompts_and_custom_endpoint_queries_do_not_leak_to_logs(self):
        short_prompt = "draw a cat"
        query_secret = "AIzaSyQuerySecret123456789"
        cases = [
            (
                CustomEndpointProvider,
                "https://api.example.com/v1/images/generations?api_key=" + query_secret + "&size=1024x1024",
                {"data": [{"url": "https://cdn.example.com/custom.png"}]},
                lambda payload: payload["prompt"],
            ),
            (
                CustomEndpointProvider,
                "https://api.example.com/v1/chat/completions",
                {"choices": [{"message": {"content": "https://cdn.example.com/custom-chat.png"}}]},
                lambda payload: payload["messages"][0]["content"][0]["text"],
            ),
            (
                CustomEndpointProvider,
                "https://api.example.com/v1/responses",
                {"output": [{"type": "image_generation_call", "result": _long_b64()}]},
                lambda payload: payload["input"],
            ),
            (
                OpenAIProvider,
                "https://api.example.com/v1",
                {"data": [{"url": "https://cdn.example.com/openai.png"}]},
                lambda payload: payload["prompt"],
            ),
            (
                OpenAIChatProvider,
                "https://api.example.com/v1",
                {"choices": [{"message": {"content": "https://cdn.example.com/chat.png"}}]},
                lambda payload: payload["messages"][0]["content"][0]["text"],
            ),
        ]

        for provider_cls, endpoint, response_payload, prompt_getter in cases:
            with self.subTest(provider_cls=provider_cls.__name__, endpoint=endpoint):
                fake_logger.messages.clear()
                provider, session = self._provider(endpoint, response_payload, provider_cls=provider_cls)

                await provider.generate_image(short_prompt)

                self.assertIn(short_prompt, prompt_getter(session.posts[0]["json"]))
                if provider_cls is CustomEndpointProvider:
                    self.assertEqual(session.posts[0]["url"], endpoint)
                logs = self._log_text()
                self.assertIn(short_prompt, self._prompt_log_text())
                self.assertNotIn(short_prompt, self._request_summary_log_text())
                self.assertNotIn(query_secret, logs)
                self.assertNotIn("1024x1024", logs)
                if provider_cls is not OpenAIChatProvider:
                    summary_logs = self._request_summary_log_text()
                    self.assertTrue(
                        "<prompt chars=" in summary_logs
                        or "<text chars=" in summary_logs
                        or "<input chars=" in summary_logs
                    )
                if query_secret in endpoint:
                    self.assertIn("api_key=<redacted>", logs)

    async def test_provider_error_logs_and_exceptions_are_sanitized(self):
        raw_b64 = base64.b64encode(b"provider-error-image" * 40).decode("ascii")
        secret = "sk-error-secret-should-not-log"
        long_detail = ("echoed error detail " * 40) + "ERROR_TAIL_SHOULD_NOT_LOG"
        error_payload = {
            "error": {
                "message": {
                    "api_key": secret,
                    "image": raw_b64,
                    "detail": long_detail,
                }
            }
        }

        cases = [
            (CustomEndpointProvider, "https://api.example.com/v1/images/generations"),
            (OpenAIProvider, "https://api.example.com/v1"),
            (OpenAIChatProvider, "https://api.example.com/v1"),
        ]

        for provider_cls, endpoint in cases:
            with self.subTest(provider_cls=provider_cls.__name__):
                fake_logger.messages.clear()
                provider, _ = self._provider(endpoint, error_payload, provider_cls=provider_cls, status=400)

                with self.assertRaises(RuntimeError) as raised:
                    await provider.generate_image("draw a cat")

                combined = str(raised.exception) + "\n" + self._log_text()
                self.assertNotIn(secret, combined)
                self.assertNotIn(raw_b64[:40], combined)
                self.assertNotIn("ERROR_TAIL_SHOULD_NOT_LOG", combined)
                self.assertIn("<redacted>", combined)
                self.assertIn("<image_base64", combined)

    async def test_short_error_text_does_not_echo_prompt_or_non_openai_key(self):
        query_secret = "AIzaSyErrorSecret123456789"
        error_payload = {"error": {"message": "Invalid API key: " + query_secret + " for prompt: draw a cat"}}
        cases = [
            (CustomEndpointProvider, "https://api.example.com/v1/images/generations"),
            (OpenAIProvider, "https://api.example.com/v1"),
            (OpenAIChatProvider, "https://api.example.com/v1"),
        ]

        for provider_cls, endpoint in cases:
            with self.subTest(provider_cls=provider_cls.__name__):
                fake_logger.messages.clear()
                provider, _ = self._provider(endpoint, error_payload, provider_cls=provider_cls, status=400)

                with self.assertRaises(RuntimeError) as raised:
                    await provider.generate_image("draw a cat")

                combined = str(raised.exception) + "\n" + self._non_prompt_log_text()
                self.assertNotIn(query_secret, combined)
                self.assertNotIn("draw a cat", combined)
                self.assertIn("API key=<redacted>", combined)
                self.assertIn("prompt=<redacted>", combined)
                self.assertIn("draw a cat", self._prompt_log_text())

    async def test_unexpected_success_response_exception_is_summarized(self):
        raw_b64 = base64.b64encode(b"unexpected-success-image" * 35).decode("ascii")
        payload = {
            "error": {
                "api_key": "sk-success-secret-should-not-log",
                "image": raw_b64,
                "detail": "x" * 700,
            }
        }
        provider, _ = self._provider("https://api.example.com/v1", payload, provider_cls=OpenAIProvider)

        with self.assertRaises(ValueError) as raised:
            await provider.generate_image("draw a cat")

        message = str(raised.exception)
        self.assertNotIn("sk-success-secret-should-not-log", message)
        self.assertNotIn(raw_b64[:40], message)
        self.assertIn("<redacted>", message)
        self.assertIn("<image_base64", message)

