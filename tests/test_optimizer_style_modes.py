# -*- coding: utf-8 -*-
"""[amo patch] 副脑风格模式回归：自定义风格、结果包装、旧画风清理（2026-11-16）。"""
import asyncio
import importlib
import json
import logging
import pathlib
import sys
import types
from types import SimpleNamespace

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR.parent))


# 本文件可以单独跑：不能依赖别的测试文件先 stub 掉 astrbot/quart；
# 自己加的 stub 导入完就撤掉，别把其他测试文件的 stub 环境弄脏。
_added_stubs = []


def _stub(name, **attrs):
    module = sys.modules.get(name)
    if module is not None:
        return module
    module = types.ModuleType(name)
    module.__path__ = []
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    _added_stubs.append(name)
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
for _name in _added_stubs:
    sys.modules.pop(_name, None)
PromptOptimizer = _mod.PromptOptimizer
apply_style_lock = _mod.apply_style_lock

CUSTOM_STYLE = "\u81ea\u5b9a\u4e49\u6a21\u5f0f"
ANIME_STYLE = "\u65e5\u7cfb\u63d2\u753b\u5927\u5e08"

ITEM = {
    "subject_appearance": "a girl with short hair",
    "clothing_and_accessories": "a grey hoodie",
    "pose_and_action": "sitting on a sofa",
    "environment_and_scene": "a small living room",
    "lighting_and_mood": "warm lamp light",
    "technical_specs": "casual smartphone photo, natural colors",
    "realism_and_quality_guardrails": "clean anatomy",
}


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}
        self.charset = "utf-8"

    def raise_for_status(self):
        return None

    async def read(self):
        return json.dumps(self.payload).encode("utf-8")

    async def text(self):
        return json.dumps(self.payload)


class _Post:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


class _Session:
    def __init__(self, response):
        self.response = response
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append(kwargs)
        return _Post(self.response)


def _session_for(content):
    return _Session(_Response({"choices": [{"message": {"content": content}}]}))


class _Provider:
    id = "node_1"
    api_type = "openai_chat"
    base_url = "https://api.example.com/v1"
    api_keys = ["key-a"]
    model = "chat-model"


def _config(style, custom_prompt=""):
    provider = _Provider()
    cfg = SimpleNamespace(
        enable_optimizer=True,
        chains={"optimizer": [provider.id]},
        providers=[provider],
        optimizer_model="chat-model",
        optimizer_timeout=15.0,
        optimizer_style=style,
        optimizer_custom_prompt=custom_prompt,
    )
    cfg.get_provider = lambda provider_id: provider if provider_id == provider.id else None
    return cfg


def test_custom_mode_english_prompt_reaches_style_slots():
    style_data, _ = PromptOptimizer(_config(CUSTOM_STYLE))._build_style_data(
        CUSTOM_STYLE, "watercolor painting, soft edges"
    )
    assert "watercolor painting, soft edges" in style_data["role"]


def test_custom_mode_chinese_prompt_reaches_style_slots():
    style_data, _ = PromptOptimizer(_config(CUSTOM_STYLE))._build_style_data(
        CUSTOM_STYLE, "\u6c34\u5f69\u753b\uff0c\u67d4\u548c\u8fb9\u7f18"
    )
    assert "\u6c34\u5f69\u753b" in style_data["role"]


def test_custom_mode_english_prompt_does_not_break_optimize():
    """以前这里先抛 UnboundLocalError，再在 _build_system_prompt 抛 KeyError。"""
    session = _session_for(json.dumps(ITEM))
    out = asyncio.run(
        PromptOptimizer(_config(CUSTOM_STYLE, "watercolor painting")).optimize(
            "she sits on the sofa", 1, session=session
        )
    )
    assert len(out) == 1
    assert "a girl with short hair" in out[0]


def test_single_count_wrapped_results_keep_content():
    """模型把单个对象包成 {"results": [{...}]} 时，不能只剩风格句。"""
    session = _session_for(json.dumps({"results": [ITEM]}))
    out = asyncio.run(
        PromptOptimizer(_config("\u624b\u673a\u65e5\u5e38\u539f\u751f\u611f")).optimize(
            "a cat under neon lights", 1, session=session
        )
    )
    assert "a girl with short hair" in out[0]
    assert "a small living room" in out[0]
    assert "a cat under neon lights" not in out[0]


def test_anime_lock_drops_shortened_preset_style_phrases():
    style_data, _ = PromptOptimizer(_config(ANIME_STYLE))._build_style_data(ANIME_STYLE, "")
    items = [{
        "technical_specs": "polished 2D illustration, clean linework, high resolution, balanced composition",
    }]
    out = apply_style_lock(items, style_data, ANIME_STYLE)
    tech = out[0]["technical_specs"]
    assert "semi-realistic anime illustration" in tech
    assert "2D illustration" not in tech
    assert "linework" not in tech
    assert "high resolution" in tech
    assert "balanced composition" in tech


def test_anime_lock_only_drops_medium_words_not_camera_tokens():
    style_data = {
        "camera": "2D illustration, 35mm lens, shallow depth of field, golden hour light, balanced composition"
    }
    # 副脑常把相机句写短/加料，整句 replace 命中不了，只能按段清画风词
    items = [{
        "technical_specs": "2D illustration, 35mm lens, balanced composition, handheld framing",
        "lighting_and_mood": "golden hour light through a window",
        "environment_and_scene": "a cozy living room",
    }]
    out = apply_style_lock(items, style_data, ANIME_STYLE)

    tech = out[0]["technical_specs"]
    assert "2D illustration" not in tech
    for token in ("35mm lens", "balanced composition", "handheld framing"):
        assert token in tech
    assert out[0]["lighting_and_mood"] == "golden hour light through a window"
    assert out[0]["environment_and_scene"] == "a cozy living room"

    first = tech
    apply_style_lock(items, style_data, ANIME_STYLE)
    assert out[0]["technical_specs"] == first


def test_style_without_override_keeps_every_camera_token():
    style_data = {"camera": "polished 2D illustration, clean linework, high resolution"}
    items = [{"technical_specs": "polished 2D illustration, clean linework, high resolution"}]
    out = apply_style_lock(items, style_data, "手机日常原生感")
    assert out[0]["technical_specs"] == "polished 2D illustration, clean linework, high resolution"


def test_anime_lock_is_still_idempotent_with_segment_cleanup():
    style_data, _ = PromptOptimizer(_config(ANIME_STYLE))._build_style_data(ANIME_STYLE, "")
    items = [{"technical_specs": "polished 2D illustration, high resolution"}]
    apply_style_lock(items, style_data, ANIME_STYLE)
    first = items[0]["technical_specs"]
    apply_style_lock(items, style_data, ANIME_STYLE)
    assert items[0]["technical_specs"] == first
    assert first.lower().count("semi-realistic anime illustration") == 1
