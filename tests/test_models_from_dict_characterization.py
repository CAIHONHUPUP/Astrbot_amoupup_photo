# -*- coding: utf-8 -*-
"""models.PluginConfig.from_dict 行为快照（拆函数前生成，拆完必须逐字节一致）。"""
import copy
import dataclasses
import json
import os
import unittest

import test_custom_endpoint as fixtures

GOLD = json.load(open(os.path.join(os.path.dirname(__file__), "data",
                                   "plugin_config_from_dict_golden.json"), encoding="utf-8"))
DATA_DIR = "D:/tmp/photo-polish-golden-data"

CFG_CASES = {
    "empty": {},
    "presets_mixed": {"presets": ["胶片:35mm", "名字：提示词", "bad", {"name": "x", "prompt": "y"}]},
    "permissions": {"permission_config": {
        "usable_users": "u1\nu2", "access_users": "u2,u3", "use_whitelist": "u4",
        "allowed_users": "a1", "unlimited_users": "a2", "user_whitelist": "a3",
        "blocked_users": "b1", "user_blacklist": "b2",
        "unlimited_groups": "g1", "group_whitelist": "g2"}},
    "usage": {"usage_config": {"enable_daily_limit": True, "daily_image_limit": 5,
                               "enable_checkin": True, "checkin_bonus_min": 5,
                               "checkin_bonus_max": 2}},
    "cache": {"cache_config": {"enable_scheduled_cleanup": True,
                               "scheduled_cleanup_interval_hours": 12,
                               "enable_size_limit_cleanup": True,
                               "max_cache_size_mb": 500}},
    "reply": {"reply_config": {"draw_pending_message": "画着", "selfie_pending_message": "拍着",
                               "draw_error_message": "错了", "selfie_error_message": "拍错"}},
    "negative": {"image_generation": {"negative_prompt_enabled": False,
                                      "base_negative": "", "room_negative": "r",
                                      "item_negative": "i"}},
    "router_optimizer": {"router_config": {"chain_text2img": "node_9", "chain_selfie": "node_8",
                                           "chain_video": "video_node_2"},
                         "optimizer_config": {"enable_optimizer": False, "optimizer_model": "m",
                                              "optimizer_style": "x", "max_batch_count": 99,
                                              "optimizer_timeout": 30,
                                              "optimizer_custom_prompt": "cp"}},
    "persona_legacy": {"persona_config": {"persona_name": "阿鱼", "persona_base_prompt": "base"}},
    "providers": {"providers": [{"id": "p1", "api_type": "openai_image",
                                 "base_url": "https://x.example/v1", "api_keys": ["k1"],
                                 "model": "m1"}],
                  "video_providers": [{"id": "v1", "api_type": "async_task",
                                       "base_url": "https://v.example/v1", "api_keys": ["k2"],
                                       "model": "vm1"}]},
}


class FromDictCharacterizationTests(unittest.TestCase):
    def test_all_golden_configs_match_byte_for_byte(self):
        for name, raw in CFG_CASES.items():
            with self.subTest(name=name):
                config = fixtures.PluginConfig.from_dict(copy.deepcopy(raw), DATA_DIR)
                self.assertEqual(dataclasses.asdict(config), GOLD[name])


if __name__ == "__main__":
    unittest.main()
