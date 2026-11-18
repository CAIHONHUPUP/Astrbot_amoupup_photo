# -*- coding: utf-8 -*-
"""providers.base.extract_image_url_from_response 行为快照（拆函数前生成）。"""
import base64
import json
import os
import unittest

import test_custom_endpoint as fixtures

GOLD = json.load(open(os.path.join(os.path.dirname(__file__), "data",
                                   "extract_image_url_golden.json"), encoding="utf-8"))
TINY = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 8).decode()
LONG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 200).decode()
BASE = "https://api.example.com/v1"

CASES = {
    "plain_url": ({"data": [{"url": "https://cdn.example.com/a.png"}]}, BASE, None),
    "bare_url_text": ({"text": "https://cdn.example.com/a.jpg"}, BASE, None),
    "data_url": ({"image": "data:image/png;base64," + TINY}, BASE, None),
    "markdown": ({"content": "![x](https://cdn.example.com/a.webp)"}, BASE, None),
    "json_text": ({"result": json.dumps({"data": [{"url": "https://cdn.example.com/b.png"}]})}, BASE, None),
    "b64_json": ({"data": [{"b64_json": LONG}]}, BASE, None),
    "b64_short_png": ({"b64_json": TINY}, BASE, None),
    "gemini_inline": ({"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": TINY}}]}}]}, BASE, None),
    "gemini_bad_mime": ({"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "text/plain", "data": TINY}}]}}]}, BASE, None),
    "image_generation_call": ({"type": "image_generation_call", "result": TINY}, BASE, None),
    "nested_list": ({"data": [{"foo": 1}, {"url": "https://x.example/y.png"}]}, BASE, None),
    "invalid": ({"data": [{"b64_json": "!!!!"}]}, BASE, None),
    "max_bytes_error": ({"b64_json": LONG}, BASE, 1),
    "relative_uri": ({"uri": "/img/a.png"}, BASE, None),
    "empty": ({}, BASE, None),
}


class ExtractImageUrlCharacterizationTests(unittest.TestCase):
    def test_all_golden_payloads_match(self):
        for name, (payload, base, max_bytes) in CASES.items():
            with self.subTest(name=name):
                try:
                    result = fixtures.extract_image_url_from_response(
                        payload, base, max_inline_bytes=max_bytes)
                except Exception as exc:
                    self.assertEqual(GOLD[name], {
                        "error": "%s: %s" % (type(exc).__name__, exc)})
                else:
                    self.assertEqual(GOLD[name], {"result": result})


if __name__ == "__main__":
    unittest.main()
