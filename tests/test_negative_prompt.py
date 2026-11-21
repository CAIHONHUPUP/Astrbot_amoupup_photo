# -*- coding: utf-8 -*-
"""负面提示词：组合、场景、自拍过滤、降级、配置读取。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 本文件可以单独跑：models/utils 会导入 astrbot，不能依赖别的测试文件先 stub。
if "astrbot" not in sys.modules:
    import logging as _logging
    import types as _types

    _astrbot = _types.ModuleType("astrbot")
    _astrbot_api = _types.ModuleType("astrbot.api")
    setattr(_astrbot_api, "logger", _logging.getLogger("test"))
    _astrbot_event = _types.ModuleType("astrbot.api.event")
    setattr(_astrbot_event, "AstrMessageEvent", object)
    setattr(_astrbot, "api", _astrbot_api)
    sys.modules.update({
        "astrbot": _astrbot,
        "astrbot.api": _astrbot_api,
        "astrbot.api.event": _astrbot_event,
    })

from core.negative_prompt import (  # noqa: E402
    DEFAULT_BASE,
    EXTRA_BASE,
    DEFAULT_ITEM,
    DEFAULT_ROOM,
    get_negative_prompt,
    merge_negative,
    scene_type_of,
)


class ComposeTests(unittest.TestCase):
    def test_room_combines_base_and_room(self):
        text = get_negative_prompt({}, prompt="房间场景参考图：客厅")
        self.assertIn("水印", text)
        self.assertIn("拥挤", text)
        self.assertIn("地板悬空", text)
        self.assertNotIn("穿模", text)

    def test_item_combines_base_and_item(self):
        text = get_negative_prompt({}, prompt="服装平铺参考图：米色衬衫")
        self.assertIn("水印", text)
        self.assertIn("穿模", text)
        self.assertIn("不对称", text)
        self.assertNotIn("拥挤", text)

    def test_default_only_base(self):
        text = get_negative_prompt({}, prompt="一只猫在窗台")
        self.assertEqual(text, DEFAULT_BASE + ", " + EXTRA_BASE)

    def test_scene_type_of(self):
        self.assertEqual(scene_type_of("卧室场景"), "room")
        self.assertEqual(scene_type_of("单品平铺"), "item")
        self.assertEqual(scene_type_of("普通风景"), "")

    def test_selfie_removes_person_terms(self):
        text = get_negative_prompt({}, "selfie", "自拍")
        self.assertNotIn("人物", text)
        self.assertNotIn("人类", text)
        self.assertIn("水印", text)

    def test_disabled_returns_empty(self):
        self.assertEqual(get_negative_prompt({"negative_prompt_enabled": False}, prompt="房间"), "")

    def test_custom_config_values(self):
        cfg = {"base_negative": "低画质", "room_negative": "地板悬空", "item_negative": "穿模"}
        self.assertEqual(get_negative_prompt(cfg, prompt="房间"),
                         "低画质, " + EXTRA_BASE + ", 地板悬空")

    def test_dedupe_tokens(self):
        cfg = {"base_negative": "水印, 模糊", "room_negative": "模糊, 拥挤"}
        self.assertEqual(get_negative_prompt(cfg, prompt="房间"),
                         "水印, 模糊, " + EXTRA_BASE + ", 拥挤")


class FallbackTests(unittest.TestCase):
    def test_merge_appends_natural_language(self):
        text = merge_negative("一只猫", "人物, 文字")
        self.assertIn("一只猫", text)
        self.assertIn("画面中不要出现：人物, 文字", text)

    def test_merge_is_idempotent(self):
        once = merge_negative("一只猫", "文字")
        self.assertEqual(merge_negative(once, "文字"), once)

    def test_empty_negative_keeps_prompt(self):
        self.assertEqual(merge_negative("一只猫", ""), "一只猫")


class ConfigTests(unittest.TestCase):
    def test_plugin_config_reads_image_generation(self):
        parent = os.path.dirname(ROOT)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        from Astrbot_amoupup_photo.models import PluginConfig
        cfg = PluginConfig.from_dict({
            "image_generation": {
                "negative_prompt_enabled": True,
                "base_negative": "水印",
                "room_negative": "拥挤",
                "item_negative": "穿模",
            }
        }, data_dir=ROOT, cleanup_persona_refs=False)
        self.assertEqual(cfg.base_negative, "水印")
        self.assertEqual(cfg.room_negative, "拥挤")
        self.assertEqual(cfg.item_negative, "穿模")
        self.assertTrue(cfg.negative_prompt_enabled)


class SchemaDefaultTests(unittest.TestCase):
    def test_schema_defaults_match_code_defaults(self):
        """配置页默认值与代码兜底默认值必须一致（2026-11-16 负向词去掉二次元/插画）。"""
        import json
        import pathlib

        schema = json.loads((pathlib.Path(ROOT) / "_conf_schema.json").read_text(encoding="utf-8"))
        items = schema["image_generation"]["items"]
        self.assertEqual(items["base_negative"]["default"], DEFAULT_BASE)
        self.assertEqual(items["room_negative"]["default"], DEFAULT_ROOM)
        self.assertEqual(items["item_negative"]["default"], DEFAULT_ITEM)


if __name__ == "__main__":
    unittest.main()
