"""教学技能包：教师用 Markdown 扩展系统，但扩不到"算不算掌握"那一层。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from base import DBTestCase, ROOT  # noqa: F401

from packages import skills
from packages.skills import loader


class TestSkillLoader(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig, loader.SKILLS_DIR = loader.SKILLS_DIR, Path(self._tmp.name)
        loader._cache.clear()

    def tearDown(self):
        loader.SKILLS_DIR = self._orig
        loader._cache.clear()
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        p = Path(self._tmp.name) / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_frontmatter_subset_parses_scalars_and_lists(self):
        meta, body = loader.parse_frontmatter(
            "---\nname: x\npriority: 7\napplies_to_kp_type: [concept, method]\n"
            "applies_to_course:\n  - ML\n  - DL\n---\n正文在这里\n"
        )
        self.assertEqual(meta["name"], "x")
        self.assertEqual(meta["priority"], 7)
        self.assertEqual(meta["applies_to_kp_type"], ["concept", "method"])
        self.assertEqual(meta["applies_to_course"], ["ML", "DL"])
        self.assertIn("正文", body)

    def test_always_directive_is_stripped(self):
        """技能包不许申明"任何场景都必须加载我"。"""
        self.write("a.md", "---\nname: a\nkind: asking_style\nalways: true\n---\n正文\n")
        pack = skills.load_all()[0]
        self.assertFalse(hasattr(pack, "always"))
        self.assertNotIn("always", pack.to_dict())

    def test_unknown_kind_is_reported_not_silently_loaded(self):
        self.write("b.md", "---\nname: b\nkind: mastery_override\n---\n正文\n")
        self.assertEqual(skills.load_all(), [])
        st = skills.stats()
        self.assertEqual(st["loaded"], 0)
        self.assertIn("未知 kind", st["broken"][0]["error"])

    def test_oversized_file_is_skipped(self):
        self.write("big.md", "---\nname: big\nkind: asking_style\n---\n" + "x" * 70000)
        self.assertEqual(skills.load_all(), [])

    def test_priority_orders_packs(self):
        self.write("lo.md", "---\nname: lo\nkind: asking_style\npriority: 1\n---\n低\n")
        self.write("hi.md", "---\nname: hi\nkind: asking_style\npriority: 9\n---\n高\n")
        self.assertEqual([p.name for p in skills.load_all()], ["hi", "lo"])

    def test_style_and_kp_type_filtering(self):
        self.write("c.md", "---\nname: c\nkind: asking_style\nstyle: 反例质疑\n"
                           "applies_to_kp_type: [concept]\n---\n正文\n")
        self.assertIsNotNone(skills.pick_asking_style("反例质疑", "concept"))
        self.assertIsNone(skills.pick_asking_style("类比迁移", "concept"))
        self.assertIsNone(skills.pick_asking_style("反例质疑", "skill"))

    def test_edit_takes_effect_without_restart(self):
        p = self.write("d.md", "---\nname: d\nkind: quiz_template\n---\n第一版内容\n")
        self.assertIn("第一版", skills.quiz_guidance())
        import os
        import time

        p.write_text("---\nname: d\nkind: quiz_template\n---\n第二版内容\n", encoding="utf-8")
        os.utime(p, (time.time() + 2, time.time() + 2))
        self.assertIn("第二版", skills.quiz_guidance())

    def test_sha_is_recorded_for_provenance(self):
        self.write("e.md", "---\nname: e\nkind: review_note\n---\n正文\n")
        self.assertEqual(len(skills.load_all()[0].sha256), 16)


class TestShippedSkills(unittest.TestCase):
    def test_repo_skills_all_load(self):
        st = skills.stats()
        self.assertEqual(st["broken"], [])
        self.assertGreaterEqual(st["by_kind"]["asking_style"], 1)
        self.assertGreaterEqual(st["by_kind"]["quiz_template"], 1)


class TestSkillsCannotChangeJudgement(DBTestCase):
    seed_course = True

    def test_asking_plan_style_is_decided_before_the_pack(self):
        """style 由 AskingStrategy 确定性选出，技能包只影响表达。"""
        from packages.agents.asking import AskingStrategy

        plan = AskingStrategy().decide(self.student, self.kp["C"])
        self.assertIn(plan.style, ("概念澄清", "反例质疑", "类比迁移", "条件变更"))
        pack = skills.pick_asking_style(plan.style)
        if pack:
            self.assertEqual(pack.kind, "asking_style")

    def test_loader_never_imports_state_or_llm(self):
        import ast

        src = (ROOT / "packages" / "skills" / "loader.py").read_text(encoding="utf-8")
        mods = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        for m in mods:
            self.assertFalse(m.startswith("packages.state"))
            self.assertFalse(m.startswith("packages.llm"))
            self.assertFalse(m.startswith("packages.agents"))


if __name__ == "__main__":
    unittest.main()
