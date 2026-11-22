# -*- coding: utf-8 -*-
"""跨插件生图 principal 权限/配额/用量契约（task-27）。

生产事故（work/audit/cross_plugin.md P1-4）：generate_images_for_plugin 只在
event is not None 时校验权限/配额/记用量；能陪两条自动生图路径都不传 event
（photo_generate.py:294/459），于是谁能在聊天里触发能陪发图，都绕过定格的
白名单/每日配额/面板统计。

本测试钉住修复后的契约：
- event=None 但带合法 principal(owner) -> 权限/配额/用量全部生效，usage_recorded=True；
- principal 坏形状/不受信 caller -> 拒绝，且只给能说出口的文案；
- kind=system（内部参考图）-> 放行、不占个人配额、不记“她的今日生图”；
- 旧版调用（无 event 无 principal）-> 过渡期放行但 usage 记 unattributed，
  contract_miss 计数进面板（滚动升级后清零）。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import threading
import types
import unittest

import _custom_endpoint_helpers as helpers

main_module = helpers.main_module
OmniDrawPlugin = helpers.OmniDrawPlugin

PRINCIPAL = {"contract": "amo-image-principal", "version": 1,
             "caller": "Astrbot_amoupup_forever", "kind": "owner",
             "user_id": "ou_cec66", "scope": "private",
             "umo": "lark:FriendMessage:ou_cec66"}


class _Event:
    """最小事件：只用于确认 event 优先分支不被 principal 影响。"""

    unified_msg_origin = "lark:FriendMessage:ou_cec66"

    def get_sender_id(self):
        return "ou_cec66"

    def is_admin(self):
        return False


class PrincipalQuotaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omnidraw-principal-")
        self.addCleanup(self.temp.cleanup)
        self.plugin = self._plugin()

    def _plugin(self):
        plugin = object.__new__(OmniDrawPlugin)
        plugin.data_dir = str(pathlib.Path(self.temp.name))
        plugin.plugin_config = types.SimpleNamespace(
            enable_daily_limit=True,
            daily_image_limit=2,
            max_batch_count=4,
            enable_checkin=False,
            checkin_bonus_min=1,
            checkin_bonus_max=3,
            usable_users=[], blocked_users=[], unlimited_users=[],
            unlimited_groups=[], allowed_users=[],
        )
        plugin._usage_lock = threading.RLock()
        plugin._usage_stats = {}
        plugin._quota_reservations = {}
        plugin._persist_usage_stats = lambda: None
        plugin._refresh_from_native_config_if_changed = lambda: None
        plugin._notice_calls = []

        async def text2img(*args, **kwargs):
            return {"requested_count": 1, "prompts": ["p"], "results": [object()],
                    "mode": "text2img", "chain": "text2img"}

        plugin._run_text2img_generation = text2img
        plugin._run_selfie_generation = text2img
        plugin._plugin_generation_response = lambda generation: (
            {"success": True, "message": "ok", "images": [{"url": "x"}]}, [object()])
        plugin._release_cache_paths = lambda paths: None
        return plugin

    async def _generate(self, **kwargs):
        params = {"prompt": "测试生图", "count": 1}
        params.update(kwargs)
        return await plugin_generate(self.plugin, **params)

    # ---------- principal 解析 ----------
    def test_principal_normalization(self):
        status = self.plugin._normalize_plugin_principal(PRINCIPAL)
        self.assertIsNotNone(status)
        self.assertTrue(status["allowed"])
        self.assertEqual(status["level"], "plugin_owner")
        self.assertFalse(status["unlimited"])

    def test_untrusted_caller_or_kind_is_invalid(self):
        self.assertIsNone(self.plugin._normalize_plugin_principal(
            dict(PRINCIPAL, caller="astrbot_plugin_evil")))
        self.assertIsNone(self.plugin._normalize_plugin_principal(
            dict(PRINCIPAL, kind="admin")))
        self.assertIsNone(self.plugin._normalize_plugin_principal("not-a-dict"))

    def test_blocked_owner_denied(self):
        self.plugin.plugin_config.blocked_users = ["ou_cec66"]
        status = self.plugin._normalize_plugin_principal(PRINCIPAL)
        self.assertFalse(status["allowed"])

    # ---------- 全链路：principal 权限/配额/用量 ----------
    async def test_owner_principal_records_usage(self):
        result = await self._generate(principal=PRINCIPAL, record_usage=True)
        self.assertTrue(result["success"], result)
        self.assertTrue(result["usage_recorded"])
        self.assertEqual(result["contract"], "amo-image-principal")
        self.assertEqual(result["principal"]["kind"], "owner")
        self.assertEqual(result["principal"]["source"], "plugin")
        stats = self.plugin._usage_stats
        self.assertEqual(stats["users"]["ou_cec66"]["count"], 1)
        self.assertEqual(stats["users"]["ou_cec66"]["source"], "plugin")
        self.assertEqual(self.plugin._quota_reservations, {})

    async def test_owner_principal_hits_daily_quota(self):
        await self._generate(principal=PRINCIPAL, record_usage=True)
        await self._generate(principal=PRINCIPAL, record_usage=True)
        blocked = await self._generate(principal=PRINCIPAL, record_usage=True)
        self.assertFalse(blocked["success"])
        self.assertFalse(blocked["usage_recorded"])
        self.assertIn("额度", blocked["message"])
        self.assertEqual(self.plugin._usage_stats["users"]["ou_cec66"]["count"], 2)

    async def test_system_principal_is_unlimited_and_not_counted(self):
        self.plugin.plugin_config.daily_image_limit = 1
        await self._generate(principal=PRINCIPAL, record_usage=True)
        system = await self._generate(
            principal=dict(PRINCIPAL, kind="system"), record_usage=False)
        self.assertTrue(system["success"], system)
        self.assertFalse(system["usage_recorded"])
        self.assertEqual(self.plugin._usage_stats["users"]["ou_cec66"]["count"], 1)

    async def test_invalid_principal_denied_with_safe_text(self):
        result = await self._generate(
            principal={"caller": "astrbot_plugin_evil", "kind": "owner"},
            record_usage=True)
        self.assertFalse(result["success"])
        self.assertFalse(result["usage_recorded"])
        self.assertIn("老公", result["message"])
        for word in ("权限", "principal", "event", "配额"):
            self.assertNotIn(word, result["message"])

    async def test_legacy_call_without_principal_is_flagged(self):
        result = await self._generate(record_usage=True)
        self.assertTrue(result["success"], result)
        self.assertTrue(result["usage_recorded"])
        self.assertEqual(result["principal"]["source"], "legacy")
        stats = self.plugin._usage_stats
        self.assertEqual(stats["users"]["legacy:unattributed"]["count"], 1)
        self.assertEqual(stats["contract_miss"], 1)

    async def test_usage_page_exposes_sources_and_contract_counts(self):
        await self._generate(principal=PRINCIPAL, record_usage=True)
        await self._generate(record_usage=True)
        page = self.plugin._usage_stats_for_page()
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["by_source"].get("plugin"), 1)
        self.assertEqual(page["by_source"].get("legacy"), 1)
        self.assertEqual(page["contract_miss"], 1)
        self.assertEqual(page["principal_denied"], 0)

    async def test_no_principal_and_record_usage_false_does_not_count(self):
        result = await self._generate(record_usage=False)
        self.assertTrue(result["success"], result)
        self.assertFalse(result["usage_recorded"])
        self.assertEqual(self.plugin._usage_stats.get("total", 0), 0)


async def plugin_generate(plugin, **kwargs):
    return await plugin.generate_images_for_plugin(**kwargs)


if __name__ == "__main__":
    unittest.main()
