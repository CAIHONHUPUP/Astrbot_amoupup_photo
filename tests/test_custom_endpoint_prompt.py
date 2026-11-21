# -*- coding: utf-8 -*-
"""custom_endpoint 回归套件拆分母文件（断言与原文件逐字一致）。"""
import ast
import json
import types
import unittest

from _custom_endpoint_helpers import (FakeSession, GeminiOfficialProvider, PLUGIN_DIR, PluginConfig, PromptOptimizer, ProviderConfig, base_module, prompt_optimizer_module, provider_factory_module)  # noqa: F401


class PromptOptimizerRegressionTest(unittest.IsolatedAsyncioTestCase):
    class OptimizerResponse:
        def __init__(self, payload, headers=None):
            self.payload = payload
            self.headers = headers or {}
            self.charset = "utf-8"
            self.read_called = False

        def raise_for_status(self):
            return None

        async def read(self):
            self.read_called = True
            return json.dumps(self.payload).encode("utf-8")

    @staticmethod
    def _config(api_type="openai_chat", api_keys=None):
        provider = ProviderConfig(
            id="optimizer-node",
            api_type=api_type,
            base_url="https://api.example.com/v1",
            api_keys=api_keys or ["key-a", "key-b"],
            model="chat-model",
            timeout=30.0,
        )
        config = types.SimpleNamespace(
            enable_optimizer=True,
            chains={"optimizer": [provider.id]},
            providers=[provider],
            optimizer_model="chat-model",
            optimizer_timeout=15.0,
            optimizer_style="手机日常原生感",
            optimizer_custom_prompt="",
        )
        config.get_provider = lambda provider_id: provider if provider_id == provider.id else None
        return config

    async def test_extracts_legal_top_level_json_array(self):
        content = json.dumps([
            {"subject_appearance": "first distinct subject"},
            {"subject_appearance": "second distinct subject"},
        ])
        response = self.OptimizerResponse({
            "choices": [{"message": {"content": content}}],
        })

        results = await PromptOptimizer(self._config()).optimize(
            "raw action",
            count=2,
            session=FakeSession(response),
        )

        self.assertIn("first distinct subject", results[0])
        self.assertIn("second distinct subject", results[1])

    async def test_extracts_top_level_array_before_bracketed_trailing_note(self):
        content = json.dumps([
            {"subject_appearance": "first distinct subject"},
            {"subject_appearance": "second distinct subject"},
        ]) + "\nDone [ok]"
        response = self.OptimizerResponse({
            "choices": [{"message": {"content": content}}],
        })

        results = await PromptOptimizer(self._config()).optimize(
            "raw action",
            count=2,
            session=FakeSession(response),
        )

        self.assertIn("first distinct subject", results[0])
        self.assertIn("second distinct subject", results[1])

    async def test_gemini_official_node_degrades_without_chat_request(self):
        session = FakeSession(self.OptimizerResponse({}))

        results = await PromptOptimizer(self._config(api_type="gemini_official")).optimize(
            "raw action",
            count=2,
            session=session,
        )

        self.assertEqual(results, ["raw action", "raw action"])
        self.assertEqual(session.posts, [])

    async def test_response_body_limit_is_checked_before_read(self):
        response = self.OptimizerResponse(
            {"choices": []},
            headers={"Content-Length": str(prompt_optimizer_module.MAX_OPTIMIZER_RESPONSE_BYTES + 1)},
        )

        results = await PromptOptimizer(self._config()).optimize(
            "raw action",
            session=FakeSession(response),
        )

        self.assertEqual(results, ["raw action"])
        self.assertFalse(response.read_called)

    async def test_optimizer_key_rotation_isolated_from_image_scope(self):
        with base_module._KEY_ROTATION_LOCK:
            original_rotation = dict(base_module._KEY_ROTATION_INDEX)
            base_module._KEY_ROTATION_INDEX.clear()
        try:
            image_scope = "image:openai_chat:https://api.example.com/v1"
            self.assertEqual(
                base_module.next_api_key("optimizer-node", ["key-a", "key-b"], scope=image_scope),
                "key-a",
            )
            response = self.OptimizerResponse({
                "choices": [{"message": {"content": '{"subject_appearance": "optimized subject"}'}}],
            })
            session = FakeSession(response)

            await PromptOptimizer(self._config()).optimize("raw action", session=session)

            self.assertEqual(session.posts[0]["headers"]["Authorization"], "Bearer key-a")
        finally:
            with base_module._KEY_ROTATION_LOCK:
                base_module._KEY_ROTATION_INDEX.clear()
                base_module._KEY_ROTATION_INDEX.update(original_rotation)


class GenerationMetadataConfigTest(unittest.TestCase):
    def test_generation_metadata_toggles_default_to_hidden(self):
        config = PluginConfig.from_dict({}, str(PLUGIN_DIR))

        self.assertFalse(config.show_generation_time)
        self.assertFalse(config.show_request_model)

    def test_generation_metadata_toggles_are_independent(self):
        config = PluginConfig.from_dict(
            {
                "show_generation_time": True,
                "show_request_model": False,
            },
            str(PLUGIN_DIR),
        )

        self.assertTrue(config.show_generation_time)
        self.assertFalse(config.show_request_model)

    def test_provider_default_sizes_are_normalized(self):
        config = PluginConfig.from_dict(
            {
                "providers": [{"id": "node_1", "default_size": " 1024x1024 "}],
                "video_providers": [{"id": "video_node_1", "default_size": "1280x720"}],
            },
            str(PLUGIN_DIR),
        )

        self.assertEqual(config.providers[0].default_size, "1024x1024")
        self.assertEqual(config.video_providers[0].default_size, "1280x720")


class RuntimeConfigKeyTest(unittest.TestCase):
    def test_generation_metadata_keys_are_preserved_by_runtime_config_cleaner(self):
        tree = ast.parse((PLUGIN_DIR / "main.py").read_text(encoding="utf-8"))
        config_keys = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "CONFIG_KEYS"
                for target in node.targets
            ):
                config_keys = set(ast.literal_eval(node.value))
                break

        self.assertIn("show_generation_time", config_keys)
        self.assertIn("show_request_model", config_keys)

    def test_schema_and_pages_offer_gemini_before_custom(self):
        schema = json.loads((PLUGIN_DIR / "_conf_schema.json").read_text(encoding="utf-8"))
        options = schema["providers"]["templates"]["image_provider"]["items"]["api_type"]["options"]
        self.assertLess(options.index("gemini_official"), options.index("custom_endpoint"))

        app_js = (PLUGIN_DIR / "pages" / "插件配置" / "app.js").read_text(encoding="utf-8")
        self.assertLess(app_js.index('"gemini_official"'), app_js.index('"custom_endpoint"'))
        self.assertIn("GEMINI_OFFICIAL_BASE_URL", app_js)
        self.assertNotIn("return applyImageProviderDefaults({", app_js)

    def test_tests_directory_is_not_gitignored(self):
        gitignore = (PLUGIN_DIR / ".gitignore").read_text(encoding="utf-8")

        self.assertNotIn("tests/", gitignore.splitlines())

    def test_provider_factory_creates_gemini_provider(self):
        config = ProviderConfig(
            id="gemini_node",
            api_type="gemini_official",
            base_url="",
            api_keys=["test-key"],
            model="gemini-3.1-flash-image-preview",
            timeout=120.0,
        )

        self.assertIsInstance(provider_factory_module.create_provider(config, session=object()), GeminiOfficialProvider)

