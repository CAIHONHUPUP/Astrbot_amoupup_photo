# -*- coding: utf-8 -*-
"""B2c：OUTFIT LOCK 只在 action 没点名衣服时生效（/自拍 与 amo 两条路径）。"""

import json
import os
import pathlib
import sys
import tempfile
import time
import types
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)

import test_custom_endpoint as fixtures  # noqa: E402

main_module = fixtures.main_module
OmniDrawPlugin = fixtures.OmniDrawPlugin
ChainRunResult = fixtures.ChainRunResult
PromptOptimizer = fixtures.PromptOptimizer
optimizer_module = fixtures.prompt_optimizer_module
action_names_outfit = optimizer_module.action_names_outfit

LOCK = "米色雪纺衬衫，黑色包臀裙"
NO_CLOTHES_ACTION = "看着镜头微笑"


def _write_lock(data_dir):
    path = pathlib.Path(data_dir) / "amo_outfit.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"day": time.strftime("%Y-%m-%d"), "text": LOCK}, ensure_ascii=False),
        encoding="utf-8",
    )


class _FakeClientSession:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeEvent:
    def __init__(self):
        self.sent = []

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)

    async def send(self, result):
        self.sent.append(result)


def _make_plugin(data_dir):
    plugin = object.__new__(OmniDrawPlugin)
    plugin.data_dir = str(data_dir)
    plugin.plugin_config = types.SimpleNamespace(
        max_batch_count=4,
        selfie_pending_message="pending {command}",
        persona_name="默认助手",
        verbose_report=False,
        persona_ref_images=["dummy-persona.png"],
        chains={"selfie": ["selfie-node"]},
    )
    plugin.cmd_parser = main_module.CommandParser()
    plugin.optimizer_calls = []
    plugin.chain_calls = []

    async def optimize(prompt, count=1, session=None, **kwargs):
        plugin.optimizer_calls.append({"prompt": prompt, "count": count, **kwargs})
        return [f"optimized {prompt}"]

    plugin.prompt_optimizer = types.SimpleNamespace(optimize=optimize)
    plugin.persona_manager = types.SimpleNamespace(
        build_persona_prompt=lambda action: (
            f"persona base, {action}", {"persona_ref": "persona-default.png"}
        )
    )
    plugin._refresh_from_native_config_if_changed = lambda: None
    plugin._permission_denied_message = lambda event: ""
    plugin._image_quota_error_message = lambda event, count=1: ""
    plugin._reserve_image_quota = lambda event, count=1: ("", 0)
    plugin._complete_image_quota = lambda event, reserved, count: None
    plugin._release_image_quota = lambda event, reserved: None
    plugin._record_generated_images = lambda event, count=1: None
    plugin._parse_extra_params = lambda extra: {}
    plugin._prune_cache_if_needed = lambda *args, **kwargs: None
    plugin._lease_cache_paths = lambda paths: list(paths or [])
    plugin._release_cache_paths = lambda paths: None
    plugin._get_event_images = lambda *args, **kwargs: []
    plugin._extract_command_message = lambda event, command, fallback: fallback

    async def process_refs(refs, session=None, allow_local_refs=False):
        return [f"safe:{ref}" for ref in refs]

    plugin._process_and_save_images = process_refs
    plugin._build_image_success_components = lambda result, elapsed: [
        {"result": result.image_url}
    ]

    async def send_plain(event, message):
        return None

    plugin._send_plain_message = send_plain
    return plugin


def _patch_chain(prompt_log):
    class FakeChainManager:
        def __init__(self, config, session):
            pass

        async def run_chain_with_metadata(self, chain_name, prompt, **kwargs):
            prompt_log.append({"chain": chain_name, "prompt": prompt, **kwargs})
            return ChainRunResult(
                image_url="https://cdn.example.com/selfie.png",
                provider_id="selfie_node",
                model="model_a",
                elapsed_seconds=1.0,
            )

    return FakeChainManager


