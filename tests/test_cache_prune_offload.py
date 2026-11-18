# -*- coding: utf-8 -*-
"""机会性缓存清理不能阻塞事件循环（2026-11-16）。"""
import base64
import pathlib
import tempfile
import threading
import unittest

import test_custom_endpoint as fixtures

OmniDrawPlugin = fixtures.OmniDrawPlugin
PNG_DATA = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"pixels" * 20).decode("ascii")


def _bare_plugin(data_dir):
    plugin = object.__new__(OmniDrawPlugin)
    plugin.data_dir = str(data_dir)
    plugin._background_tasks = set()
    plugin._background_task_kinds = {}
    plugin._background_semaphore = None
    plugin._background_semaphore_loop = None
    plugin._cache_lease_lock = threading.RLock()
    plugin._cache_leases = {}
    return plugin


class CachePruneOffloadTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omnidraw-cache-prune-")
        self.addCleanup(self.temp.cleanup)
        self.data_dir = pathlib.Path(self.temp.name)

    async def test_schedule_prune_runs_off_the_event_loop(self):
        plugin = _bare_plugin(self.data_dir)
        seen = {}

        def prune(trigger="auto", protected_paths=None):
            seen["thread"] = threading.current_thread().name
            seen["trigger"] = trigger
            seen["protected"] = list(protected_paths or [])
            return {}

        plugin._prune_cache_if_needed = prune
        plugin._schedule_cache_prune("temp_images", ["/tmp/keep.png"])

        tasks = list(plugin._background_tasks)
        self.assertEqual(len(tasks), 1)
        await tasks[0]

        self.assertNotEqual(seen["thread"], threading.current_thread().name)
        self.assertEqual(seen["trigger"], "temp_images")
        self.assertIn("/tmp/keep.png", seen["protected"])
        self.assertEqual(plugin._cache_leases, {})

    async def test_image_component_schedules_prune_instead_of_blocking(self):
        plugin = _bare_plugin(self.data_dir)
        plugin._prune_cache_if_needed = lambda *args, **kwargs: self.fail("必须走后台清理，不能同步扫目录")
        scheduled = []
        plugin._schedule_cache_prune = lambda trigger="auto", protected_paths=None: scheduled.append(
            (trigger, list(protected_paths or []))
        )

        component = plugin._create_image_component(PNG_DATA)

        self.assertIsNotNone(component)
        self.assertEqual([item[0] for item in scheduled], ["temp_images"])
        self.assertTrue(scheduled[0][1][0].endswith(".png"))
        self.assertTrue(pathlib.Path(scheduled[0][1][0]).exists())

    async def test_serialize_result_schedules_prune_instead_of_blocking(self):
        plugin = _bare_plugin(self.data_dir)
        plugin._prune_cache_if_needed = lambda *args, **kwargs: self.fail("序列化时不能同步扫缓存目录")
        scheduled = []
        plugin._schedule_cache_prune = lambda trigger="auto", protected_paths=None: scheduled.append(
            (trigger, list(protected_paths or []))
        )

        payload = plugin._serialize_plugin_image_result(PNG_DATA, 1, "prompt")

        self.assertTrue(payload["file_path"].endswith(".png"))
        self.assertTrue(pathlib.Path(payload["file_path"]).exists())
        self.assertEqual([item[0] for item in scheduled], ["temp_images"])
        self.assertEqual(scheduled[0][1], [payload["file_path"]])

    def test_schedule_prune_without_running_loop_is_noop(self):
        plugin = _bare_plugin(self.data_dir)
        plugin._prune_cache_if_needed = lambda *args, **kwargs: self.fail("没有事件循环时不应执行清理")
        plugin._background_tasks = None
        plugin._schedule_cache_prune("temp_images", ["/tmp/keep.png"])
