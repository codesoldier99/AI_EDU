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
from packages.courseware.chat import ContentChatAgent
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

    def test_plan_deck_uses_concept_layout_and_leads_with_difficulty_chart(self):
        """课件不再是"标题+要点"两种页——每个知识点是 concept 布局，
        且固定带一页难度分布图（数据来自图谱标注，不依赖 LLM/学生数据，始终能生成）。"""
        syl_out = SyllabusAgent().generate(self.course_id)
        sid = cw_repo.save_syllabus(self.course_id, syl_out.plan)
        TeachingPlanAgent().generate(sid)
        first = cw_repo.list_teaching_plan(sid)[0]

        plan = DeckAgent().plan_deck(first["id"])
        layouts = [s["layout"] for s in plan.slides]
        self.assertEqual(layouts[0], "title")
        self.assertEqual(layouts[1], "barchart")
        concept_slides = [s for s in plan.slides if s["layout"] == "concept"]
        self.assertEqual(len(concept_slides), len(first["kp_codes"]))
        for s in concept_slides:
            self.assertTrue(s["title"][0] in "💡🔧🎯🛠", "标题必须带确定性图标前缀")
        chart_slide = plan.slides[1]
        self.assertEqual(len(chart_slide["chart"]["series"][0]["values"]), len(first["kp_codes"]))

    def test_pitfalls_grounded_in_real_error_pattern_when_available(self):
        """常见误区优先取真实错误模式库；没有真实数据时 grounded=False，
        不能把两者混为一谈（诚实标注，见 packages/courseware/deck.py 顶部说明）。"""
        from packages.errors import service as errors

        errors.record_error(
            student_id=self.student, kp_id=self.kp["A"], raw_text="示例错误作答",
        )
        # 人工确认，让它进入 typical_errors_for 的高优先级
        rows = errors.list_patterns(kp_id=self.kp["A"])
        if rows:
            errors.verify_pattern(rows[0].id, "教师确认")

        syl_out = SyllabusAgent().generate(self.course_id)
        sid = cw_repo.save_syllabus(self.course_id, syl_out.plan)
        TeachingPlanAgent().generate(sid)
        sessions = cw_repo.list_teaching_plan(sid)
        target = next(s for s in sessions if "A" in s["kp_codes"])

        plan = DeckAgent().plan_deck(target["id"])
        slide_a = next(s for s in plan.slides
                       if s["layout"] == "concept" and "A" in s["kp_codes"])
        other_slides = [s for s in plan.slides
                        if s["layout"] == "concept" and "A" not in s["kp_codes"]]
        self.assertTrue(slide_a["pitfalls"])
        self.assertTrue(slide_a["pitfalls_grounded"])
        for s in other_slides:  # 没有真实数据的知识点不应该被误标成"真实"
            self.assertFalse(s["pitfalls_grounded"])

    def test_render_with_rich_content_produces_valid_pptx(self):
        """concept/barchart 两种新布局在离线模式下也必须能渲染出合法的 pptx，
        不是只有结构没有实际产物。"""
        syl_out = SyllabusAgent().generate(self.course_id)
        sid = cw_repo.save_syllabus(self.course_id, syl_out.plan)
        TeachingPlanAgent().generate(sid)
        first = cw_repo.list_teaching_plan(sid)[0]

        agent = DeckAgent()
        plan = agent.plan_deck(first["id"])
        plan, _ = agent.fill_content(plan)
        out = agent.render(plan)
        z = zipfile.ZipFile(out.plan["file_path"])
        self.assertIsNone(z.testzip())
        n_slides = sum(1 for n in z.namelist()
                       if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        self.assertEqual(n_slides, len(plan.slides))
        # 难度图那页（第 2 张）必须真的含数值文本，不是空壳
        chart_xml = z.read("ppt/slides/slide2.xml").decode("utf-8")
        self.assertIn("knowledge_point.difficulty", chart_xml)


class TestContentChatAgent(DBTestCase):
    """对话式修订：只改"怎么说"，结构（章节/知识点/页数）必须原样不动。"""

    seed_course = True

    def setUp(self):
        super().setUp()
        self._orig_path = CONFIG.officecli_path
        CONFIG.officecli_path = ""  # 强制走内建降级引擎
        self.syl_out = SyllabusAgent().generate(self.course_id)
        self.syllabus_id = cw_repo.save_syllabus(self.course_id, self.syl_out.plan)
        TeachingPlanAgent().generate(self.syllabus_id)
        self.session = cw_repo.list_teaching_plan(self.syllabus_id)[0]
        plan = DeckAgent().plan_deck(self.session["id"])
        plan, _ = DeckAgent().fill_content(plan)  # populate bullets so tests have real content
        self.deck = DeckAgent().render(plan)
        self.deck_id = self.deck.plan["deck_id"]
        self.chat = ContentChatAgent()

    def tearDown(self):
        CONFIG.officecli_path = self._orig_path
        super().tearDown()

    def test_refine_syllabus_chapter_does_not_change_structure_then_save_persists(self):
        before = cw_repo.get_syllabus(self.syllabus_id)["content"]["chapters"]
        r = self.chat.refine_syllabus_chapter(self.syllabus_id, 1, "写短一点")
        self.assertIn("draft", r)
        self.chat.save_syllabus_chapter(self.syllabus_id, 1, r["draft"])
        after = cw_repo.get_syllabus(self.syllabus_id)["content"]["chapters"]
        # 结构字段（kp_codes/kp_names/unit）必须逐章完全不变，只有 narrative 可能变
        for b, a in zip(before, after):
            self.assertEqual(b["kp_codes"], a["kp_codes"])
            self.assertEqual(b["unit"], a["unit"])
        self.assertEqual(after[0]["narrative"], r["draft"])

    def test_refine_session_then_save_persists(self):
        r = self.chat.refine_session(self.session["id"], "加一句课堂互动")
        self.chat.save_session(self.session["id"], r["draft"])
        item = cw_repo.get_teaching_plan_item(self.session["id"])
        self.assertEqual(item["narrative"], r["draft"])

    def test_refine_slide_in_degraded_mode_keeps_original_bullets_unfragmented(self):
        """回归测试：离线降级模式下改写要点，不能把免责声明文字拆进 bullets 里
        （曾经的 bug：re-split 免责文案导致最后一条被从中间截断）。"""
        deck = cw_repo.get_deck(self.deck_id)
        slide_idx = next(i for i, s in enumerate(deck["deck_plan"]["slides"])
                         if s["layout"] == "concept" and s["bullets"])
        original_bullets = deck["deck_plan"]["slides"][slide_idx]["bullets"]

        r = self.chat.refine_slide(self.deck_id, slide_idx, "换个更口语化的说法")
        self.assertTrue(r["degraded"])  # 测试环境没有配置真实 LLM Key
        self.assertEqual(r["bullets"], original_bullets)
        for b in r["bullets"]:
            self.assertNotIn("离线模式", b)  # 免责声明不应该混进要点列表

    def test_save_slide_then_rerender_reflects_new_bullets_in_file(self):
        deck = cw_repo.get_deck(self.deck_id)
        slide_idx = next(i for i, s in enumerate(deck["deck_plan"]["slides"])
                         if s["layout"] == "concept")
        new_bullets = ["全新要点甲", "全新要点乙"]
        self.chat.save_slide(self.deck_id, slide_idx, new_bullets)

        saved = cw_repo.get_deck(self.deck_id)
        self.assertEqual(saved["deck_plan"]["slides"][slide_idx]["bullets"], new_bullets)

        out = self.chat.rerender_after_edit(self.deck_id)
        self.assertTrue(out.degraded)
        file_path = Path(out.plan["file_path"])
        self.assertTrue(file_path.exists())
        z = zipfile.ZipFile(file_path)
        xml = z.read(f"ppt/slides/slide{slide_idx + 1}.xml").decode("utf-8")
        self.assertIn("全新要点甲", xml)
        self.assertIn("全新要点乙", xml)


if __name__ == "__main__":
    unittest.main()
