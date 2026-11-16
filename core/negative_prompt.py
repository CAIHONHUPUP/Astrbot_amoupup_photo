# -*- coding: utf-8 -*-
"""负面提示词（2026-11-15）：配置化、按场景组合、API 不支持时降级。

- base_negative：通用负面词（人物/文字/水印/畸形…）
- room_negative：房间场景追加（拥挤/杂乱/地板悬空）
- item_negative：衣服/物品追加（穿模/不对称/背景杂乱）
- get_negative_prompt(scene_type)：组合返回
- merge_negative(prompt, negative)：API 不支持独立 negative_prompt 时，把负面词
  作为自然语言附加到正向提示词末尾，保证仍然生效。
"""
from __future__ import annotations

DEFAULT_BASE = "人物, 人类, 文字, 水印, 签名, 畸形, 多余的肢体, 模糊, 低画质"
DEFAULT_ROOM = "拥挤, 杂乱无章, 地板悬空, 墙壁破损"
DEFAULT_ITEM = "穿模, 不对称, 背景杂乱"

ROOM_WORDS = ("房间", "客厅", "卧室", "室内", "家", "场景参考图", "房屋")
ITEM_WORDS = ("服装", "平铺", "单品", "物品", "摆件", "参考图", "product", "item")

FALLBACK_SUFFIX = "(画面中不要出现：%s)"


def _value(config, name, default):
    if config is None:
        return default
    if isinstance(config, dict):
        value = config.get(name)
    else:
        value = getattr(config, name, None)
    if value is None:
        return default
    return str(value).strip()


def scene_type_of(prompt, explicit=""):
    text = str(prompt or "")
    if explicit in ("room", "item", "selfie"):
        return explicit
    if any(word in text for word in ROOM_WORDS):
        return "room"
    if any(word in text for word in ITEM_WORDS):
        return "item"
    return ""


def get_negative_prompt(config, scene_type="", prompt=""):
    """按场景组合负面词；关闭时返回空串。"""
    if not _enabled(config):
        return ""
    scene = scene_type_of(prompt, scene_type)
    base = _value(config, "base_negative", DEFAULT_BASE)
    if scene == "selfie":
        # 自拍画面里本来就只有她，负面词不能再压“人物/人类”
        base = ",".join(token.strip() for token in base.replace("，", ",").split(",")
                        if token.strip() not in ("人物", "人类"))
    parts = [base]
    if scene == "room":
        parts.append(_value(config, "room_negative", DEFAULT_ROOM))
    elif scene == "item":
        parts.append(_value(config, "item_negative", DEFAULT_ITEM))
    merged = "，".join(part for part in parts if part)
    # 去重（保序）
    seen = set()
    out = []
    for token in merged.replace("，", ",").split(","):
        token = token.strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return ", ".join(out)


def _enabled(config):
    if config is None:
        return True
    if isinstance(config, dict):
        value = config.get("negative_prompt_enabled", True)
    else:
        value = getattr(config, "negative_prompt_enabled", True)
    if value is None:
        return True
    return str(value).strip().lower() not in ("0", "false", "off", "no")


def merge_negative(prompt, negative):
    """API 不支持独立负面参数时的降级：把负面词自然语言附在正向词末尾。"""
    body = str(prompt or "").strip()
    neg = str(negative or "").strip()
    if not neg:
        return body
    if neg in body:
        return body
    return (body + "\n" + FALLBACK_SUFFIX % neg).strip()
