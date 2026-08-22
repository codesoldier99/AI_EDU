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

    def test_quiz_layer_never_touches_llm(self):
        """题库、选题、判分全部是确定性的。

        这条是铁律 3 在"考"这件事上的落地：出题的**表达**可以换模型，
        "算不算对"不行。判分一旦掺进模型，成绩就不可复算了。
        """
        for f in pkg_files("quiz"):
            for m in imports_of(f):
                self.assertFalse(m.startswith("packages.llm"), f"{f.name} 引用了 llm")
                self.assertFalse(m.startswith("packages.agents"), f"{f.name} 引用了 agents")

    def test_exam_never_touches_llm_or_agents(self):
        """考试是判定，不是表达。整条链路上不许出现大模型。

        分数决定谁进实验班、也是两年纵向研究的基线。任何一处让模型参与判定，
        这个基线就不可复算，论文里也交代不过去。
        """
        for f in pkg_files("exam"):
            for m in imports_of(f):
                self.assertFalse(m.startswith("packages.llm"), f"{f.name} 引用了 llm")
                self.assertFalse(m.startswith("packages.agents"), f"{f.name} 引用了 agents")

    def test_anchor_items_excluded_from_practice_pool(self):
        """概念锚题两年内要重测，绝不能进日常组卷池——泄漏一次纵向比较就报废。"""
        src = (ROOT / "packages" / "quiz" / "bank.py").read_text(encoding="utf-8")
        self.assertIn("anchor", src)
        for fn in ("list_for_kp", "list_pending"):
            body = src.split(f"def {fn}(")[1].split("\ndef ")[0]
            self.assertIn("origin<>'anchor'", body, f"{fn} 没有排除锚题")

    def test_kpmatch_never_writes_the_real_mapping(self):
        """任务-知识点映射是学分认定的依据，只能由教师采纳后写入。

        自动匹配可以提候选、可以排序、可以解释，就是不能自己落笔。
        这条一旦失守，"导师标注"就悄悄变成了"模型推测"，
        而且看不出来——所有下游（缺口、挡路度、能力追溯）都会照单全收。
        """
        f = ROOT / "packages" / "agents" / "kpmatch.py"
        ids = identifiers(f)
        self.assertNotIn("link_task_kp", ids, "匹配智能体直接写了正式映射表")
        self.assertNotIn("decide_candidate", ids, "匹配智能体自己替教师做了裁决")

    def test_lexmatch_is_a_leaf_tool(self):
        """判定部分必须是零依赖的确定性工具，换模型不影响候选与分数。"""
        for m in imports_of(ROOT / "packages" / "tools" / "lexmatch.py"):
            self.assertFalse(m.startswith("packages."), f"lexmatch 依赖了 {m}")

    def test_tools_depend_on_nothing(self):
        """确定性工具箱是叶子节点：谁都能用它，它谁都不用。"""
        for f in pkg_files("tools"):
            for m in imports_of(f):
                self.assertFalse(m.startswith("packages."), f"{f.name} 依赖了 {m}")

    def test_skills_cannot_reach_state_or_llm(self):
        """技能包只能影响"怎么说"，够不到"算不算掌握"。"""
        for f in pkg_files("skills"):
            for m in imports_of(f):
                self.assertFalse(m.startswith("packages.state"), f"{f.name} 引用了 state")
                self.assertFalse(m.startswith("packages.llm"), f"{f.name} 引用了 llm")
                self.assertFalse(m.startswith("packages.agents"), f"{f.name} 引用了 agents")

    def test_practice_priorities_are_not_accuracy_based(self):
        """铁律 5：练习排序不得以正确率 / 做题数 / 停留时长为依据。"""
        src = (ROOT / "packages" / "quiz" / "selector.py").read_text(encoding="utf-8")
        self.assertIn("PRIORITY_KINDS", src)
        block = src.split("PRIORITY_KINDS = ")[1].split("\n")[0]
        for bad in ("accuracy", "correct_rate", "score", "item_count", "dwell", "rank"):
            self.assertNotIn(bad, block)

    def test_grading_never_silently_guesses(self):
        """判不了必须能表达成"判不了"：GradeResult 的 is_correct 必须允许 None。"""
        src = (ROOT / "packages" / "quiz" / "grader.py").read_text(encoding="utf-8")
        self.assertIn("is_correct: bool | None", src)
        self.assertIn("pending_teacher", src)

    def test_no_direct_llm_sdk_import(self):
        """任何 LLM 调用必须经 packages/llm。"""
        sdks = {"openai", "anthropic", "dashscope", "zhipuai", "langchain", "litellm"}
        for d in ("graph", "state", "engagement", "errors", "rag", "agents", "adapters",
                  "quiz", "tools", "skills", "exam", "courseware"):
            for f in pkg_files(d):
                for m in imports_of(f):
                    self.assertNotIn(m.split(".")[0], sdks, f"{f} 直接 import 了大模型 SDK")

    def test_courseware_does_not_write_mastery_directly(self):
        """课件/大纲/授课计划生成层同样不得绕过 tracker 写掌握度（铁律 1）。"""
        for f in pkg_files("courseware"):
            src = f.read_text(encoding="utf-8")
            self.assertNotIn("write_mastery", identifiers(f), f"{f.name} 直接写了掌握度")
            self.assertNotIn("UPDATE mastery_state", src, f"{f.name} 直接改了掌握度表")
            self.assertNotIn("INSERT INTO mastery_state", src, f"{f.name} 直接写了掌握度表")

    def test_courseware_subprocess_confined_to_render_module(self):
        """外部渲染二进制（OfficeCLI）的调用必须收敛到 officecli_render.py 一处，
        便于审计与替换——不允许在包内其他文件里悄悄再开一条 subprocess 调用。"""
        for f in pkg_files("courseware"):
            if f.name == "officecli_render.py":
                continue
            self.assertNotIn("subprocess", identifiers(f), f"{f.name} 不应直接调用 subprocess")

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
