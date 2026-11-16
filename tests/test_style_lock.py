# -*- coding: utf-8 -*-
"""[amo patch] 风格锁：预设风格句必须压过副脑写的冲突措辞。"""
import importlib
import logging
import pathlib
import sys
import types

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR.parent))


# 本文件可以单独跑：不能依赖别的测试文件先 stub 掉 astrbot/quart。
def _stub(name, **attrs):
    module = types.ModuleType(name)
    module.__path__ = []
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)
    return module


_log = logging.getLogger("test")
_astrbot = _stub("astrbot")
_api = _stub("astrbot.api", logger=_log)
_astrbot.api = _api
_stub("astrbot.api.event", AstrMessageEvent=object)
_stub("astrbot.api.event.components")
_stub("astrbot.api.message_components")
_stub("astrbot.api.star", Context=object, Star=object)
_stub("astrbot.api.utils", logger=_log)
_stub("quart")

_mod = importlib.import_module(PLUGIN_DIR.name + ".core.prompt_optimizer")
apply_style_lock = _mod.apply_style_lock


def test_style_lock_appends_preset_and_strips_conflicts():
    style = {"camera": "polished 2D illustration, clean linework, refined cel shading"}
    items = [{
        "technical_specs": "front-facing phone selfie quality, not an illustration",
        "lighting_and_mood": "real-life snapshot look, warm lamp glow",
        "environment_and_scene": "a real subway car, not a stock photo",
    }]
    out = apply_style_lock(items, style)
    assert out is items
    tech = out[0]["technical_specs"]
    assert "polished 2D illustration" in tech
    assert "not an illustration" not in tech
    assert "real-life snapshot" not in out[0]["lighting_and_mood"]
    assert "not a stock photo" not in out[0]["environment_and_scene"]
    assert "front-facing phone selfie quality" in tech


def test_style_lock_noop_without_camera():
    items = [{"technical_specs": "keep me"}]
    assert apply_style_lock(items, {})[0]["technical_specs"] == "keep me"
    assert apply_style_lock(None, {"camera": "x"}) is None

def test_locked_prompt_includes_style_lock():
    out = _mod.locked_prompt("她坐在沙发上笑", "楼下的布艺沙发", "", "semi-realistic anime illustration")
    assert "楼下的布艺沙发" in out[0]
    assert "semi-realistic anime illustration" in out[0]

def test_style_lock_is_not_duplicated_on_second_pass():
    style = {"camera": "polished 2D illustration, clean linework"}
    items = [{"technical_specs": "two hands, natural pose"}]
    apply_style_lock(items, style, "日系插画大师")
    first = items[0]["technical_specs"]
    apply_style_lock(items, style, "日系插画大师")
    second = items[0]["technical_specs"]
    assert second == first
    assert second.lower().count("semi-realistic anime illustration") == 1


def test_locked_prompt_does_not_repeat_lock_text():
    out = _mod.locked_prompt("she smiles, semi-realistic anime illustration",
                             "", "", "semi-realistic anime illustration")
    assert out[0].lower().count("semi-realistic anime illustration") == 1



def test_optimizer_disabled_still_carries_style_lock():
    import asyncio
    from types import SimpleNamespace

    cfg = SimpleNamespace(enable_optimizer=False, optimizer_style="日系插画大师",
                          optimizer_custom_prompt="")
    out = asyncio.run(_mod.PromptOptimizer(cfg).optimize("她坐在沙发上笑"))
    assert "semi-realistic anime illustration" in out[0]


def test_style_lock_uses_realistic_override_for_anime_preset():
    style = {"camera": "polished 2D illustration, clean linework, refined cel shading"}
    items = [{"technical_specs": "not an illustration, front-facing selfie"}]
    out = apply_style_lock(items, style, "日系插画大师")
    tech = out[0]["technical_specs"]
    assert "semi-realistic anime illustration" in tech
    assert "not an illustration" not in tech
    assert "polished 2D illustration" not in tech

