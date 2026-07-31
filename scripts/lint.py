"""静态检查。有 ruff 就用 ruff，没有就用内置的最小自检（零依赖环境的兜底）。

自检项刻意只保留三条硬规范，避免变成风格洁癖：
  1. 所有 .py 语法可解析；
  2. 业务代码不得直接 import 大模型 SDK（必须经 packages/llm）；
  3. agents / apps 不得直接 import sqlite3 或写 SQL（必须经 repository）。
"""
from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
from packages.core.config import ROOT

FORBIDDEN_SDK = {"openai", "anthropic", "dashscope", "zhipuai", "langchain"}
SQL_KEYWORDS = ("SELECT ", "INSERT INTO", "UPDATE ", "DELETE FROM")


def files() -> list[Path]:
    out = []
    for d in ("packages", "apps", "scripts", "tests"):
        out += sorted((ROOT / d).rglob("*.py"))
    return out


def main() -> int:
    if shutil.which("ruff"):
        print("→ ruff")
        return subprocess.call(["ruff", "check", "packages", "apps", "scripts", "tests"])

    print("→ 内置自检（未安装 ruff）")
    problems: list[str] = []
    for f in files():
        src = f.read_text(encoding="utf-8")
        rel = f.relative_to(ROOT)
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            problems.append(f"{rel}:{e.lineno} 语法错误 {e.msg}")
            continue
        in_llm_pkg = str(rel).startswith("packages/llm")
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module.split(".")[0]]
            for m in mods:
                if m in FORBIDDEN_SDK and not in_llm_pkg:
                    problems.append(f"{rel}:{node.lineno} 禁止直接 import {m}，必须经 packages/llm")
                if m == "sqlite3" and str(rel).startswith(("packages/agents", "apps")):
                    problems.append(f"{rel}:{node.lineno} 禁止在此层 import sqlite3，走 repository")
        # agents 层允许 get_db().query 做只读聚合查询，写操作一律在 repo 层收口；
        # 这条规则由 tests/test_layering.py 检查，lint 不重复。
    for p in problems:
        print(f"  ✗ {p}")
    print(f"{'通过' if not problems else str(len(problems)) + ' 处问题'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
