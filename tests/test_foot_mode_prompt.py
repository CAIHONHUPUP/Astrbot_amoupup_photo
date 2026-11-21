# -*- coding: utf-8 -*-
"""脚趾图：selfie 模式不再硬加 arm's length / head-to-thighs。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _custom_endpoint_helpers import PromptOptimizer  # noqa: E402


def _rules(skip):
    opt = PromptOptimizer.__new__(PromptOptimizer)
    return opt._apply_reference_rules({}, False, True, False, "", "", "", "", skip_selfie_template=skip)


def test_foot_mode_replaces_selfie_rule():
    style, rule, _scene, _pose = _rules(True)
    assert "FOOT CLOSE-UP MODE" in rule
    assert "SELFIE MODE" not in rule
    assert "arm's length" not in rule
    assert "low-angle" in style["camera"]


def test_selfie_mode_unchanged():
    _style, rule, _scene, _pose = _rules(False)
    assert "SELFIE MODE" in rule
    assert "head to thighs" in rule
