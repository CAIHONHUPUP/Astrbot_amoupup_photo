# -*- coding: utf-8 -*-
"""custom_endpoint 回归套件拆分母文件（断言与原文件逐字一致）。"""
import types
import unittest

from _custom_endpoint_helpers import (ChainManager, ChainRunResult, FakeClientSession, OmniDrawPlugin, PLUGIN_DIR, PluginConfig, ProviderConfig, VideoManager, _long_b64, chain_manager_module, main_module)  # noqa: F401


class PresetEventHandlerTest(unittest.IsolatedAsyncioTestCase):
    def _plugin(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.plugin_config = types.SimpleNamespace(
            presets={"胶片少女": "35mm film portrait --size 1024x1024"},
            draw_pending_message="正在生成 {command}: {prompt} 参数{param_count}",
            persona_name="默认助理",
            verbose_report=False,
        )
        plugin.cmd_parser = main_module.CommandParser()
        plugin._permission_denied_message = lambda event: ""
        plugin._image_quota_error_message = lambda event, count=1: ""
        plugin._reserve_image_quota = lambda event, count=1: ("", 0)
        plugin._complete_image_quota = lambda event, reserved, count: setattr(plugin, "_recorded_count", count)
        plugin._release_image_quota = lambda event, reserved: None
        plugin._get_event_images = lambda *args, **kwargs: []
        plugin._process_and_save_images = self._process_refs
        plugin._lease_cache_paths = lambda paths: list(paths or [])
        plugin._release_cache_paths = lambda paths: None
        plugin._recorded_count = 0
        plugin._record_generated_images = lambda event, count=1: setattr(plugin, "_recorded_count", count)
        plugin._build_image_success_components = lambda result, elapsed: [{"result": result.image_url}]
        return plugin

    async def _process_refs(self, refs, session=None, allow_local_refs=False):
        return []

    async def test_wake_command_with_prefix_stripped_triggers_preset_generation(self):
        plugin = self._plugin()
        calls = []
        original_client_session = main_module.aiohttp.ClientSession
        original_chain_manager = main_module.ChainManager

        class FakeChainManager:
            def __init__(self, config, session):
                pass

            async def run_chain_with_metadata(self, chain_name, prompt, **kwargs):
                calls.append((chain_name, prompt, kwargs))
                return ChainRunResult(
                    image_url="https://cdn.example.com/preset.png",
                    provider_id="node_1",
                    model=kwargs.get("model", "model_a"),
                    elapsed_seconds=1.0,
                )

        class FakeEvent:
            is_at_or_wake_command = True

            def __init__(self):
                self.message_str = "胶片少女 皮肤白一点 --model test-model"
                self.message_obj = types.SimpleNamespace(message_str=self.message_str, message=[], message_id="msg-1")
                self.stopped = False

            def stop_event(self):
                self.stopped = True

            def plain_result(self, text):
                return ("plain", text)

            def chain_result(self, chain):
                return ("chain", chain)

            async def send(self, result):
                self.sent.append(result)

        event = FakeEvent()
        event.sent = []
        main_module.aiohttp.ClientSession = FakeClientSession
        main_module.ChainManager = FakeChainManager
        try:
            results = [item async for item in plugin.on_message_preset(event)]
        finally:
            main_module.aiohttp.ClientSession = original_client_session
            main_module.ChainManager = original_chain_manager

        self.assertTrue(event.stopped)
        self.assertEqual(plugin._recorded_count, 1)
        self.assertEqual(calls, [
            (
                "text2img",
                "35mm film portrait\nAdditional requirements: 皮肤白一点",
                {"size": "1024x1024", "model": "test-model"},
            )
        ])
        self.assertEqual(event.sent[0][0], "plain")
        self.assertIn("胶片少女", event.sent[0][1])
        self.assertEqual(results, [("chain", [{"result": "https://cdn.example.com/preset.png"}])])


class CommandPendingSendTest(unittest.IsolatedAsyncioTestCase):
    def _plugin(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.plugin_config = types.SimpleNamespace(
            draw_pending_message="正在生成 {command}: {prompt} 参数{param_count}",
            selfie_pending_message="正在自拍 {command}: {prompt} 参数{param_count}",
            persona_name="默认助手",
            verbose_report=False,
            persona_ref_images=[],
            chains={"selfie": ["selfie_node"]},
        )
        plugin.cmd_parser = main_module.CommandParser()
        plugin.prompt_optimizer = types.SimpleNamespace(
            optimize=lambda prompt, count, session=None, **kwargs: self._optimize(prompt, count)
        )
        plugin._load_locked_outfit = lambda: ""
        plugin.persona_manager = types.SimpleNamespace(
            build_persona_prompt=lambda action: (f"persona base, {action}", {"persona_ref": "persona-default.png"})
        )
        plugin.video_manager = types.SimpleNamespace(background_task_runner=self._video_runner)
        plugin._permission_denied_message = lambda event: ""
        plugin._image_quota_error_message = lambda event, count=1: ""
        plugin._reserve_image_quota = lambda event, count=1: ("", 0)
        plugin._complete_image_quota = lambda event, reserved, count: setattr(plugin, "_recorded_count", count)
        plugin._release_image_quota = lambda event, reserved: None
        plugin._extract_command_message = lambda event, command, fallback: fallback
        plugin._get_event_images = lambda *args, **kwargs: []
        plugin._process_and_save_images = self._process_refs
        plugin._lease_cache_paths = lambda paths: list(paths or [])
        plugin._release_cache_paths = lambda paths: None
        plugin._recorded_count = 0
        plugin._record_generated_images = lambda event, count=1: setattr(plugin, "_recorded_count", count)
        plugin._build_image_success_components = lambda result, elapsed: [{"result": result.image_url}]
        plugin._scheduled_tasks = []

        def create_background_task(coro, **kwargs):
            plugin._scheduled_tasks.append(coro)
            close = getattr(coro, "close", None)
            if callable(close):
                close()

        plugin._create_background_task = create_background_task
        return plugin

    async def _process_refs(self, refs, session=None, allow_local_refs=False):
        return []

    async def _optimize(self, prompt, count):
        return [f"optimized {prompt}"]

    async def _video_runner(self, event, prompt, refs, kwargs):
        return None

    class FakeEvent:
        def __init__(self):
            self.message_obj = types.SimpleNamespace(message_str="", message=[], message_id="msg-1")
            self.message_str = ""
            self.sent = []

        def plain_result(self, text):
            return ("plain", text)

        def chain_result(self, chain):
            return ("chain", chain)

        async def send(self, result):
            self.sent.append(result)

    async def test_draw_sends_pending_message_without_yielding_before_chain(self):
        plugin = self._plugin()
        calls = []
        original_client_session = main_module.aiohttp.ClientSession
        original_chain_manager = main_module.ChainManager

        class FakeChainManager:
            def __init__(self, config, session):
                pass

            async def run_chain_with_metadata(self, chain_name, prompt, **kwargs):
                calls.append((chain_name, prompt, kwargs))
                return ChainRunResult(
                    image_url="https://cdn.example.com/draw.png",
                    provider_id="node_1",
                    model=kwargs.get("model", "model_a"),
                    elapsed_seconds=1.0,
                )

        event = self.FakeEvent()
        main_module.aiohttp.ClientSession = FakeClientSession
        main_module.ChainManager = FakeChainManager
        try:
            results = [item async for item in plugin.cmd_draw(event, "海边日落", "--model", "test-model")]
        finally:
            main_module.aiohttp.ClientSession = original_client_session
            main_module.ChainManager = original_chain_manager

        self.assertEqual(event.sent[0][0], "plain")
        self.assertIn("正在生成 画", event.sent[0][1])
        self.assertEqual(calls, [("text2img", "海边日落", {"model": "test-model"})])
        self.assertEqual(results, [("chain", [{"result": "https://cdn.example.com/draw.png"}])])
        self.assertEqual(plugin._recorded_count, 1)

    async def test_selfie_sends_pending_message_without_yielding_before_chain(self):
        plugin = self._plugin()
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
                    provider_id="selfie_node",
                    model=kwargs.get("model", "model_a"),
                    elapsed_seconds=1.0,
                )

        event = self.FakeEvent()
        main_module.aiohttp.ClientSession = FakeClientSession
        main_module.ChainManager = FakeChainManager
        try:
            results = [item async for item in plugin.cmd_selfie(event, "挥手", "--model", "test-model")]
        finally:
            main_module.aiohttp.ClientSession = original_client_session
            main_module.ChainManager = original_chain_manager

        self.assertEqual(event.sent[0][0], "plain")
        self.assertIn("正在自拍 自拍", event.sent[0][1])
        self.assertEqual(
            calls,
            [("selfie", "persona base, optimized 挥手", {"persona_ref": "persona-default.png", "model": "test-model"})],
        )
        self.assertEqual(results, [("chain", [{"result": "https://cdn.example.com/selfie.png"}])])
        self.assertEqual(plugin._recorded_count, 1)

    async def test_video_sends_pending_message_then_schedules_background_task(self):
        plugin = self._plugin()
        event = self.FakeEvent()

        results = [item async for item in plugin.cmd_video(event, "城市夜景", "--duration", "5")]

        self.assertEqual(event.sent[0][0], "plain")
        self.assertIn("视频任务已提交后台渲染", event.sent[0][1])
        self.assertEqual(results, [])
        self.assertEqual(len(plugin._scheduled_tasks), 1)

    async def test_pending_send_failure_does_not_abort_draw_generation(self):
        plugin = self._plugin()
        calls = []
        original_client_session = main_module.aiohttp.ClientSession
        original_chain_manager = main_module.ChainManager

        class FakeChainManager:
            def __init__(self, config, session):
                pass

            async def run_chain_with_metadata(self, chain_name, prompt, **kwargs):
                calls.append((chain_name, prompt, kwargs))
                return ChainRunResult(
                    image_url="https://cdn.example.com/draw.png",
                    provider_id="node_1",
                    model="model_a",
                    elapsed_seconds=1.0,
                )

        class FailingSendEvent(self.FakeEvent):
            async def send(self, result):
                raise RuntimeError("send failed")

        event = FailingSendEvent()
        main_module.aiohttp.ClientSession = FakeClientSession
        main_module.ChainManager = FakeChainManager
        try:
            results = [item async for item in plugin.cmd_draw(event, "海边日落")]
        finally:
            main_module.aiohttp.ClientSession = original_client_session
            main_module.ChainManager = original_chain_manager

        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [("chain", [{"result": "https://cdn.example.com/draw.png"}])])


