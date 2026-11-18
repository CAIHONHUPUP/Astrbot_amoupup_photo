# -*- coding: utf-8 -*-
"""custom_endpoint 回归套件拆分母文件（断言与原文件逐字一致）。"""
import asyncio
import base64
import json
import tempfile
import threading
import types
import unittest

from _custom_endpoint_helpers import (CustomEndpointProvider, FakePost, FakeResponse, FakeSession, OmniDrawPlugin, OpenAIChatProvider, OpenAIProvider, PLUGIN_DIR, Path, ProviderConfig, VideoManager, base_module)  # noqa: F401


class SecurityAndLimitRegressionTest(unittest.IsolatedAsyncioTestCase):
    def test_cache_page_stats_do_not_expose_filesystem_paths(self):
        plugin = object.__new__(OmniDrawPlugin)
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin.data_dir = temp_dir
            cache_dir = Path(temp_dir) / "temp_images"
            cache_dir.mkdir(parents=True)
            (cache_dir / "sample.png").write_bytes(b"\x89PNG\r\n\x1a\n")

            stats = plugin._cache_stats_for_page()

        self.assertNotIn("path", stats["dirs"]["temp_images"])
        self.assertEqual(stats["dirs"]["temp_images"]["count"], 1)

    def test_main_image_magic_rejects_mp4_ftyp_and_accepts_avif(self):
        plugin = object.__new__(OmniDrawPlugin)

        self.assertFalse(plugin._looks_like_image_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20))
        self.assertTrue(plugin._looks_like_image_bytes(b"\x00\x00\x00\x18ftypavif" + b"\x00" * 20))

    def test_legacy_provider_api_keys_are_migrated_masked_and_restored(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin._page_image_tokens = {}
        legacy_config = {
            "providers": [{"id": "image-node", "API密钥": "legacy-image-secret"}],
            "video_providers": [{"id": "video-node", "API密钥": ["legacy-video-secret"]}],
        }

        plugin.raw_config = plugin._clean_runtime_config(legacy_config)
        self.assertEqual(plugin.raw_config["providers"][0]["api_keys"], "legacy-image-secret")
        self.assertEqual(plugin.raw_config["video_providers"][0]["api_keys"], ["legacy-video-secret"])
        self.assertNotIn("API密钥", plugin.raw_config["providers"][0])
        self.assertNotIn("API密钥", plugin.raw_config["video_providers"][0])

        page_config = plugin._config_for_page()
        serialized_page = json.dumps(page_config, ensure_ascii=False)
        self.assertNotIn("legacy-image-secret", serialized_page)
        self.assertNotIn("legacy-video-secret", serialized_page)
        self.assertNotIn("API密钥", serialized_page)

        plugin._restore_masked_api_keys(page_config)
        self.assertEqual(page_config["providers"][0]["api_keys"], ["legacy-image-secret"])
        self.assertEqual(page_config["video_providers"][0]["api_keys"], ["legacy-video-secret"])

    def test_canonical_empty_api_keys_do_not_resurrect_legacy_secret(self):
        plugin = object.__new__(OmniDrawPlugin)

        cleaned = plugin._clean_runtime_config({
            "providers": [{"id": "image-node", "api_keys": [], "API密钥": "stale-secret"}],
        })

        self.assertEqual(cleaned["providers"][0]["api_keys"], [])
        self.assertNotIn("API密钥", cleaned["providers"][0])

    def test_command_error_message_redacts_sensitive_exception_text(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.plugin_config = types.SimpleNamespace(
            draw_error_message="绘制失败: {error}",
            selfie_error_message="自拍失败: {error}",
            persona_name="默认助理",
        )
        secret = "AIza-command-secret-123456789"

        message = plugin._build_command_error_message(
            "cmd_draw",
            RuntimeError(f"request failed: https://api.example.com/run?key={secret}&size=1"),
        )

        self.assertNotIn(secret, message)
        self.assertIn("key=<redacted>", message)

        timeout_message = plugin._build_command_error_message(
            "cmd_draw",
            asyncio.TimeoutError(),
            error_kind="timeout",
        )
        self.assertIn("操作超时", timeout_message)

    async def test_llm_image_tools_redact_sensitive_errors_in_non_result_mode(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin._permission_denied_message = lambda event: ""
        plugin._normalize_count = lambda count: 1
        plugin._reserve_image_quota = lambda event, count: ("", 1)
        plugin._release_image_quota = lambda event, count: None
        secret = "sk-tool-secret-123456789"

        async def fail_generation(*args, **kwargs):
            raise RuntimeError(f"Authorization: Bearer {secret}")

        plugin._run_text2img_generation = fail_generation
        plugin._run_selfie_generation = fail_generation

        image_message = await plugin.tool_generate_image(object(), "draw a cat")
        selfie_message = await plugin.tool_generate_selfie(object(), "look at camera")

        self.assertNotIn(secret, image_message)
        self.assertNotIn(secret, selfie_message)
        self.assertIn("<redacted>", image_message)
        self.assertIn("<redacted>", selfie_message)

    def test_page_config_masks_and_restores_api_keys(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.raw_config = {
            "providers": [{"id": "node_1", "api_keys": ["secret-key-one", "secret-key-two"]}],
            "video_providers": [],
        }
        page_config = json.loads(json.dumps(plugin.raw_config))

        plugin._mask_config_api_keys(page_config)

        masked = page_config["providers"][0]["api_keys"]
        self.assertNotIn("secret-key-one", masked)
        self.assertNotIn("secret-key-two", masked)
        self.assertTrue(page_config["providers"][0]["api_keys_configured"])

        payload = {"providers": [{"id": "node_1", "api_keys": masked}]}
        plugin._restore_masked_api_keys(payload)
        self.assertEqual(payload["providers"][0]["api_keys"], ["secret-key-one", "secret-key-two"])

    def test_page_config_restores_reordered_and_deleted_masked_api_keys_by_suffix(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.raw_config = {
            "providers": [{
                "id": "node_1",
                "api_keys": ["secret-key-1111", "secret-key-2222", "secret-key-3333"],
            }],
            "video_providers": [],
        }

        payload = {"providers": [{
            "id": "node_1",
            "api_keys": "\n".join([
                plugin._mask_api_key("secret-key-3333"),
                plugin._mask_api_key("secret-key-1111"),
            ]),
        }]}
        plugin._restore_masked_api_keys(payload)

        self.assertEqual(
            payload["providers"][0]["api_keys"],
            ["secret-key-3333", "secret-key-1111"],
        )

    def test_page_config_restores_duplicate_mask_suffixes_without_reusing_old_key(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.raw_config = {
            "providers": [{
                "id": "node_1",
                "api_keys": ["first-secret-same", "second-secret-same"],
            }],
            "video_providers": [],
        }

        payload = {"providers": [{
            "id": "node_1",
            "api_keys": "\n".join([
                plugin._mask_api_key("first-secret-same"),
                plugin._mask_api_key("second-secret-same"),
            ]),
        }]}
        plugin._restore_masked_api_keys(payload)

        self.assertEqual(
            payload["providers"][0]["api_keys"],
            ["first-secret-same", "second-secret-same"],
        )

    def test_native_config_refresh_persists_previous_config_when_runtime_apply_fails(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin._config_lock = threading.RLock()
        plugin._native_config_path = "native-config.json"
        plugin._native_config_signature = "old-signature"
        plugin._native_config_mtime = 1.0
        plugin._native_config = None
        plugin.raw_config = {"verbose_report": False}
        plugin.plugin_config = object()
        plugin.persona_manager = object()
        plugin.video_manager = object()
        plugin.prompt_optimizer = object()
        persisted = []

        plugin._get_mtime = lambda path: 2.0
        plugin._file_signature = lambda path: "new-signature"
        plugin._load_json_file = lambda path: {"verbose_report": True}
        plugin._clean_runtime_config = lambda config: dict(config)
        plugin._prepare_runtime_config = lambda config: dict(config)
        plugin._persist_config = lambda: persisted.append(dict(plugin.raw_config))

        def fail_apply(config):
            raise RuntimeError("apply failed")

        plugin._apply_runtime_config = fail_apply

        with self.assertRaisesRegex(RuntimeError, "apply failed"):
            plugin._refresh_from_native_config_if_changed()

        self.assertEqual(plugin.raw_config, {"verbose_report": False})
        self.assertEqual(
            persisted,
            [{"verbose_report": True}, {"verbose_report": False}],
        )
        self.assertEqual(plugin._native_config_signature, "old-signature")
        self.assertEqual(plugin._native_config_mtime, 1.0)

    def test_empty_newer_native_config_does_not_replace_persisted_payload_at_startup(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin._sync_native_active_persona_upload = lambda config: None

        with tempfile.TemporaryDirectory() as temp_dir:
            native_path = Path(temp_dir) / "native.json"
            persisted_path = Path(temp_dir) / "persisted.json"
            native_path.write_text("{}", encoding="utf-8")
            persisted_config = {
                "providers": [{"id": "node_1", "api_keys": ["secret-key"]}],
            }
            persisted_path.write_text(json.dumps(persisted_config), encoding="utf-8")
            plugin._native_config_path = str(native_path)
            plugin.config_path = str(persisted_path)
            plugin._native_config_mtime = 2.0
            plugin._persist_config_mtime = 1.0

            loaded = plugin._load_initial_config({})

        self.assertEqual(loaded, persisted_config)

    def test_newer_native_config_with_payload_replaces_persisted_payload_at_startup(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin._sync_native_active_persona_upload = lambda config: None
        native_config = {
            "providers": [{"id": "native-node", "api_keys": ["native-secret"]}],
        }
        persisted_config = {
            "providers": [{"id": "persisted-node", "api_keys": ["persisted-secret"]}],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            native_path = Path(temp_dir) / "native.json"
            persisted_path = Path(temp_dir) / "persisted.json"
            native_path.write_text(json.dumps(native_config), encoding="utf-8")
            persisted_path.write_text(json.dumps(persisted_config), encoding="utf-8")
            plugin._native_config_path = str(native_path)
            plugin.config_path = str(persisted_path)
            plugin._native_config_mtime = 2.0
            plugin._persist_config_mtime = 1.0

            loaded = plugin._load_initial_config(native_config)

        self.assertEqual(loaded, native_config)

    def test_page_preview_rejects_local_files_outside_plugin_data_dir(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.data_dir = str(PLUGIN_DIR / ".pytest_cache" / "page_security" / "plugin_data")
        plugin._page_image_tokens = {}
        inside = Path(plugin.data_dir) / "persona_refs" / "inside.png"
        outside = PLUGIN_DIR / ".pytest_cache" / "page_security" / "outside.png"
        inside.parent.mkdir(parents=True, exist_ok=True)
        outside.parent.mkdir(parents=True, exist_ok=True)
        image_bytes = b"\x89PNG\r\n\x1a\n" + b"test-image"
        inside.write_bytes(image_bytes)
        outside.write_bytes(image_bytes)

        self.assertTrue(plugin._image_ref_for_page(str(inside)).startswith("data:image/png;base64,"))
        self.assertEqual(plugin._image_ref_for_page(str(outside)), "")

    def test_quota_reservation_blocks_concurrent_overcommit(self):
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

        error, reserved = plugin._reserve_image_quota(object(), 2)
        self.assertEqual(error, "")
        self.assertEqual(reserved, 2)
        error, reserved = plugin._reserve_image_quota(object(), 1)
        self.assertIn("额度不足", error)
        self.assertEqual(reserved, 0)
        plugin._release_image_quota(object(), 2)
        self.assertEqual(plugin._reserve_image_quota(object(), 1), ("", 1))

    async def test_provider_rejects_loopback_reference_before_request(self):
        config = ProviderConfig(
            id="node_1",
            api_type="openai_image",
            base_url="https://api.example.com/v1",
            api_keys=["key-1"],
            model="image-model",
            timeout=30.0,
        )
        session = FakeSession(FakeResponse({"data": [{"url": "https://cdn.example.com/out.png"}]}))
        provider = OpenAIProvider(config, session)

        with self.assertRaisesRegex(RuntimeError, "不允许访问"):
            await provider.generate_image("draw a cat", user_refs=["http://127.0.0.1/ref.png"])
        self.assertEqual(session.gets, [])

    async def test_provider_rejects_plain_text_mislabeled_as_image(self):
        config = ProviderConfig(
            id="node_1",
            api_type="openai_image",
            base_url="https://api.example.com/v1",
            api_keys=["key-1"],
            model="image-model",
            timeout=30.0,
        )
        session = FakeSession(FakeResponse({"data": [{"url": "https://cdn.example.com/out.png"}]}))
        provider = OpenAIProvider(config, session)
        reference = "data:image/png;base64," + base64.b64encode(b"plain text").decode("ascii")

        with self.assertRaisesRegex(RuntimeError, "不是支持的图片格式"):
            await provider.generate_image("draw a cat", user_refs=[reference])
        self.assertEqual(session.posts, [])

    async def test_openai_chat_non_success_response_honors_body_limit(self):
        config = ProviderConfig(
            id="chat-node",
            api_type="openai_chat",
            base_url="https://api.example.com/v1",
            api_keys=["key-1"],
            model="chat-image-model",
            timeout=30.0,
        )
        response = FakeResponse({"error": {"message": "too large"}}, status=400)
        response.headers = {"Content-Length": str(base_module.MAX_PROVIDER_RESPONSE_BYTES + 1)}
        provider = OpenAIChatProvider(config, FakeSession(response))

        with self.assertRaisesRegex(RuntimeError, "响应体超过"):
            await provider.generate_image("draw a cat")

    async def test_custom_image_json_endpoint_rejects_more_than_three_refs(self):
        endpoint = "https://api.example.com/v1/images/generations"
        config = ProviderConfig(
            id="custom_node",
            api_type="custom_endpoint",
            base_url=endpoint,
            api_keys=["key-1"],
            model="image-model",
            timeout=30.0,
        )
        session = FakeSession(FakeResponse({"data": [{"url": "https://cdn.example.com/out.png"}]}))
        provider = CustomEndpointProvider(config, session)
        refs = [
            "data:image/png;base64," + base64.b64encode(f"image-{index}".encode() * 20).decode("ascii")
            for index in range(4)
        ]

        with self.assertRaisesRegex(ValueError, "最多支持 3 张"):
            await provider.generate_image("edit a cat", user_refs=refs)
        self.assertEqual(session.posts, [])

    def test_provider_filter_blocks_underscore_image_fields(self):
        filtered = base_module.filter_provider_api_kwargs(
            {"image_1": "secret", "image2": "secret", "size": "1024x1024"},
            "node_1",
        )
        self.assertEqual(filtered, {"size": "1024x1024"})

    def test_api_key_rotation_scope_is_isolated_by_provider_id(self):
        scope = "image:openai_image:https://api.example.com/v1"

        self.assertEqual(base_module.next_api_key("node-a", ["a1", "a2"], scope=scope), "a1")
        self.assertEqual(base_module.next_api_key("node-b", ["b1", "b2"], scope=scope), "b1")
        self.assertEqual(base_module.next_api_key("node-a", ["a1", "a2"], scope=scope), "a2")
        self.assertEqual(base_module.next_api_key("node-b", ["b1", "b2"], scope=scope), "b2")

    def test_cache_cleanup_skips_leased_reference_file(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.data_dir = str(PLUGIN_DIR / ".pytest_cache" / "lease_security")
        plugin._cache_lease_lock = threading.RLock()
        plugin._cache_leases = {}
        image_path = Path(plugin.data_dir) / "user_refs" / "leased.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nleased")
        item = {
            "cache_name": "user_refs",
            "path": str(image_path),
            "bytes": image_path.stat().st_size,
            "mtime": image_path.stat().st_mtime,
        }

        lease = plugin._lease_cache_paths([str(image_path)])
        result = plugin._delete_cache_files([item], reason="test")
        self.assertEqual(result["deleted_count"], 0)
        self.assertTrue(image_path.exists())

        plugin._release_cache_paths(lease)
        result = plugin._delete_cache_files([item], reason="test")
        self.assertEqual(result["deleted_count"], 1)
        self.assertFalse(image_path.exists())

    def test_cache_cleanup_rechecks_lease_acquired_after_initial_snapshot(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.data_dir = str(PLUGIN_DIR / ".pytest_cache" / "lease_race_security")
        plugin._cache_lease_lock = threading.RLock()
        plugin._cache_leases = {}
        image_path = Path(plugin.data_dir) / "user_refs" / "leased-late.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nleased-late")
        item = {
            "cache_name": "user_refs",
            "path": str(image_path),
            "bytes": image_path.stat().st_size,
            "mtime": image_path.stat().st_mtime,
        }
        acquired_lease = []

        def delayed_items():
            acquired_lease.extend(plugin._lease_cache_paths([str(image_path)]))
            yield item

        try:
            result = plugin._delete_cache_files(delayed_items(), reason="test")
            self.assertEqual(result["deleted_count"], 0)
            self.assertEqual(result["skipped_count"], 1)
            self.assertTrue(image_path.exists())
        finally:
            plugin._release_cache_paths(acquired_lease)
            image_path.unlink(missing_ok=True)

    async def test_video_submit_and_poll_reuse_same_api_key(self):
        provider = ProviderConfig(
            id="video-key-lifecycle",
            api_type="async_task",
            base_url="https://api.example.com/v1",
            api_keys=["key-a", "key-b"],
            model="video-model",
            timeout=30.0,
        )

        class SequencedSession:
            def __init__(self):
                self.posts = []
                self.gets = []

            def post(self, url, **kwargs):
                self.posts.append({"url": url, **kwargs})
                return FakePost(FakeResponse({"id": "task-1"}))

            def get(self, url, **kwargs):
                self.gets.append({"url": url, **kwargs})
                return FakePost(FakeResponse({"status": "SUCCESS", "url": "https://cdn.example.com/out.mp4"}))

        session = SequencedSession()
        manager = VideoManager(types.SimpleNamespace())

        result = await manager._fetch_video_from_api(provider, "make a video", session)

        self.assertEqual(result, "https://cdn.example.com/out.mp4")
        self.assertEqual(session.posts[0]["headers"]["Authorization"], "Bearer key-a")
        self.assertEqual(session.gets[0]["headers"]["Authorization"], "Bearer key-a")

