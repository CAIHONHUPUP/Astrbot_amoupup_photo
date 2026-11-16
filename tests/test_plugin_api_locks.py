# -*- coding: utf-8 -*-
"""插件 Web API 必须把场景/姿势锁透传给生图方法（2026-11-16）。"""
import copy
import importlib
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import test_custom_endpoint as fixtures

main = fixtures.main_module
main_config = importlib.import_module((main.__package__ + "." if main.__package__ else "") + "main_config")


class _Context:
    def __init__(self):
        self.routes = {}
        self.updates = []

    def register_web_api(self, path, handler, methods, description):
        self.routes[path] = (handler, methods)

    def update_config(self, config):
        self.updates.append(copy.deepcopy(config))


class _Request:
    content_length = None

    def __init__(self, payload):
        self.payload = payload

    async def get_json(self, **kwargs):
        return copy.deepcopy(self.payload)


class PluginApiLockTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omnidraw-api-lock-")
        self.addCleanup(self.temp.cleanup)
        self.data_dir = pathlib.Path(self.temp.name) / "plugin_data"
        with patch.object(fixtures.OmniDrawPlugin, "_resolve_data_dir", return_value=str(self.data_dir)):
            self.plugin = fixtures.OmniDrawPlugin(_Context(), {})
        self.calls = []

        async def fake_generate(**kwargs):
            self.calls.append(kwargs)
            return {"success": True, "images": [], "message": "ok"}

        self.plugin.generate_images_for_plugin = fake_generate

    async def asyncTearDown(self):
        await self.plugin.terminate()

    async def _call_handler(self, payload):
        request_patch = patch.object(main_config, "request", _Request(payload))
        jsonify_patch = patch.object(main_config, "jsonify", lambda data: data)
        with request_patch, jsonify_patch:
            return await self.plugin.generate_image_for_plugin_handler()

    async def test_scene_and_pose_locks_reach_generation(self):
        response = await self._call_handler({
            "prompt": "standing by the window",
            "scene_lock": "in a subway car with overhead straps",
            "pose_lock": "holding an overhead strap with the right hand",
        })
        self.assertEqual(response[1], 200)
        call = self.calls[-1]
        self.assertEqual(call["scene_lock"], "in a subway car with overhead straps")
        self.assertEqual(call["pose_lock"], "holding an overhead strap with the right hand")

    async def test_missing_locks_default_to_empty_strings(self):
        response = await self._call_handler({"prompt": "standing by the window"})
        self.assertEqual(response[1], 200)
        call = self.calls[-1]
        self.assertEqual(call["scene_lock"], "")
        self.assertEqual(call["pose_lock"], "")
