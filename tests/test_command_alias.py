# -*- coding: utf-8 -*-
"""命令改名兼容：/定格帮助 是 /万象帮助 的别名，旧名继续可用。"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import constants  # noqa: E402


class HelpCommandAliasTests(unittest.TestCase):
    def test_new_help_command_is_reserved(self):
        self.assertIn("定格帮助", constants.RESERVED_PRESET_NAMES)

    def test_old_help_command_still_reserved_as_alias(self):
        self.assertIn("万象帮助", constants.RESERVED_PRESET_NAMES)

    def test_cmd_help_registers_new_alias_and_keeps_old_name(self):
        tree = ast.parse((ROOT / "main_commands.py").read_text(encoding="utf-8"))
        target = next(node for node in ast.walk(tree)
                      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "cmd_help")
        joined = " ".join(ast.unparse(item) for item in target.decorator_list)
        self.assertIn("万象帮助", joined)
        self.assertIn("定格帮助", joined)
        self.assertIn("alias=", joined)


if __name__ == "__main__":
    unittest.main()
