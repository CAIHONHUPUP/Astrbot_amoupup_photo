# -*- coding: utf-8 -*-
"""兼容层：3273 行回归套件已按域拆到 test_custom_endpoint_*.py。

历史测试（test_gif_workflow / test_page_backend 等）仍按
import test_custom_endpoint as fixtures 取 fixtures 和测试类；这里用模块级
__getattr__ 惰性转发，既保留旧入口，又不会让拆出去测试类被 pytest 收集两次。
"""
from __future__ import annotations

import importlib

from _custom_endpoint_helpers import *  # noqa: F401,F403

_CLASS_MODULES = {
    "PromptOptimizerRegressionTest": "test_custom_endpoint_prompt",
    "GenerationMetadataConfigTest": "test_custom_endpoint_prompt",
    "RuntimeConfigKeyTest": "test_custom_endpoint_prompt",
    "CustomEndpointHelpersTest": "test_custom_endpoint_image",
    "ImageSuccessComponentsTest": "test_custom_endpoint_image",
    "PluginImageReturnTest": "test_custom_endpoint_image",
    "FastPresetListTest": "test_custom_endpoint_image",
    "PresetEventHandlerTest": "test_custom_endpoint_preset",
    "CommandPendingSendTest": "test_custom_endpoint_preset",
    "ChainManagerMetadataTest": "test_custom_endpoint_preset",
    "VideoSuccessMetadataTest": "test_custom_endpoint_preset",
    "GeminiOfficialProviderTest": "test_custom_endpoint_provider",
    "CustomEndpointProviderTest": "test_custom_endpoint_provider",
    "SecurityAndLimitRegressionTest": "test_custom_endpoint_security",
    "ReliabilityBoundaryRegressionTest": "test_custom_endpoint_reliability",
}


def __getattr__(name):
    module_name = _CLASS_MODULES.get(name)
    if module_name is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value