class ActionNamesOutfitTests(unittest.TestCase):
    def test_chinese_clothing_words(self):
        for action in ("穿宽大卫衣拍一张", "换上黑色包臀裙", "白衬衫", "牛仔裤",
                       "外面套件外套", "运动鞋", "泳衣",
                       "穿黑色比基尼拍一张", "黑色三点式"):
            with self.subTest(action=action):
                self.assertTrue(action_names_outfit(action))

    def test_english_clothing_words(self):
        for action in ("wearing a red hoodie", "in a black skirt",
                       "put on a coat", "white shirt and jeans", "sneakers"):
            with self.subTest(action=action):
                self.assertTrue(action_names_outfit(action))

    def test_mixed_language(self):
        self.assertTrue(action_names_outfit("穿oversized hoodie在窗边拍一张"))

    def test_empty_action_never_counts(self):
        self.assertFalse(action_names_outfit(""))
        self.assertFalse(action_names_outfit(None))
        self.assertFalse(action_names_outfit("   "))

    def test_accessory_only_does_not_count(self):
        # P1 反向：只提围巾/帽子/项链，不算点名衣服，仍应锁今日穿搭。
        self.assertFalse(action_names_outfit("戴一条围巾和帽子拍一张"))
        self.assertFalse(action_names_outfit("wearing a scarf and a hat"))

    def test_non_clothing_action_does_not_count(self):
        self.assertFalse(action_names_outfit("看着镜头微笑"))
        self.assertFalse(action_names_outfit("穿过人群走过来"))


class ReferenceRuleTests(unittest.TestCase):
    def _rules(self, locked_outfit, action):
        optimizer = PromptOptimizer(types.SimpleNamespace())
        style = {"subject": "s", "clothing": "c", "environment": "e",
                 "lighting": "l", "camera": "cam", "quality": "q"}
        style_data, ref_rule, _scene, _pose = optimizer._apply_reference_rules(
            style, False, False, False, locked_outfit, "", "", raw_action=action,
        )
        return style_data, ref_rule

    def test_named_outfit_skips_lock(self):
        style, rule = self._rules(LOCK, "穿宽大卫衣拍一张")
        self.assertNotIn("9. OUTFIT LOCK", rule)
        self.assertEqual(style["clothing"], "c")

    def test_default_action_keeps_lock(self):
        style, rule = self._rules(LOCK, NO_CLOTHES_ACTION)
        self.assertIn("9. OUTFIT LOCK", rule)
        self.assertIn(LOCK, style["clothing"])

    def test_empty_action_keeps_lock(self):
        style, rule = self._rules(LOCK, "")
        self.assertIn("9. OUTFIT LOCK", rule)
        self.assertIn(LOCK, style["clothing"])

    def test_accessory_only_keeps_lock(self):
        style, rule = self._rules(LOCK, "戴一条围巾拍一张")
        self.assertIn("9. OUTFIT LOCK", rule)
        self.assertIn(LOCK, style["clothing"])

    def test_english_named_outfit_skips_lock(self):
        style, rule = self._rules(LOCK, "wearing a red hoodie by the window")
        self.assertNotIn("9. OUTFIT LOCK", rule)
        self.assertEqual(style["clothing"], "c")

    def test_mixed_language_named_outfit_skips_lock(self):
        style, rule = self._rules(LOCK, "穿oversized hoodie在窗边")
        self.assertNotIn("9. OUTFIT LOCK", rule)

    def test_no_locked_outfit_never_writes_rule(self):
        style, rule = self._rules("", "看着镜头微笑")
        self.assertNotIn("9. OUTFIT LOCK", rule)
        self.assertEqual(style["clothing"], "c")


