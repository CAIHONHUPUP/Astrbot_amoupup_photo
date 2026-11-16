# -*- coding: utf-8 -*-
"""负面词配置：旧默认自动升级、配置页保存不被丢弃（2026-11-16）。"""
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import test_custom_endpoint as fixtures

OmniDrawPlugin = fixtures.OmniDrawPlugin
OLD_BASE_NEGATIVE = "人物, 人类, 文字, 水印, 签名, 二次元, 插画, 畸形, 多余的肢体, 模糊, 低画质"
NEW_BASE_NEGATIVE = "人物, 人类, 文字, 水印, 签名, 畸形, 多余的肢体, 模糊, 低画质"


class _Context:
    def __init__(self):
        self.routes = {}
        self.updates = []

    def register_web_api(self, path, handler, methods, description):
        self.routes[path] = (handler, methods)

    def update_config(self, config):
        self.updates.append(config)


class NegativeConfigUpgradeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omnidraw-neg-config-")
        self.addCleanup(self.temp.cleanup)
        self.data_dir = pathlib.Path(self.temp.name) / "plugin_data"

    def _plugin(self, native):
        with patch.object(OmniDrawPlugin, "_resolve_data_dir", return_value=str(self.data_dir)):
            return OmniDrawPlugin(_Context(), native)

    def _persisted(self, plugin):
        return json.loads(pathlib.Path(plugin.config_path).read_text(encoding="utf-8"))

    async def test_legacy_default_is_upgraded_and_persisted(self):
        plugin = self._plugin({"image_generation": {"base_negative": OLD_BASE_NEGATIVE}})
        try:
            self.assertEqual(plugin.plugin_config.base_negative, NEW_BASE_NEGATIVE)
            self.assertEqual(
                self._persisted(plugin)["image_generation"]["base_negative"],
                NEW_BASE_NEGATIVE,
            )
        finally:
            await plugin.terminate()

    async def test_custom_negative_is_preserved(self):
        plugin = self._plugin({
            "image_generation": {"base_negative": "水印", "negative_prompt_enabled": False},
        })
        try:
            self.assertEqual(plugin.plugin_config.base_negative, "水印")
            self.assertFalse(plugin.plugin_config.negative_prompt_enabled)
        finally:
            await plugin.terminate()

    async def test_page_save_keeps_image_generation(self):
        plugin = self._plugin({})
        try:
            plugin._save_page_config({
                "image_generation": {"base_negative": "水印, 模糊", "item_negative": "穿模"},
            })
            self.assertEqual(plugin.plugin_config.base_negative, "水印, 模糊")
            self.assertEqual(plugin.plugin_config.item_negative, "穿模")
            self.assertEqual(
                self._persisted(plugin)["image_generation"]["base_negative"],
                "水印, 模糊",
            )
        finally:
            await plugin.terminate()

    async def test_upgrade_survives_restart(self):
        plugin = self._plugin({"image_generation": {"base_negative": OLD_BASE_NEGATIVE}})
        try:
            await plugin.terminate()
        finally:
            pass
        restarted = self._plugin({})
        try:
            self.assertEqual(restarted.plugin_config.base_negative, NEW_BASE_NEGATIVE)
        finally:
            await restarted.terminate()
