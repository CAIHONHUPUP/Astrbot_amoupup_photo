# -*- coding: utf-8 -*-
"""回归：副脑关闭/降级时也必须保住 amo 传进来的场景与姿势锁（2026-11-15）。"""
import asyncio
import importlib
import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))

def _stub(name, **attrs):
    module = types.ModuleType(name)
    module.__path__ = []
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)
    return module


_log = __import__("logging").getLogger("test")
_astrbot = _stub("astrbot")
_api = _stub("astrbot.api", logger=_log)
_astrbot.api = _api
_stub("astrbot.api.event", AstrMessageEvent=object)
_stub("astrbot.api.event.components")
_stub("astrbot.api.message_components")
_stub("astrbot.api.star", Context=object, Star=object)
_stub("quart")

optimizer_mod = importlib.import_module(PLUGIN_DIR.name + ".core.prompt_optimizer")
PromptOptimizer = optimizer_mod.PromptOptimizer
locked_prompt = optimizer_mod.locked_prompt


class _Config:
    enable_optimizer = False
    providers: list[object] = []
    chains: dict[str, object] = {}


def test_locked_prompt_appends_and_dedupes():
    assert locked_prompt("standing by window", "in a subway car") == [
        "standing by window, in a subway car"
    ]
    assert locked_prompt("x, in a car", scene_lock="in a car") == ["x, in a car"]


def test_disabled_optimizer_still_applies_locks():
    optimizer = PromptOptimizer(_Config())
    out = asyncio.run(optimizer.optimize(
        "standing by the window", count=2,
        scene_lock="in a subway car", pose_lock="holding a strap",
    ))
    assert len(out) == 2
    assert "subway car" in out[0]
    assert "strap" in out[0]


def test_empty_action_keeps_locks():
    optimizer = PromptOptimizer(_Config())
    out = asyncio.run(optimizer.optimize("", count=1, scene_lock="in a subway car"))
    assert out == ["in a subway car"]