class _SelfiePathBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omnidraw-outfit-b2c-")
        self.addCleanup(self.temp.cleanup)
        self.data_dir = pathlib.Path(self.temp.name)
        _write_lock(self.data_dir)
        self.plugin = _make_plugin(self.data_dir)

    async def _run_command(self, action):
        prompt_log = []
        original_session = main_module.aiohttp.ClientSession
        original_chain = main_module.ChainManager
        main_module.aiohttp.ClientSession = _FakeClientSession
        main_module.ChainManager = _patch_chain(prompt_log)
        try:
            results = [
                item async for item in self.plugin.cmd_selfie(_FakeEvent(), action)
            ]
        finally:
            main_module.aiohttp.ClientSession = original_session
            main_module.ChainManager = original_chain
        return prompt_log, results

    async def _run_plugin_api(self, action, **kwargs):
        prompt_log = []
        original_session = main_module.aiohttp.ClientSession
        original_chain = main_module.ChainManager
        main_module.aiohttp.ClientSession = _FakeClientSession
        main_module.ChainManager = _patch_chain(prompt_log)
        try:
            result = await self.plugin.generate_images_for_plugin(
                prompt=action, count=1, refs=[], mode="selfie", **kwargs,
            )
        finally:
            main_module.aiohttp.ClientSession = original_session
            main_module.ChainManager = original_chain
        return result, prompt_log


class CommandSelfieActionTests(_SelfiePathBase):
    async def test_command_named_outfit_skips_lock(self):
        prompt_log, _results = await self._run_command("穿红色卫衣拍一张")
        call = self.plugin.optimizer_calls[-1]
        self.assertEqual(call["locked_outfit"], "")
        final_prompt = prompt_log[-1]["prompt"]
        self.assertNotIn("she is wearing exactly", final_prompt)
        self.assertIn("穿红色卫衣", final_prompt)

    async def test_command_default_keeps_lock(self):
        prompt_log, _results = await self._run_command(NO_CLOTHES_ACTION)
        call = self.plugin.optimizer_calls[-1]
        self.assertEqual(call["locked_outfit"], LOCK)
        self.assertIn("she is wearing exactly: " + LOCK, prompt_log[-1]["prompt"])

    async def test_command_accessory_only_keeps_lock(self):
        prompt_log, _results = await self._run_command("戴一条围巾拍一张")
        self.assertEqual(self.plugin.optimizer_calls[-1]["locked_outfit"], LOCK)
        self.assertIn("she is wearing exactly: " + LOCK, prompt_log[-1]["prompt"])

    async def test_command_english_named_outfit_skips_lock(self):
        prompt_log, _results = await self._run_command("wearing a red hoodie")
        self.assertEqual(self.plugin.optimizer_calls[-1]["locked_outfit"], "")
        self.assertNotIn("she is wearing exactly", prompt_log[-1]["prompt"])


class PluginApiActionTests(_SelfiePathBase):
    async def test_amo_path_named_outfit_skips_lock(self):
        result, prompt_log = await self._run_plugin_api("穿宽大卫衣，在窗边")
        self.assertEqual(self.plugin.optimizer_calls[-1]["locked_outfit"], "")
        self.assertNotIn("she is wearing exactly", prompt_log[-1]["prompt"])
        self.assertTrue(result["success"])

    async def test_amo_path_default_keeps_lock(self):
        result, prompt_log = await self._run_plugin_api(NO_CLOTHES_ACTION)
        self.assertEqual(self.plugin.optimizer_calls[-1]["locked_outfit"], LOCK)
        self.assertIn("she is wearing exactly: " + LOCK, prompt_log[-1]["prompt"])
        self.assertTrue(result["success"])

    async def test_amo_path_explicit_skip_flag_wins_even_without_words(self):
        # amo 的显式按请求参数：plan 判断他点名了衣服，但 prompt 因场景冲突没带服装词。
        result, prompt_log = await self._run_plugin_api(NO_CLOTHES_ACTION, skip_outfit_lock=True)
        self.assertEqual(self.plugin.optimizer_calls[-1]["locked_outfit"], "")
        self.assertNotIn("she is wearing exactly", prompt_log[-1]["prompt"])
        self.assertTrue(result["success"])

    async def test_amo_path_default_does_not_skip_without_flag(self):
        await self._run_plugin_api(NO_CLOTHES_ACTION)
        self.assertEqual(self.plugin.optimizer_calls[-1]["locked_outfit"], LOCK)


if __name__ == "__main__":
    unittest.main()