class ChainManagerMetadataTest(unittest.IsolatedAsyncioTestCase):
    async def test_chain_result_uses_actual_successful_provider_metadata(self):
        config = PluginConfig.from_dict(
            {
                "providers": [
                    {
                        "id": "primary",
                        "api_type": "openai_image",
                        "base_url": "https://api.example.com/v1",
                        "api_keys": "key-1",
                        "model": "primary-model",
                    },
                    {
                        "id": "backup",
                        "api_type": "openai_image",
                        "base_url": "https://api.example.com/v1",
                        "api_keys": "key-2",
                        "model": "backup-model",
                    },
                ],
                "router_config": {"chain_text2img": "primary,backup"},
            },
            str(PLUGIN_DIR),
        )
        calls = []

        class FakeProvider:
            def __init__(self, provider_config):
                self.provider_config = provider_config

            async def generate_image(self, prompt, **kwargs):
                calls.append((self.provider_config.id, prompt, kwargs))
                if self.provider_config.id == "primary":
                    raise RuntimeError("primary failed")
                return "https://cdn.example.com/out.png"

        original_create_provider = chain_manager_module.create_provider
        chain_manager_module.create_provider = lambda provider_config, session: FakeProvider(provider_config)
        try:
            manager = ChainManager(config, session=object())
            result = await manager.run_chain_with_metadata(
                "text2img",
                "draw a cat",
                size="1024x1024",
                model="override-model",
            )

            self.assertEqual(result.image_url, "https://cdn.example.com/out.png")
            self.assertEqual(result.provider_id, "backup")
            self.assertEqual(result.model, "override-model")
            self.assertGreaterEqual(result.elapsed_seconds, 0)
            self.assertEqual([call[0] for call in calls], ["primary", "backup"])
            self.assertEqual(calls[0][2]["size"], "1024x1024")
            self.assertEqual(calls[1][2]["size"], "1024x1024")
            self.assertEqual(await manager.run_chain("text2img", "draw a cat"), "https://cdn.example.com/out.png")
        finally:
            chain_manager_module.create_provider = original_create_provider

    async def test_provider_default_size_is_used_when_request_omits_size(self):
        config = PluginConfig.from_dict(
            {
                "providers": [
                    {
                        "id": "primary",
                        "api_type": "openai_image",
                        "base_url": "https://api.example.com/v1",
                        "api_keys": "key-1",
                        "model": "primary-model",
                        "default_size": "1536x1024",
                    }
                ],
                "router_config": {"chain_text2img": "primary"},
            },
            str(PLUGIN_DIR),
        )
        calls = []

        class FakeProvider:
            async def generate_image(self, prompt, **kwargs):
                calls.append(kwargs)
                return "https://cdn.example.com/out.png"

        original_create_provider = chain_manager_module.create_provider
        chain_manager_module.create_provider = lambda provider_config, session: FakeProvider()
        try:
            manager = ChainManager(config, session=object())
            await manager.run_chain_with_metadata("text2img", "draw a cat")
            await manager.run_chain_with_metadata("text2img", "draw a cat", size="1024x1024")
            await manager.run_chain_with_metadata("text2img", "draw a cat", resolution="2048x2048")

            self.assertEqual(calls[0]["size"], "1536x1024")
            self.assertEqual(calls[1]["size"], "1024x1024")
            self.assertNotIn("size", calls[2])
            self.assertEqual(calls[2]["resolution"], "2048x2048")
        finally:
            chain_manager_module.create_provider = original_create_provider

    async def test_backup_provider_uses_its_own_default_size(self):
        config = PluginConfig.from_dict(
            {
                "providers": [
                    {
                        "id": "primary",
                        "api_type": "openai_image",
                        "base_url": "https://api.example.com/v1",
                        "api_keys": "key-1",
                        "model": "primary-model",
                        "default_size": "1024x1024",
                    },
                    {
                        "id": "backup",
                        "api_type": "openai_image",
                        "base_url": "https://api.example.com/v1",
                        "api_keys": "key-2",
                        "model": "backup-model",
                        "default_size": "1536x1024",
                    },
                ],
                "router_config": {"chain_text2img": "primary,backup"},
            },
            str(PLUGIN_DIR),
        )
        calls = []

        class FakeProvider:
            def __init__(self, provider_config):
                self.provider_config = provider_config

            async def generate_image(self, prompt, **kwargs):
                calls.append((self.provider_config.id, kwargs))
                if self.provider_config.id == "primary":
                    raise RuntimeError("primary failed")
                return "https://cdn.example.com/out.png"

        original_create_provider = chain_manager_module.create_provider
        chain_manager_module.create_provider = lambda provider_config, session: FakeProvider(provider_config)
        try:
            manager = ChainManager(config, session=object())
            result = await manager.run_chain_with_metadata("text2img", "draw a cat")

            self.assertEqual(result.provider_id, "backup")
            self.assertEqual(calls[0], ("primary", {"size": "1024x1024"}))
            self.assertEqual(calls[1], ("backup", {"size": "1536x1024"}))
        finally:
            chain_manager_module.create_provider = original_create_provider

    async def test_gemini_chain_allows_default_base_url(self):
        config = PluginConfig.from_dict(
            {
                "providers": [
                    {
                        "id": "gemini",
                        "api_type": "gemini_official",
                        "base_url": "",
                        "api_keys": "key-1",
                        "model": "",
                    }
                ],
                "router_config": {"chain_text2img": "gemini"},
            },
            str(PLUGIN_DIR),
        )
        calls = []

        class FakeProvider:
            async def generate_image(self, prompt, **kwargs):
                calls.append((prompt, kwargs))
                return "data:image/png;base64," + _long_b64()

        original_create_provider = chain_manager_module.create_provider
        chain_manager_module.create_provider = lambda provider_config, session: FakeProvider()
        try:
            manager = ChainManager(config, session=object())
            result = await manager.run_chain_with_metadata("text2img", "draw a cat")

            self.assertTrue(result.image_url.startswith("data:image/png;base64,"))
            self.assertEqual(result.provider_id, "gemini")
            self.assertEqual(result.model, "gemini-3.1-flash-image")
            self.assertEqual(calls, [("draw a cat", {})])
        finally:
            chain_manager_module.create_provider = original_create_provider


