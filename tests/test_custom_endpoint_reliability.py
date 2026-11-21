# -*- coding: utf-8 -*-
"""custom_endpoint 回归套件拆分母文件（断言与原文件逐字一致）。"""
import ast
import asyncio
import json
import tempfile
import threading
import types
import unittest

from _custom_endpoint_helpers import (FakeClientSession, FakePost, FakeResponse, FakeSession, OmniDrawPlugin, OpenAIProvider, Path, PluginConfig, ProviderConfig, VideoManager, VideoTaskError, base_module, main_config, main_module, video_manager_module)  # noqa: F401


class ReliabilityBoundaryRegressionTest(unittest.IsolatedAsyncioTestCase):
    def _quota_plugin(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.plugin_config = types.SimpleNamespace(enable_daily_limit=True, daily_image_limit=2)
        plugin._usage_stats = {"date": plugin._today_key(), "total": 0, "users": {}}
        plugin._quota_reservations = {}
        plugin._usage_lock = threading.RLock()
        plugin._access_status = lambda event, refresh=True: {
            "user_id": "user-1",
            "group_id": "",
            "allowed": True,
            "unlimited": False,
            "level": "limited",
            "reason": "",
        }
        plugin._get_event_user_id = lambda event: "user-1"
        plugin._permission_denied_message = lambda event: ""
        return plugin

    def test_quota_reservation_is_atomic_under_real_thread_contention(self):
        plugin = self._quota_plugin()
        start = threading.Barrier(3)
        results = []
        failures = []
        result_lock = threading.Lock()

        def reserve():
            try:
                start.wait(timeout=5)
                result = plugin._reserve_image_quota(object(), 2)
                with result_lock:
                    results.append(result)
            except BaseException as exc:
                with result_lock:
                    failures.append(exc)

        workers = [threading.Thread(target=reserve) for _ in range(2)]
        for worker in workers:
            worker.start()
        start.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=5)

        self.assertFalse(failures)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(sorted(reserved for _, reserved in results), [0, 2])
        self.assertEqual(sum(1 for message, _ in results if not message), 1)
        self.assertEqual(sum(1 for message, _ in results if "额度不足" in message), 1)

    async def test_plugin_generation_releases_quota_reservation_when_cancelled(self):
        plugin = self._quota_plugin()
        plugin.plugin_config.max_batch_count = 2
        plugin._refresh_from_native_config_if_changed = lambda: None
        plugin._validated_plugin_image_refs = lambda refs, allow_local_refs=False: []

        async def cancel_generation(*args, **kwargs):
            raise asyncio.CancelledError()

        plugin._run_text2img_generation = cancel_generation

        with self.assertRaises(asyncio.CancelledError):
            await plugin.generate_images_for_plugin(
                prompt="draw a cat",
                event=object(),
                record_usage=True,
            )

        self.assertEqual(plugin._quota_reservations, {})

    async def test_chat_generation_handlers_release_reservation_when_cancelled(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.plugin_config = types.SimpleNamespace(
            presets={"胶片少女": "35mm film portrait"},
            draw_pending_message="pending",
            selfie_pending_message="pending",
            persona_name="默认助理",
            persona_ref_images=[],
            chains={"selfie": ["selfie-node"]},
            verbose_report=False,
        )
        plugin.cmd_parser = main_module.CommandParser()
        plugin._permission_denied_message = lambda event: ""
        plugin._reserve_image_quota = lambda event, count: ("", 1)
        released = []
        plugin._release_image_quota = lambda event, count: released.append(count)
        plugin._get_event_text = lambda event: "胶片少女"
        plugin._event_is_at_or_wake_command = lambda event: True
        plugin._get_event_images = lambda *args, **kwargs: ["reference"]
        plugin._extract_command_message = lambda event, command, fallback="": fallback or "prompt"
        plugin.prompt_optimizer = types.SimpleNamespace(
            optimize=lambda prompt, count, session=None, **kwargs: self._return_prompts(prompt)
        )
        plugin._load_locked_outfit = lambda: ""
        plugin.persona_manager = types.SimpleNamespace(
            build_persona_prompt=lambda prompt: (prompt, {})
        )

        async def cancel_reference_processing(*args, **kwargs):
            raise asyncio.CancelledError()

        plugin._process_and_save_images = cancel_reference_processing

        async def consume(generator):
            async for _ in generator:
                pass

        handlers = (
            plugin.on_message_preset(object()),
            plugin.cmd_draw(object(), "prompt"),
            plugin.cmd_selfie(object(), "prompt"),
        )
        for handler in handlers:
            with self.assertRaises(asyncio.CancelledError):
                await consume(handler)

        self.assertEqual(released, [1, 1, 1])

    async def _return_prompts(self, prompt):
        return [prompt]

    async def test_llm_image_tools_release_reservation_when_cancelled(self):
        plugin = self._quota_plugin()
        plugin.plugin_config.max_batch_count = 2

        async def cancel_generation(*args, **kwargs):
            raise asyncio.CancelledError()

        plugin._run_text2img_generation = cancel_generation
        plugin._run_selfie_generation = cancel_generation

        with self.assertRaises(asyncio.CancelledError):
            await plugin.tool_generate_image(object(), "draw a cat")
        self.assertEqual(plugin._quota_reservations, {})

        with self.assertRaises(asyncio.CancelledError):
            await plugin.tool_generate_selfie(object(), "look at camera")
        self.assertEqual(plugin._quota_reservations, {})

    async def test_config_apply_failure_restores_memory_and_persisted_file(self):
        plugin = object.__new__(OmniDrawPlugin)
        old_config = {"verbose_report": False, "providers": []}
        old_runtime = tuple(object() for _ in range(4))

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin.data_dir = temp_dir
            plugin.config_path = str(Path(temp_dir) / "omnidraw_persist_config.json")
            plugin._config_lock = threading.RLock()
            plugin._persist_config_mtime = 0.0
            plugin.raw_config = json.loads(json.dumps(old_config))
            (
                plugin.plugin_config,
                plugin.persona_manager,
                plugin.video_manager,
                plugin.prompt_optimizer,
            ) = old_runtime
            plugin._persist_config()

            plugin._normalize_saved_page_images = lambda config: None
            plugin._prepare_runtime_config = lambda config: config
            plugin._safe_update_context_config = lambda: None

            def fail_apply(candidate):
                plugin.plugin_config = object()
                plugin.persona_manager = object()
                raise RuntimeError("apply failed")

            plugin._apply_runtime_config = fail_apply

            async def get_json(*args, **kwargs):
                return {"verbose_report": True}

            original_request = main_config.request
            main_config.request = types.SimpleNamespace(content_length=None, get_json=get_json)
            try:
                response, status = await plugin.save_config_handler()
            finally:
                main_config.request = original_request

            self.assertEqual(status, 500)
            self.assertFalse(response["args"][0]["success"])
            self.assertEqual(plugin.raw_config, old_config)
            self.assertIs(plugin.plugin_config, old_runtime[0])
            self.assertIs(plugin.persona_manager, old_runtime[1])
            self.assertIs(plugin.video_manager, old_runtime[2])
            self.assertIs(plugin.prompt_optimizer, old_runtime[3])
            self.assertEqual(json.loads(Path(plugin.config_path).read_text(encoding="utf-8")), old_config)

    async def test_reference_redirect_is_revalidated_before_following(self):
        config = ProviderConfig(
            id="redirect-node",
            api_type="openai_image",
            base_url="https://api.example.com/v1",
            api_keys=["key-1"],
            model="image-model",
            timeout=30.0,
        )

        class RedirectSession:
            def __init__(self):
                self.gets = []

            def get(self, url, **kwargs):
                self.gets.append({"url": url, **kwargs})
                response = FakeResponse({}, status=302)
                response.headers = {"Location": "http://127.0.0.1/private.png"}
                return FakePost(response)

        session = RedirectSession()
        provider = OpenAIProvider(config, session)
        checked_urls = []
        original_validate = base_module._validate_remote_reference_url

        def validate_each_hop(url):
            checked_urls.append(url)
            if "127.0.0.1" in url:
                raise RuntimeError("private redirect blocked")

        base_module._validate_remote_reference_url = validate_each_hop
        try:
            with self.assertRaisesRegex(RuntimeError, "private redirect blocked"):
                await provider.read_reference_image("https://public.example/ref.png")
        finally:
            base_module._validate_remote_reference_url = original_validate

        self.assertEqual(
            checked_urls,
            ["https://public.example/ref.png", "http://127.0.0.1/private.png"],
        )
        self.assertEqual(len(session.gets), 1)

    async def test_video_async_submission_rejects_non_object_json(self):
        provider = ProviderConfig(
            id="video-invalid-submit",
            api_type="async_task",
            base_url="https://invalid-submit.example.com/v1",
            api_keys=["key-a"],
            model="video-model",
            timeout=30.0,
        )
        session = FakeSession(FakeResponse(["task-1"]))
        manager = VideoManager(types.SimpleNamespace())

        with self.assertRaisesRegex(VideoTaskError, "提交接口返回的 JSON 不是对象"):
            await manager._fetch_video_from_api(provider, "make a video", session)
        self.assertEqual(len(session.posts), 1)
        self.assertEqual(session.gets, [])

    async def test_video_polling_honors_zero_second_absolute_deadline(self):
        provider = ProviderConfig(
            id="video-zero-deadline",
            api_type="async_task",
            base_url="https://zero-deadline.example.com/v1",
            api_keys=["key-a"],
            model="video-model",
            timeout=0.0,
        )

        class SubmitOnlySession:
            def __init__(self):
                self.posts = []
                self.gets = []

            def post(self, url, **kwargs):
                self.posts.append({"url": url, **kwargs})
                return FakePost(FakeResponse({"id": "task-1"}))

            def get(self, url, **kwargs):
                self.gets.append({"url": url, **kwargs})
                return FakePost(FakeResponse({"status": "SUCCESS", "url": "https://cdn.example.com/out.mp4"}))

        session = SubmitOnlySession()
        manager = VideoManager(types.SimpleNamespace())

        with self.assertRaisesRegex(VideoTaskError, "轮询超时"):
            await manager._fetch_video_from_api(provider, "make a video", session)
        self.assertEqual(len(session.posts), 1)
        self.assertEqual(session.gets, [])

    async def test_video_polling_propagates_cancellation(self):
        provider = ProviderConfig(
            id="video-cancel",
            api_type="async_task",
            base_url="https://cancel.example.com/v1",
            api_keys=["key-a"],
            model="video-model",
            timeout=30.0,
        )

        class CancelledRequest:
            async def __aenter__(self):
                raise asyncio.CancelledError()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class CancelledSession:
            def get(self, url, **kwargs):
                return CancelledRequest()

        manager = VideoManager(types.SimpleNamespace())
        with self.assertRaises(asyncio.CancelledError):
            await manager._poll_task_result(provider, "task-1", CancelledSession(), api_key="key-a")

    async def test_draw_failure_after_pending_message_returns_final_error_and_releases_quota(self):
        from test_custom_endpoint_preset import CommandPendingSendTest

        helper = CommandPendingSendTest(methodName="test_draw_sends_pending_message_without_yielding_before_chain")
        plugin = helper._plugin()
        plugin.plugin_config.draw_error_message = "绘制失败: {error}"
        plugin.plugin_config.selfie_error_message = "自拍失败: {error}"
        plugin._reserve_image_quota = lambda event, count=1: ("", 1)
        released = []
        plugin._release_image_quota = lambda event, count: released.append(count)
        sequence = []

        class FailingChainManager:
            def __init__(self, config, session):
                pass

            async def run_chain_with_metadata(self, chain_name, prompt, **kwargs):
                sequence.append("generate")
                raise RuntimeError("provider failed")

        class OrderedEvent(helper.FakeEvent):
            async def send(self, result):
                sequence.append("pending")
                await super().send(result)

        event = OrderedEvent()
        original_client_session = main_module.aiohttp.ClientSession
        original_chain_manager = main_module.ChainManager
        main_module.aiohttp.ClientSession = FakeClientSession
        main_module.ChainManager = FailingChainManager
        try:
            results = [item async for item in plugin.cmd_draw(event, "海边日落")]
        finally:
            main_module.aiohttp.ClientSession = original_client_session
            main_module.ChainManager = original_chain_manager

        self.assertEqual(sequence, ["pending", "generate"])
        self.assertEqual(results, [("plain", "绘制失败: provider failed")])
        self.assertEqual(released, [1])
        self.assertEqual(plugin._recorded_count, 0)

    def test_command_and_provider_filters_block_all_reserved_generation_fields(self):
        plugin = object.__new__(OmniDrawPlugin)
        command_payload = {
            "PROMPT": "override",
            "Messages": [],
            "TOOLS": [],
            "Input": "override",
            "N": 99,
            "Stream": True,
            "USER_REF": "secret",
            "user_refs": ["secret"],
            "Persona_Ref": "secret",
            "persona_refs": ["secret"],
            "size": "1024x1024",
        }
        self.assertEqual(plugin._sanitize_generation_kwargs(command_payload), {"size": "1024x1024"})

        provider_payload = {
            **command_payload,
            "MODEL": "override",
            "Contents": [],
            "Image": "secret",
            "images": ["secret"],
            "IMAGE_URL": "secret",
        }
        self.assertEqual(
            base_module.filter_provider_api_kwargs(provider_payload, "node-1"),
            {"size": "1024x1024"},
        )

    def test_admin_decorators_and_reserved_preset_names_are_source_protected(self):
        tree = ast.parse(Path(main_module.__file__).read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for function_name in (
            "cmd_switch_persona",
            "cmd_switch_chain",
            "cmd_switch_model",
            "cmd_add_preset",
            "cmd_delete_preset",
        ):
            with self.subTest(function=function_name):
                decorators = [ast.unparse(item) for item in functions[function_name].decorator_list]
                self.assertIn("filter.permission_type(filter.PermissionType.ADMIN)", decorators)

        expected_reserved = {
            "万象帮助",
            "定格帮助",
            "查看预设",
            "极速宏",
            "添加预设",
            "删除预设",
            "清理缓存",
            "签到",
            "人设",
            "切换人设",
            "切换链路",
            "切换模型",
            "画",
            "自拍",
            "视频",
        }
        self.assertTrue(expected_reserved.issubset(main_module.RESERVED_PRESET_NAMES))
        plugin = object.__new__(OmniDrawPlugin)
        plugin._command_prefixes = lambda: ["/"]
        for name in expected_reserved:
            with self.subTest(preset=name):
                self.assertIn("内置指令同名", plugin._validate_preset_name(name))

        cleaned = plugin._clean_runtime_config({
            "presets": [
                "画:不应覆盖内置命令",
                {"name": "切换模型", "prompt": "不应覆盖内置命令"},
                "普通预设:保留",
            ]
        })
        self.assertEqual(cleaned["presets"], ["普通预设:保留"])

    def test_numeric_and_boolean_config_values_are_safely_normalized(self):
        raw_config = {
            "providers": [{
                "id": "image-node",
                "api_type": "openai_image",
                "base_url": "https://api.example.com/v1",
                "api_keys": ["key"],
                "model": "image-model",
                "timeout": "nan",
            }],
            "video_providers": [{
                "id": "video-node",
                "api_type": "async_task",
                "base_url": "https://video.example.com/v1",
                "api_keys": ["key"],
                "model": "video-model",
                "timeout": "inf",
            }],
            "optimizer_config": {
                "enable_optimizer": "false",
                "optimizer_timeout": "nan",
                "max_batch_count": 999,
            },
            "cache_config": {
                "enable_scheduled_cleanup": "0",
                "scheduled_cleanup_interval_hours": 0,
                "enable_size_limit_cleanup": "yes",
                "max_cache_size_mb": "inf",
            },
            "verbose_report": "false",
            "show_generation_time": "1",
            "show_request_model": "no",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            config = PluginConfig.from_dict(raw_config, temp_dir, cleanup_persona_refs=False)

        self.assertFalse(config.enable_optimizer)
        self.assertEqual(config.optimizer_timeout, 15.0)
        self.assertEqual(config.max_batch_count, 10)
        self.assertEqual(config.providers[0].timeout, 60.0)
        self.assertEqual(config.video_providers[0].timeout, 300.0)
        self.assertFalse(config.enable_scheduled_cleanup)
        self.assertEqual(config.scheduled_cleanup_interval_hours, 1)
        self.assertTrue(config.enable_size_limit_cleanup)
        self.assertEqual(config.max_cache_size_mb, 512)
        self.assertFalse(config.verbose_report)
        self.assertTrue(config.show_generation_time)
        self.assertFalse(config.show_request_model)

    def test_background_queue_rejects_over_capacity_and_closes_coroutine(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin._background_tasks = set(range(main_module.MAX_BACKGROUND_TASKS))
        released = []
        plugin._lease_cache_paths = lambda paths: list(paths)
        plugin._release_cache_paths = lambda paths: released.append(list(paths))

        async def never_started():
            await asyncio.sleep(0)

        coro = never_started()
        with self.assertRaisesRegex(RuntimeError, "队列已满"):
            plugin._create_background_task(coro, leased_paths=["leased.png"])

        self.assertEqual(released, [["leased.png"]])
        self.assertIsNone(coro.cr_frame)

    async def test_background_task_releases_lease_after_failure(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin._background_tasks = set()
        plugin._background_task_kinds = {}
        plugin._background_semaphore = None
        plugin._background_semaphore_loop = None
        plugin._lease_cache_paths = lambda paths: list(paths)
        released = []
        plugin._release_cache_paths = lambda paths: released.append(list(paths))

        async def fail():
            raise RuntimeError("background failed")

        task = plugin._create_background_task(fail(), kind="video", leased_paths=["leased.png"])
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        await asyncio.sleep(0)

        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(released, [["leased.png"]])
        self.assertNotIn(task, plugin._background_tasks)
        self.assertNotIn(task, plugin._background_task_kinds)

    async def test_background_semaphore_limits_simultaneous_tasks(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin._background_tasks = set()
        plugin._background_task_kinds = {}
        plugin._background_semaphore = None
        plugin._background_semaphore_loop = None
        plugin._lease_cache_paths = lambda paths: []
        plugin._release_cache_paths = lambda paths: None
        gate = asyncio.Event()
        saturated = asyncio.Event()
        started = 0
        active = 0
        peak = 0

        async def worker():
            nonlocal started, active, peak
            started += 1
            active += 1
            peak = max(peak, active)
            if started == main_module.MAX_ACTIVE_BACKGROUND_TASKS:
                saturated.set()
            try:
                await gate.wait()
            finally:
                active -= 1

        tasks = [
            plugin._create_background_task(worker(), kind="video")
            for _ in range(main_module.MAX_ACTIVE_BACKGROUND_TASKS + 1)
        ]
        await asyncio.wait_for(saturated.wait(), timeout=2)
        await asyncio.sleep(0)

        self.assertEqual(started, main_module.MAX_ACTIVE_BACKGROUND_TASKS)
        self.assertEqual(peak, main_module.MAX_ACTIVE_BACKGROUND_TASKS)

        gate.set()
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        self.assertEqual(started, main_module.MAX_ACTIVE_BACKGROUND_TASKS + 1)
        self.assertEqual(peak, main_module.MAX_ACTIVE_BACKGROUND_TASKS)

    async def test_background_task_closes_coroutine_when_cancelled_before_semaphore(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin._background_tasks = set()
        plugin._background_task_kinds = {}
        plugin._background_semaphore = asyncio.Semaphore(0)
        plugin._background_semaphore_loop = asyncio.get_running_loop()
        plugin._lease_cache_paths = lambda paths: list(paths)
        released = []
        plugin._release_cache_paths = lambda paths: released.append(list(paths))

        async def never_started():
            await asyncio.sleep(0)

        coro = never_started()
        task = plugin._create_background_task(coro, kind="video", leased_paths=["queued.png"])
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        self.assertIsNone(coro.cr_frame)
        self.assertEqual(released, [["queued.png"]])

    async def test_video_error_text_redacts_provider_secrets(self):
        manager = object.__new__(VideoManager)
        response = FakeResponse({"error": {"message": "api_key=video-secret"}}, status=401)

        message = await manager._read_error(response)

        self.assertNotIn("video-secret", message)
        self.assertIn("<redacted>", message)

    async def test_main_reference_redirect_is_revalidated_before_following(self):
        plugin = object.__new__(OmniDrawPlugin)
        checked_urls = []

        def validate_each_hop(url):
            checked_urls.append(url)
            if "127.0.0.1" in url:
                raise ValueError("private redirect blocked")

        plugin._validate_remote_image_url = validate_each_hop

        class RedirectSession:
            def __init__(self):
                self.gets = []

            def get(self, url, **kwargs):
                self.gets.append({"url": url, **kwargs})
                response = FakeResponse({}, status=302)
                response.headers = {"Location": "http://127.0.0.1/private.png"}
                return FakePost(response)

        session = RedirectSession()
        with self.assertRaisesRegex(ValueError, "private redirect blocked"):
            await plugin._download_remote_image(session, "https://public.example/ref.png", {})

        self.assertEqual(
            checked_urls,
            ["https://public.example/ref.png", "http://127.0.0.1/private.png"],
        )
        self.assertEqual(len(session.gets), 1)

    async def test_video_reference_redirect_is_revalidated_before_following(self):
        manager = VideoManager(types.SimpleNamespace())
        checked_urls = []
        original_validate = video_manager_module._validate_remote_reference_url

        def validate_each_hop(url):
            checked_urls.append(url)
            if "127.0.0.1" in url:
                raise VideoTaskError("private redirect blocked")

        class RedirectSession:
            def __init__(self):
                self.gets = []

            def get(self, url, **kwargs):
                self.gets.append({"url": url, **kwargs})
                response = FakeResponse({}, status=302)
                response.headers = {"Location": "http://127.0.0.1/private.png"}
                return FakePost(response)

        session = RedirectSession()
        video_manager_module._validate_remote_reference_url = validate_each_hop
        try:
            with self.assertRaisesRegex(VideoTaskError, "private redirect blocked"):
                await manager._encode_image_to_base64("https://public.example/ref.png", session)
        finally:
            video_manager_module._validate_remote_reference_url = original_validate

        self.assertEqual(
            checked_urls,
            ["https://public.example/ref.png", "http://127.0.0.1/private.png"],
        )
        self.assertEqual(len(session.gets), 1)

