"""教师工作台：教学大纲 / 授课计划 / 课件生成的单测。

覆盖点：
  - plan() 的确定性（同样输入产出同样结构，不依赖 LLM）
  - DeckPlan.kp_coverage() 的覆盖率校验
  - OfficeCLI 不可用时的降级路径（degraded=True 且仍产出有效 pptx）
  - repo 层的存取往返、端到端管线（大纲 -> 授课计划 -> 课件）
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from base import DBTestCase

from packages.core.config import CONFIG
from packages.courseware import officecli_render
from packages.courseware import repo as cw_repo
from packages.courseware.deck import DeckAgent
from packages.courseware.models import DeckPlan, SlidePlan
from packages.courseware.syllabus import SyllabusAgent
from packages.courseware.teaching_plan import TeachingPlanAgent
from packages.graph import repo as graph_repo


class TestSyllabusAgent(DBTestCase):
    seed_course = True

    def test_plan_is_deterministic_and_covers_all_kps(self):
        agent = SyllabusAgent()
        p1 = agent.plan(self.course_id)
        p2 = agent.plan(self.course_id)
        self.assertEqual(p1.to_dict(), p2.to_dict(), "同样输入必须产出同样的章节结构")
        all_codes = {k.code for k in graph_repo.list_kps(self.course_id)}
        self.assertEqual(set(p1.kp_coverage()), all_codes)

    def test_chapter_kp_count_sums_to_total(self):
        p = SyllabusAgent().plan(self.course_id)
        total = sum(len(c["kp_codes"]) for c in p.chapters)
        self.assertEqual(total, p.total_kps)
        self.assertEqual(p.total_kps, 4)   # build_mini_graph 固定造 A/B/C/D 四个知识点

    def test_empty_course_reports_caveat_not_llm_free_generation(self):
        empty_course_id = graph_repo.upsert_course("EMPTY", "空课程测试")
        out = SyllabusAgent().generate(empty_course_id)
        self.assertEqual(out.evidence_count, 0)
        self.assertIn("知识图谱为空", out.caveat)
        self.assertEqual(out.plan["chapters"], [])


class TestTeachingPlanAgent(DBTestCase):
    seed_course = True

    def test_sessions_respect_max_kp_per_session(self):
        out = SyllabusAgent().generate(self.course_id)
        sid = cw_repo.save_syllabus(self.course_id, out.plan)
        p = TeachingPlanAgent().plan(sid)
        for item in p.items:
            self.assertLessEqual(len(item["kp_codes"]), CONFIG.teaching.max_kp_per_session)

    def test_generate_persists_and_returns_ids(self):
        out = SyllabusAgent().generate(self.course_id)
        sid = cw_repo.save_syllabus(self.course_id, out.plan)
        pout = TeachingPlanAgent().generate(sid)
        rows = cw_repo.list_teaching_plan(sid)
        self.assertEqual(len(rows), len(pout.plan["items"]))
        self.assertGreater(len(rows), 0)

    def test_missing_syllabus_raises(self):
        with self.assertRaises(ValueError):
            TeachingPlanAgent().plan(999999)


class TestDeckPlan(unittest.TestCase):
    def test_kp_coverage_deduplicates(self):
        plan = DeckPlan(slides=[
            SlidePlan(layout="title", title="t").to_dict(),
            SlidePlan(layout="bullets", title="a", kp_codes=["K1", "K2"]).to_dict(),
            SlidePlan(layout="bullets", title="b", kp_codes=["K2", "K3"]).to_dict(),
        ])
        self.assertEqual(plan.kp_coverage(), ["K1", "K2", "K3"])


class TestOfficecliRenderFallback(unittest.TestCase):
    """不依赖真实 officecli 二进制：断言路径不可用时的降级行为。"""

    def setUp(self):
        self._orig_path = CONFIG.officecli_path
        CONFIG.officecli_path = "/definitely/not/a/real/binary/officecli"
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        CONFIG.officecli_path = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_is_available_false_when_binary_missing(self):
        self.assertFalse(officecli_render.is_available())

    def test_render_deck_degrades_to_builtin_and_produces_valid_pptx(self):
        plan = DeckPlan(title="降级测试课件", slides=[
            SlidePlan(layout="title", title="降级测试课件", subtitle="共 1 个知识点").to_dict(),
            SlidePlan(layout="bullets", title="示例知识点", kp_codes=["X-01"],
                      bullets=["要点一", "要点二"]).to_dict(),
        ])
        out_path = self.tmp / "deck.dat"
        result = officecli_render.render_deck(plan, out_path)
        self.assertTrue(result.degraded)
        self.assertEqual(result.render_tool, "builtin_stdlib")
        self.assertTrue(Path(result.file_path).exists())
        # 必须是能被正常打开的 zip（pptx 本质是 zip），不是半成品
        z = zipfile.ZipFile(result.file_path)
        self.assertIsNone(z.testzip())
        self.assertIn("ppt/slides/slide1.xml", z.namelist())
        self.assertIn("ppt/slides/slide2.xml", z.namelist())


class TestDeckAgentEndToEnd(DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        self._orig_path = CONFIG.officecli_path
        CONFIG.officecli_path = ""  # 强制走内建降级引擎，测试不依赖外部二进制

    def tearDown(self):
        CONFIG.officecli_path = self._orig_path
        super().tearDown()

    def test_full_pipeline_syllabus_to_deck(self):
        syl_out = SyllabusAgent().generate(self.course_id)
        sid = cw_repo.save_syllabus(self.course_id, syl_out.plan)
        TeachingPlanAgent().generate(sid)
        first = cw_repo.list_teaching_plan(sid)[0]

        agent = DeckAgent()
        plan = agent.plan_deck(first["id"])
        self.assertGreaterEqual(len(plan.slides), 2)  # 至少标题页 + 一个知识点页
        out = agent.render(plan)
        self.assertTrue(out.degraded)  # 官方渲染器不可用，必然降级
        self.assertEqual(out.plan["render_tool"], "builtin_stdlib")
        deck = cw_repo.get_deck(out.plan["deck_id"])
        self.assertIsNotNone(deck)
        self.assertTrue(Path(deck["file_path"]).exists())

    def test_deck_covers_session_kps_or_reports_missing(self):
        """课件覆盖率校验：这是"质量比纯 LLM 生成更高"的可判定证据。"""
        syl_out = SyllabusAgent().generate(self.course_id)
        sid = cw_repo.save_syllabus(self.course_id, syl_out.plan)
        TeachingPlanAgent().generate(sid)
        first = cw_repo.list_teaching_plan(sid)[0]

        agent = DeckAgent()
        plan = agent.plan_deck(first["id"])
        out = agent.render(plan)
        self.assertEqual(out.plan["missing_kp_codes"], [])
        self.assertEqual(set(out.plan["kp_coverage"]), set(first["kp_codes"]))


if __name__ == "__main__":
    unittest.main()