class VideoSuccessMetadataTest(unittest.TestCase):
    def test_success_text_respects_metadata_toggles(self):
        config = PluginConfig.from_dict(
            {"show_generation_time": True, "show_request_model": False},
            str(PLUGIN_DIR),
        )
        text = VideoManager(config)._build_success_text(65.2, "veo-3")

        self.assertIn("生成耗时", text)
        self.assertIn("65.2s", text)
        self.assertNotIn("请求模型", text)
        self.assertNotIn("veo-3", text)

        config = PluginConfig.from_dict(
            {"show_generation_time": False, "show_request_model": True},
            str(PLUGIN_DIR),
        )
        manager = VideoManager(config)
        provider = ProviderConfig(
            id="video_node",
            api_type="async_task",
            base_url="https://api.example.com/v1",
            api_keys=["key"],
            model="veo-3",
            timeout=300.0,
        )
        text = manager._build_success_text(
            65.2,
            manager._effective_request_model(provider, {"model": "video-override"}),
        )

        self.assertNotIn("生成耗时", text)
        self.assertIn("请求模型：video-override", text)

    def test_success_text_can_hide_metadata_for_llm_tools(self):
        config = PluginConfig.from_dict(
            {"show_generation_time": True, "show_request_model": True},
            str(PLUGIN_DIR),
        )
        text = VideoManager(config)._build_success_text(65.2, "veo-3", include_metadata=False)

        self.assertNotIn("生成耗时", text)
        self.assertNotIn("请求模型", text)
        self.assertNotIn("veo-3", text)


    def test_video_provider_default_size_is_used_when_request_omits_size(self):
        config = PluginConfig.from_dict(
            {"video_providers": [{"id": "video_node", "default_size": "1280x720"}]},
            str(PLUGIN_DIR),
        )
        manager = VideoManager(config)
        provider = config.video_providers[0]

        self.assertEqual(manager._apply_provider_defaults(provider, {})["size"], "1280x720")
        self.assertEqual(manager._apply_provider_defaults(provider, {"size": "1920x1080"})["size"], "1920x1080")
        self.assertEqual(manager._apply_provider_defaults(provider, {"resolution": "720p"}), {"resolution": "720p"})

