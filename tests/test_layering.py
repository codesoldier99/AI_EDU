"""架构铁律的静态检查。违反即返工，优先级高于一切。

这些不是风格建议，是会决定系统能活十年还是一年的约束，
所以用测试固化下来，而不是写在文档里靠自觉。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from base import ROOT


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def pkg_files(pkg: str) -> list[Path]:
    return sorted((ROOT / "packages" / pkg).rglob("*.py"))


def identifiers(path: Path) -> set[str]:
    """收集源码里真正被调用/引用的标识符。

    刻意不做文本匹配——注释与文档字符串里写"禁止 write_mastery"不应算违规。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.alias):
            out.add(node.name.split(".")[-1])
            if node.asname:
                out.add(node.asname)
    return out


class TestLayering(unittest.TestCase):
    def test_graph_does_not_depend_on_llm_or_state(self):
        """L1 是坐标系，不能反过来依赖上层。"""
        for f in pkg_files("graph"):
            for m in imports_of(f):
                self.assertFalse(m.startswith("packages.llm"), f"{f.name} 引用了 llm")
                self.assertFalse(m.startswith("packages.state"), f"{f.name} 引用了 state")
                self.assertFalse(m.startswith("packages.agents"), f"{f.name} 引用了 agents")

    def test_state_does_not_depend_on_llm_or_agents(self):
        for f in pkg_files("state"):
            for m in imports_of(f):
                self.assertFalse(m.startswith("packages.llm"), f"{f.name} 引用了 llm")
                self.assertFalse(m.startswith("packages.agents"), f"{f.name} 引用了 agents")
                self.assertFalse(m.startswith("packages.engagement"),
                                 f"{f.name} 反向依赖了 engagement")

    def test_engagement_never_writes_state(self):
        """铁律：禁止 packages/engagement 写入 packages/state。"""
        banned = {"write_mastery", "write_ability", "record", "recompute_ability", "tracker"}
        for f in pkg_files("engagement"):
            hit = banned & identifiers(f)
            self.assertFalse(hit, f"{f.name} 出现了写 state 的调用：{hit}")

    def test_agents_do_not_write_mastery_directly(self):
        """L3 不得直接写 L2 状态，只能经 tracker.record。"""
        for f in pkg_files("agents"):
            src = f.read_text(encoding="utf-8")
            self.assertNotIn("write_mastery", identifiers(f), f"{f.name} 直接写了掌握度")
            self.assertNotIn("UPDATE mastery_state", src, f"{f.name} 直接改了掌握度表")
            self.assertNotIn("INSERT INTO mastery_state", src, f"{f.name} 直接写了掌握度表")

    def test_no_direct_llm_sdk_import(self):
        """任何 LLM 调用必须经 packages/llm。"""
        sdks = {"openai", "anthropic", "dashscope", "zhipuai", "langchain", "litellm"}
        for d in ("graph", "state", "engagement", "errors", "rag", "agents", "adapters"):
            for f in pkg_files(d):
                for m in imports_of(f):
                    self.assertNotIn(m.split(".")[0], sdks, f"{f} 直接 import 了大模型 SDK")

    def test_api_layer_has_no_business_logic(self):
        """apps/api 仅路由与鉴权：不得出现 BKT、图算法等业务符号。"""
        # 允许路由直接调用图谱只读查询（如 blocking_severity），
        # 不允许出现任何状态计算或策略决策的实现。
        banned = {"BKTParams", "update_with_weight", "posterior", "trace_root_cause",
                  "compute_streak", "AskingStrategy"}
        for f in sorted((ROOT / "apps" / "api").rglob("*.py")):
            hit = banned & identifiers(f)
            self.assertFalse(hit, f"{f.name} 出现了业务逻辑实现：{hit}")

    def test_no_forbidden_optimization_targets(self):
        """禁止以当场正确率 / 做题数 / 停留时长作为激励对象。"""
        src = (ROOT / "packages" / "engagement" / "service.py").read_text(encoding="utf-8")
        self.assertIn("ALLOWED_KINDS", src)
        for bad in ("accuracy", "item_count", "dwell_time", "rank"):
            # 只允许出现在"禁止清单"里，不得出现在 ALLOWED_KINDS 中
            allowed_block = src.split("ALLOWED_KINDS = ")[1].split("\n")[0]
            self.assertNotIn(bad, allowed_block)

    def test_no_teacher_evaluation_data(self):
        """系统不生成、不留存任何针对教师的评价性数据。"""
        schema = (ROOT / "migrations" / "001_init.sql").read_text(encoding="utf-8")
        for bad in ("teacher_score", "teacher_rating", "teacher_rank", "teacher_evaluation"):
            self.assertNotIn(bad, schema)

    def test_event_stream_append_only_enforced(self):
        sql = (ROOT / "migrations" / "002_append_only.sql").read_text(encoding="utf-8")
        self.assertIn("learning_event_no_update", sql)
        self.assertIn("learning_event_no_delete", sql)


if __name__ == "__main__":
    unittest.main()
