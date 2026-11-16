# -*- coding: utf-8 -*-
"""命令 /自拍 与 amo 自拍链路必须有同一套 optimize 参数（2026-11-16）。"""
import asyncio
import json
import pathlib
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import test_custom_endpoint as fixtures

main_module = fixtures.main_module
OmniDrawPlugin = fixtures.OmniDrawPlugin
ChainRunResult = fixtures.ChainRunResult


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


class CommandSelfieParityTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omnidraw-selfie-parity-")
        self.addCleanup(self.temp.cleanup)
        self.data_dir = pathlib.Path(self.temp.name)

    def _plugin(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.data_dir = str(self.data_dir)
        plugin.plugin_config = types.SimpleNamespace(
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
            build_persona_prompt=lambda action: (f"persona base, {action}", {"persona_ref": "persona-default.png"})
        )
        plugin._permission_denied_message = lambda event: ""
        plugin._reserve_image_quota = lambda event, count: ("", 0)
        plugin._complete_image_quota = lambda event, reserved, count: None
        plugin._release_image_quota = lambda event, reserved: None
        plugin._extract_command_message = lambda event, command, fallback: fallback
        plugin._get_event_images = lambda *args, **kwargs: []

        async def process_refs(refs, session=None):
            return ["/stub/ref.png"]

        plugin._process_and_save_images = process_refs
        plugin._lease_cache_paths = lambda paths: list(paths or [])
        plugin._release_cache_paths = lambda paths: None
        plugin._build_image_success_components = lambda result, elapsed: [{"result": result.image_url}]

        async def send_plain(event, message):
            return None

        plugin._send_plain_message = send_plain
        return plugin

    async def _run_selfie(self, plugin, *args):
        class FakeChainManager:
            def __init__(self, config, session):
                pass

            async def run_chain_with_metadata(self, chain_name, prompt, **kwargs):
                plugin.chain_calls.append({"chain": chain_name, "prompt": prompt, **kwargs})
                return ChainRunResult(
                    image_url="https://cdn.example.com/selfie.png",
                    provider_id="selfie_node",
                    model="model_a",
                    elapsed_seconds=1.0,
                )

        original_session = main_module.aiohttp.ClientSession
        original_chain = main_module.ChainManager
        main_module.aiohttp.ClientSession = _FakeClientSession
        main_module.ChainManager = FakeChainManager
        try:
            return [item async for item in plugin.cmd_selfie(_FakeEvent(), *args)]
        finally:
            main_module.aiohttp.ClientSession = original_session
            main_module.ChainManager = original_chain

    async def test_command_selfie_passes_selfie_flags_and_locks_outfit(self):
        locked_outfit = "米色雪纺衬衫，黑色包臀裙"
        (self.data_dir / "amo_outfit.json").write_text(
            json.dumps({"day": time.strftime("%Y-%m-%d"), "text": locked_outfit}, ensure_ascii=False),
            encoding="utf-8",
        )
        plugin = self._plugin()

        results = await self._run_selfie(plugin, "挥手")

        call = plugin.optimizer_calls[-1]
        self.assertTrue(call["has_reference"])
        self.assertTrue(call["selfie"])
        self.assertFalse(call["external_camera"])
        self.assertEqual(call["locked_outfit"], locked_outfit)
        self.assertEqual(call["count"], 1)

        prompt = plugin.chain_calls[-1]["prompt"]
        self.assertIn("optimized 挥手", prompt)
        self.assertIn("she is wearing exactly: " + locked_outfit, prompt)
        self.assertEqual(results, [("chain", [{"result": "https://cdn.example.com/selfie.png"}])])

    async def test_command_selfie_with_external_camera_opens_full_body(self):
        plugin = self._plugin()

        await self._run_selfie(plugin, "帮我拍一张全身照")

        call = plugin.optimizer_calls[-1]
        self.assertTrue(call["external_camera"])
        self.assertFalse(call["selfie"])
        self.assertEqual(call["locked_outfit"], "")
        self.assertNotIn("she is wearing exactly", plugin.chain_calls[-1]["prompt"])
