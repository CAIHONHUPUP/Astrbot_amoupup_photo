#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Astrbot_amoupup_photo 质量门禁（2026-11-17 FIX-3 新增；此前 photo 没有 gate）。

口径与 forever / data 的 tools/quality_gate.py 对齐：
  clean_pycache / compile / pyflakes / main.py 行数 / 函数长度 / 静默 except / pytest

用法：python tools/quality_gate.py [--fast]   （--fast 只跑静态检查，跳过 pytest）
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_MAX_LINES = 1100
FUNC_FAIL_LINES = 400
FUNC_WARN_LINES = 250
#: 静默 except 必须显式标注原因；历史 # cleanup 标记继续承认。
SILENT_MARKS = ("# silent:", "# cleanup")
SKIP_DIRS = {"__pycache__", ".git", "tests", "tools", "_test_tmp", ".pytest_cache",
             ".test-tmp", "node_modules"}
PYFLAKES_LINE = re.compile(r"^(.+?):(\d+):(?:\d+:)? (.*)$")


def _walk_py(include_tests):
    out = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(names):
            if name.endswith(".py"):
                out.append(os.path.join(base, name))
    if include_tests:
        for base, _dirs, names in os.walk(os.path.join(ROOT, "tests")):
            for name in sorted(names):
                if name.endswith(".py"):
                    out.append(os.path.join(base, name))
    return out


def clean_pycache():
    """旧 __pycache__ 会让 Python 复用「mtime+size 相同、内容不同」的 pyc，测试假红。"""
    removed = 0
    for base, dirs, _names in os.walk(ROOT):
        for name in list(dirs):
            if name == "__pycache__":
                shutil.rmtree(os.path.join(base, name), ignore_errors=True)
                dirs.remove(name)
                removed += 1
    return removed


def check_schema():
    path = os.path.join(ROOT, "_conf_schema.json")
    if not os.path.isfile(path):
        return "schema 缺省（无 _conf_schema.json）"
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, dict) and data, "配置 schema 不是非空对象"
    return "schema ok（%d keys）" % len(data)


def check_compile():
    files = _walk_py(include_tests=True)
    for path in files:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        try:
            compile(source, path, "exec")
        except SyntaxError as exc:
            raise AssertionError("编译失败 %s: %s" % (path, exc))
    return "compile ok（%d 文件）" % len(files)


def _noqa_by_file(files):
    keep = {}
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        marks = {index for index, line in enumerate(lines, 1) if "# noqa" in line}
        if marks:
            keep[os.path.normcase(os.path.abspath(path))] = marks
    return keep


def check_pyflakes():
    files = _walk_py(include_tests=True)
    proc = subprocess.run([sys.executable, "-m", "pyflakes"] + files,
                          capture_output=True, text=True)
    noqa = _noqa_by_file(files)
    problems = []
    for raw in (proc.stdout + proc.stderr).splitlines():
        match = PYFLAKES_LINE.match(raw)
        if not match:
            continue
        path, line_no, message = match.group(1), int(match.group(2)), match.group(3)
        if line_no in noqa.get(os.path.normcase(os.path.abspath(path)), set()):
            continue
        problems.append("%s:%d %s" % (os.path.relpath(path, ROOT), line_no, message))
    assert not problems, "pyflakes 有告警：" + "；".join(problems[:8])
    return "pyflakes clean（%d 文件）" % len(files)


def check_size():
    path = os.path.join(ROOT, "main.py")
    with open(path, encoding="utf-8") as fh:
        total = len(fh.read().splitlines())
    assert total <= MAIN_MAX_LINES, "main.py %d 行 > %d" % (total, MAIN_MAX_LINES)
    return "main.py %d 行（上限 %d）" % (total, MAIN_MAX_LINES)


def check_function_sizes():
    problems = []
    longest = ("", "", 0)
    for path in _walk_py(include_tests=False):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            size = (node.end_lineno or node.lineno) - node.lineno + 1
            if size > longest[2]:
                longest = (rel, node.name, size)
            if size >= FUNC_FAIL_LINES:
                problems.append("%s::%s %d >= %d" % (rel, node.name, size, FUNC_FAIL_LINES))
            elif size >= FUNC_WARN_LINES:
                print("[gate][warn] %s::%s %d 行，考虑拆一下" % (rel, node.name, size))
    assert not problems, "函数太长：" + "；".join(problems[:6])
    return "functions ok（最长 %s::%s %d 行）" % longest


def _silent_excepts():
    total, spots, marked = 0, [], 0
    for path in _walk_py(include_tests=False):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        tree = ast.parse(text, filename=path)
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = [n for n in node.body
                    if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                            and isinstance(n.value.value, str))]
            if not (len(body) == 1 and isinstance(body[0], ast.Pass)):
                continue
            head = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
            tail = lines[body[0].lineno - 1] if body[0].lineno - 1 < len(lines) else ""
            if any(mark in head or mark in tail for mark in SILENT_MARKS):
                marked += 1
                continue
            total += 1
            spots.append("%s:%d" % (os.path.relpath(path, ROOT).replace("\\", "/"),
                                    node.lineno))
    return total, spots, marked


def check_silent_except():
    total, spots, marked = _silent_excepts()
    assert not total, ("有 %d 处未标注的静默 except：%s；请写 %s 或收窄"
                       % (total, ", ".join(spots[:8]), SILENT_MARKS[0]))
    return "silent except 0（%d 处已标注）" % marked


def check_tests():
    subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=ROOT, check=True)
    return "pytest ok"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="跳过 pytest")
    args = parser.parse_args()
    print("[gate] clean_pycache", clean_pycache())
    checks = [check_schema, check_compile, check_pyflakes, check_size,
              check_function_sizes, check_silent_except]
    if not args.fast:
        checks.append(check_tests)
    for check in checks:
        print("[gate]", check.__name__, check())
    print("[gate] 全部通过")


if __name__ == "__main__":
    main()
