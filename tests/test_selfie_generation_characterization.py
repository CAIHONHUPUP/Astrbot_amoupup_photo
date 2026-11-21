# -*- coding: utf-8 -*-
"""main._run_selfie_generation 行为快照（拆函数前，覆盖返回包/锁/负向/异常）。"""
import types
import unittest

import test_custom_endpoint as fixtures
import test_pose_ref_order as harness

main_module = fixtures.main_module
ChainRunResult = fixtures.ChainRunResult


class SelfieGenerationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _plugin(self):
        return harness.PoseRefOrderTests._plugin(self)

    async def _run(self, plugin, refs, pose_ref=None, action="挥手", count=1, **extra):
        async def run_chain(chain_name, prompt, **kwargs):
            plugin.chain_calls.append(kwargs)
            return ChainRunResult(image_url="https://cdn.example.com/o.png",
                                  provider_id="node", model="m", elapsed_seconds=1.0)

        class FakeChainManager:
            def __init__(self, config, session):
                pass

            run_chain_with_metadata = staticmethod(run_chain)

        orig_session = main_module.aiohttp.ClientSession
        orig_chain = main_module.ChainManager
        main_module.aiohttp.ClientSession = harness._FakeClientSession
        main_module.ChainManager = FakeChainManager
        try:
            return await plugin._run_selfie_generation(
                None, action, count, refs=refs, pose_ref=pose_ref, **extra)
        finally:
            main_module.aiohttp.ClientSession = orig_session
            main_module.ChainManager = orig_chain

    async def test_result_bundle_locked_outfit_and_persona_dedup(self):
        plugin = self._plugin()
        plugin._resolve_locked_outfit = lambda action, skip=False: "米色风衣"
        out = await self._run(plugin, ["room.png"], count=2)
        self.assertEqual(set(out), {"prompts", "results", "requested_count", "raw_refs",
                                    "safe_refs", "mode", "chain", "leased_result_paths"})
        self.assertEqual(out["requested_count"], 2)
        self.assertEqual(len(out["prompts"]), 2)
        self.assertEqual(len(out["results"]), 2)
        for prompt in out["prompts"]:
            self.assertIn("she is wearing exactly: 米色风衣", prompt)
            self.assertIn("today's outfit is fixed", prompt)
        call = plugin.chain_calls[-1]
        self.assertEqual(call["user_refs"],
                         ["front.png", "side.png", "full.png", "room.png"])
        self.assertNotIn("persona_ref", call)
        self.assertEqual(out["mode"], "selfie")
        self.assertEqual(out["chain"], "selfie")

    async def test_negative_prompt_explicit_and_default(self):
        plugin = self._plugin()
        plugin._negative_prompt_for = lambda prompt, kind="": "DEFAULT-NEG"
        await self._run(plugin, ["room.png"])
        self.assertEqual(plugin.chain_calls[-1]["negative_prompt"], "DEFAULT-NEG")
        seen = []
        plugin._negative_prompt_for = lambda prompt, kind="": seen.append(kind) or "DEFAULT-NEG"
        await self._run(plugin, ["room.png"], negative_prompt=" 女仆头饰 ")
        self.assertEqual(plugin.chain_calls[-1]["negative_prompt"], "女仆头饰")
        self.assertEqual(seen, [])

    async def test_no_event_refs_uses_persona_refs_only(self):
        plugin = self._plugin()
        out = await self._run(plugin, None)
        call = plugin.chain_calls[-1]
        self.assertEqual(call["user_refs"], ["front.png", "side.png", "full.png"])
        self.assertNotIn("persona_ref", call)
        self.assertEqual(out["raw_refs"], [])
        self.assertEqual(out["safe_refs"], ["front.png", "side.png", "full.png"])

    async def test_chain_exception_is_returned_not_raised(self):
        plugin = self._plugin()

        async def fail_chain(chain_name, prompt, **kwargs):
            raise ValueError("provider failed")

        class FailingChainManager:
            def __init__(self, config, session):
                pass

            run_chain_with_metadata = staticmethod(fail_chain)

        orig_session = main_module.aiohttp.ClientSession
        orig_chain = main_module.ChainManager
        main_module.aiohttp.ClientSession = harness._FakeClientSession
        main_module.ChainManager = FailingChainManager
        try:
            out = await plugin._run_selfie_generation(None, "挥手", 1, refs=["room.png"])
        finally:
            main_module.aiohttp.ClientSession = orig_session
            main_module.ChainManager = orig_chain
        self.assertIsInstance(out["results"][0], ValueError)

    async def test_failure_after_lease_releases_lease_and_reraises(self):
        plugin = self._plugin()
        released = []
        plugin._lease_cache_paths = lambda paths: ["lease"]
        plugin._release_cache_paths = lambda paths: released.append(list(paths or []))

        def boom(action):
            raise RuntimeError("build failed")

        plugin.persona_manager = types.SimpleNamespace(build_persona_prompt=boom)
        with self.assertRaises(RuntimeError):
            await self._run(plugin, ["room.png"])
        self.assertEqual(released, [["lease"], []])


if __name__ == "__main__":
    unittest.main()
