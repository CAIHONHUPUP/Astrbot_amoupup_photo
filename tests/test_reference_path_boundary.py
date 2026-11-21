# -*- coding: utf-8 -*-
"""参考图路径边界：插件 API 的本地图必须落在 data_dir 内（2026-11-16 审计用）。"""
import pathlib
import tempfile
import types
import unittest

import test_custom_endpoint as fixtures

OmniDrawPlugin = fixtures.OmniDrawPlugin
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"reference" * 16


class ReferencePathBoundaryTest(unittest.TestCase):
    def test_plugin_api_refs_outside_data_dir_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="omnidraw-ref-boundary-") as tmp:
            root = pathlib.Path(tmp)
            data_dir = root / "plugin_data"
            data_dir.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(PNG_BYTES)
            plugin = object.__new__(OmniDrawPlugin)
            plugin.data_dir = str(data_dir)

            with self.assertRaisesRegex(ValueError, "不在允许目录内"):
                plugin._validated_plugin_image_refs([str(outside)])

            # allow_local_refs=True 是 amo 这种受信任插件的跨目录传图通道，保持可用
            self.assertEqual(
                plugin._validated_plugin_image_refs([str(outside)], allow_local_refs=True),
                [str(outside)],
            )

    def test_plugin_api_refs_inside_data_dir_are_accepted(self):
        with tempfile.TemporaryDirectory(prefix="omnidraw-ref-inside-") as tmp:
            data_dir = pathlib.Path(tmp) / "plugin_data"
            data_dir.mkdir()
            inside = data_dir / "inside.png"
            inside.write_bytes(PNG_BYTES)
            plugin = object.__new__(OmniDrawPlugin)
            plugin.data_dir = str(data_dir)

            self.assertEqual(plugin._validated_plugin_image_refs([str(inside)]), [str(inside)])


class EventImageLocalRootTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _plugin(data_dir):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.data_dir = str(data_dir)
        plugin.plugin_config = types.SimpleNamespace(enable_size_limit_cleanup=False)
        return plugin

    async def test_event_local_ref_outside_data_dir_is_dropped(self):
        with tempfile.TemporaryDirectory(prefix="omnidraw-ref-event-") as tmp:
            root = pathlib.Path(tmp)
            data_dir = root / "plugin_data"
            data_dir.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(PNG_BYTES)

            self.assertEqual(await self._plugin(data_dir)._process_and_save_images([str(outside)]), [])

    async def test_event_local_ref_inside_data_dir_is_kept(self):
        with tempfile.TemporaryDirectory(prefix="omnidraw-ref-event-in-") as tmp:
            data_dir = pathlib.Path(tmp) / "plugin_data"
            data_dir.mkdir()
            inside = data_dir / "inside.png"
            inside.write_bytes(PNG_BYTES)

            self.assertEqual(
                await self._plugin(data_dir)._process_and_save_images([str(inside)]),
                [str(inside)],
            )

    async def test_trusted_plugin_api_still_accepts_outside_ref(self):
        with tempfile.TemporaryDirectory(prefix="omnidraw-ref-trusted-") as tmp:
            root = pathlib.Path(tmp)
            data_dir = root / "plugin_data"
            data_dir.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(PNG_BYTES)

            self.assertEqual(
                await self._plugin(data_dir)._process_and_save_images([str(outside)], allow_local_refs=True),
                [str(outside)],
            )
