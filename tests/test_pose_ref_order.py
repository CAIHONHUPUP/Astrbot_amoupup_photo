# -*- coding: utf-8 -*-
"""REF2：生图插件自拍参考图顺序——姿势图必须排在三位人设图前面。"""
import tempfile
import types
import unittest

import test_custom_endpoint as fixtures

main_module = fixtures.main_module
OmniDrawPlugin = fixtures.OmniDrawPlugin
ChainRunResult = fixtures.ChainRunResult

PERSONAS = ["front.png", "side.png", "full.png"]


class _FakeClientSession:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class PoseRefOrderTests(unittest.IsolatedAsyncioTestCase):
    def _plugin(self, personas=None):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.data_dir = tempfile.mkdtemp(prefix="omnidraw-pose-ref-")
        refs = list(PERSONAS if personas is None else personas)
        plugin.plugin_config = types.SimpleNamespace(
            persona_ref_images=refs,
            chains={"selfie": ["selfie-node"]},
            max_batch_count=4,
        )
        plugin.chain_calls = []
        plugin.saved_refs = []

        async def optimize(prompt, count=1, session=None, **kwargs):
            return [prompt] * count

        def build_prompt(action):
            kwargs = {"persona_ref": refs[0]} if refs else {}
            return action, kwargs

        plugin.prompt_optimizer = types.SimpleNamespace(optimize=optimize)
        plugin.persona_manager = types.SimpleNamespace(build_persona_prompt=build_prompt)
        plugin._image_request_options = lambda *args, **kwargs: {}
        plugin._selfie_camera_flags = lambda action: (False, True)
        plugin._resolve_locked_outfit = lambda action, skip=False: ""
        plugin._negative_prompt_for = lambda prompt, kind="": ""

        async def process_refs(raw, session=None, allow_local_refs=False):
            plugin.saved_refs.append(list(raw))
            return [str(ref) for ref in raw]

        plugin._process_and_save_images = process_refs
        plugin._lease_cache_paths = lambda paths: list(paths or [])
        plugin._release_cache_paths = lambda paths: None
        return plugin

    async def _run(self, plugin, refs, pose_ref=None):
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
        main_module.aiohttp.ClientSession = _FakeClientSession
        main_module.ChainManager = FakeChainManager
        try:
            return await plugin._run_selfie_generation(
                None, "挥手", 1, refs=refs, pose_ref=pose_ref)
        finally:
            main_module.aiohttp.ClientSession = orig_session
            main_module.ChainManager = orig_chain

    async def test_pose_first_then_persona_then_room_outfit(self):
        plugin = self._plugin()
        await self._run(plugin, ["room.png", "coat.png"], pose_ref="pose.png")
        self.assertEqual(
            plugin.chain_calls[-1]["user_refs"],
            ["pose.png", "front.png", "side.png", "full.png", "room.png", "coat.png"],
        )

    async def test_pose_is_not_duplicated_when_also_in_refs(self):
        plugin = self._plugin()
        await self._run(plugin, ["pose.png", "room.png"], pose_ref="pose.png")
        self.assertEqual(
            plugin.chain_calls[-1]["user_refs"],
            ["pose.png", "front.png", "side.png", "full.png", "room.png"],
        )

    async def test_without_pose_persona_stays_first(self):
        plugin = self._plugin()
        await self._run(plugin, ["room.png", "coat.png"], pose_ref=None)
        self.assertEqual(
            plugin.chain_calls[-1]["user_refs"],
            ["front.png", "side.png", "full.png", "room.png", "coat.png"],
        )

    async def test_without_persona_refs_the_caller_order_is_kept(self):
        plugin = self._plugin(personas=[])
        await self._run(plugin, ["room.png", "coat.png"], pose_ref="pose.png")
        self.assertEqual(plugin.chain_calls[-1]["user_refs"],
                         ["pose.png", "room.png", "coat.png"])

    async def test_pose_ref_survives_max_reference_cap(self):
        plugin = self._plugin()
        extras = [f"ref-{index}.png" for index in range(12)]
        await self._run(plugin, extras, pose_ref="pose.png")
        user_refs = plugin.chain_calls[-1]["user_refs"]
        self.assertEqual(len(user_refs), 14)
        self.assertEqual(user_refs[0], "pose.png")
        self.assertEqual(user_refs[1:4], PERSONAS)

    async def test_generate_images_for_plugin_forwards_pose_ref(self):
        plugin = self._plugin()
        captured = {}

        async def fake_selfie(event, action, count, **kwargs):
            captured.update(kwargs)
            return {"prompts": [action], "results": [], "requested_count": count,
                    "raw_refs": [], "safe_refs": [], "mode": "selfie", "chain": "selfie"}

        plugin._run_selfie_generation = fake_selfie
        plugin._refresh_from_native_config_if_changed = lambda: None
        plugin._validate_local_image_path = lambda path, allowed_root="": path
        plugin._plugin_generation_response = lambda generation: (
            {"success": True, "images": []}, [])

        await plugin.generate_images_for_plugin(
            prompt="挥手", refs=["room.png"], mode="selfie",
            allow_local_refs=True, pose_ref="pose.png")
        self.assertEqual(captured.get("pose_ref"), "pose.png")
        self.assertEqual(captured.get("refs"), ["room.png"])


if __name__ == "__main__":
    unittest.main()
