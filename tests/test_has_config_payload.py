# -*- coding: utf-8 -*-
"""main_config._has_config_payload：拆函数前的完整行为矩阵。"""
import unittest

import test_custom_endpoint as fixtures

main_config = fixtures.main_config
DEFAULT_INTERVAL = main_config.DEFAULT_CACHE_CLEANUP_INTERVAL_HOURS
DEFAULT_SIZE = main_config.DEFAULT_MAX_CACHE_SIZE_MB


class HasConfigPayloadTests(unittest.TestCase):
    def setUp(self):
        self.plugin = object.__new__(fixtures.OmniDrawPlugin)

    def test_false_cases(self):
        cases = {
            "none": None,
            "list": [],
            "empty": {},
            "unknown": {"unknown": 1},
            "blank_permission": {"permission_config": {"usable_users": "  "}},
            "default_usage": {"usage_config": {"daily_image_limit": 20,
                                                "checkin_bonus_min": 1,
                                                "checkin_bonus_max": 3}},
            "default_cache": {"cache_config": {
                "scheduled_cleanup_interval_hours": DEFAULT_INTERVAL,
                "max_cache_size_mb": DEFAULT_SIZE}},
            "default_reply": {"reply_config": {
                "draw_pending_message": main_config.DEFAULT_DRAW_PENDING_MESSAGE,
                "selfie_pending_message": main_config.DEFAULT_SELFIE_PENDING_MESSAGE,
                "draw_error_message": main_config.DEFAULT_DRAW_ERROR_MESSAGE,
                "selfie_error_message": main_config.DEFAULT_SELFIE_ERROR_MESSAGE}},
            "default_router": {"router_config": {"chain_text2img": "node_1",
                                                  "chain_selfie": "node_1",
                                                  "chain_video": "video_node_1"}},
            "default_optimizer": {"optimizer_config": {
                "enable_optimizer": True, "optimizer_style": "手机日常原生感",
                "chain_optimizer": "node_1", "optimizer_model": "gpt-4o-mini",
                "optimizer_timeout": 15, "max_batch_count": 0,
                "optimizer_custom_prompt": ""}},
            "default_persona": {"persona_config": {
                "active_persona_id": "default",
                "profiles": [{"id": "default", "persona_name": "默认助理"}]}},
        }
        for name, config in cases.items():
            with self.subTest(name=name):
                self.assertFalse(self.plugin._has_config_payload(config))

    def test_true_cases(self):
        cases = {
            "providers": {"providers": [{"id": "p"}]},
            "video_providers": {"video_providers": [{"id": "v"}]},
            "presets": {"presets": ["x:y"]},
            "verbose": {"verbose_report": True},
            "permission_usable": {"permission_config": {"usable_users": "u"}},
            "permission_access": {"permission_config": {"access_users": "u"}},
            "permission_whitelist": {"permission_config": {"use_whitelist": "u"}},
            "permission_allowed": {"permission_config": {"allowed_users": "u"}},
            "permission_unlimited": {"permission_config": {"unlimited_users": "u"}},
            "permission_user_whitelist": {"permission_config": {"user_whitelist": "u"}},
            "permission_blocked": {"permission_config": {"blocked_users": "u"}},
            "permission_blacklist": {"permission_config": {"user_blacklist": "u"}},
            "permission_groups": {"permission_config": {"unlimited_groups": "g"}},
            "permission_group_whitelist": {"permission_config": {"group_whitelist": "g"}},
            "usage_daily": {"usage_config": {"enable_daily_limit": True}},
            "usage_limit": {"usage_config": {"daily_image_limit": 5}},
            "usage_checkin": {"usage_config": {"enable_checkin": True}},
            "usage_min": {"usage_config": {"checkin_bonus_min": 2}},
            "usage_max": {"usage_config": {"checkin_bonus_max": 4}},
            "cache_scheduled": {"cache_config": {"enable_scheduled_cleanup": True}},
            "cache_interval": {"cache_config": {"scheduled_cleanup_interval_hours": 12}},
            "cache_size_cleanup": {"cache_config": {"enable_size_limit_cleanup": True}},
            "cache_size": {"cache_config": {"max_cache_size_mb": 500}},
            "reply_draw_pending": {"reply_config": {"draw_pending_message": "画着"}},
            "router_text": {"router_config": {"chain_text2img": "node_9"}},
            "router_selfie": {"router_config": {"chain_selfie": "node_9"}},
            "router_video": {"router_config": {"chain_video": "video_node_9"}},
            "optimizer_disable": {"optimizer_config": {"enable_optimizer": False}},
            "optimizer_style": {"optimizer_config": {"optimizer_style": "胶片"}},
            "optimizer_chain": {"optimizer_config": {"chain_optimizer": "node_9"}},
            "optimizer_model": {"optimizer_config": {"optimizer_model": "m"}},
            "optimizer_timeout": {"optimizer_config": {"optimizer_timeout": 30}},
            "optimizer_batch": {"optimizer_config": {"max_batch_count": 4}},
            "optimizer_custom": {"optimizer_config": {"optimizer_custom_prompt": "cp"}},
            "persona_active": {"persona_config": {"active_persona_id": "p2"}},
            "persona_profile_name": {"persona_config": {"profiles": [
                {"id": "default", "persona_name": "阿鱼"}]}},
            "persona_profile_base": {"persona_config": {"profiles": [
                {"id": "default", "persona_base_prompt": "base"}]}},
            "persona_profile_ref": {"persona_config": {"profiles": [
                {"id": "default", "persona_ref_image": "front.png"}]}},
            "persona_second_id": {"persona_config": {"profiles": [
                {"id": "default", "persona_name": "默认助理"},
                {"id": "persona_9", "persona_name": "人设 2"}]}},
            "persona_top_name": {"persona_config": {"persona_name": "阿鱼"}},
            "persona_top_base": {"persona_config": {"persona_base_prompt": "base"}},
            "persona_top_ref": {"persona_config": {"persona_ref_image": "front.png"}},
            "persona_top_refs": {"persona_config": {"persona_ref_images": ["front.png"]}},
        }
        for name, config in cases.items():
            with self.subTest(name=name):
                self.assertTrue(self.plugin._has_config_payload(config))


if __name__ == "__main__":
    unittest.main()
