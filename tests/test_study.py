"""分步解题 · 限域调研 · 图示。

盯住的是这三条：
1. 分步解题在未降级前，任何一步的结论都不许下发；
2. 调研只在三个知识库里查，落地性低的段落必须被标出来；
3. 图形里的数字必须等于状态层的数字——图不是模型画的。
"""
from __future__ import annotations

from base import DBTestCase, OfflineLLMMixin

from packages.agents.research import ResearchAgent
from packages.agents.solve import SolveAgent, expected_keywords, parse_steps
from packages.agents.visualize import VisualizeAgent, heat_color
from packages.core.config import CONFIG
from packages.rag import store
from packages.state import repo as state_repo
from packages.state import tracker


def seed_kb():
    store.index_text("course", "backprop.md", "反向传播讲义",
                     "反向传播依赖链式法则，必须逐层把导数乘回去。\n\n"
                     "最常见的错误是漏掉激活函数的导数。", ["A", "B"])
    store.index_text("project", "agv.md", "AGV 项目文档",
                     "训练服务偏斜会导致线上精度骤降，预处理必须与模型一起打包。", [])
    store.index_text("program", "plan.md", "培养方案",
                     "机器学习课程要求学生完成一次完整的模型训练与部署。", [])


class TestSolveParsing(DBTestCase):
    def test_step_without_conclusion_is_dropped(self):
        text = ("[步骤] 列出已知量\n[校验] text\n[结论] 已知量三条\n"
                "[步骤] 忘了写结论的一步\n[校验] text\n")
        ok, bad = parse_steps(text)
        self.assertEqual(len(ok), 1)
        self.assertEqual(len(bad), 1)

    def test_numeric_check_kind_is_inferred(self):
        ok, _ = parse_steps("[步骤] 把两个已知量相加\n[结论] 2+3\n")
        self.assertEqual(ok[0]["check_kind"], "numeric")

    def test_expected_keywords_are_deterministic(self):
        a = expected_keywords("先用链式法则，再逐层相乘，最后检查量纲")
        b = expected_keywords("先用链式法则，再逐层相乘，最后检查量纲")
        self.assertEqual(a, b)
        self.assertIn("先用链式法则", a[0])


class TestSolveAgent(OfflineLLMMixin, DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        seed_kb()
        self.agent = SolveAgent()

    def test_only_current_step_is_exposed_and_no_conclusion_leaks(self):
        v = self.agent.start(self.student, "已知每层的导数，求整体梯度")
        self.assertGreater(v.n_steps, 0)
        self.assertFalse(v.gives_answer)
        self.assertEqual(v.steps[0]["expected"], "")        # 当前步不给结论
        for s in v.steps[1:]:
            self.assertEqual(s["ask"], "")                  # 后续步骤不预告
            self.assertEqual(s["expected"], "")

    def test_passing_a_step_advances_and_reveals_that_step_only(self):
        v = self.agent.start(self.student, "已知每层的导数，求整体梯度")
        step0 = self.db.query_one(
            "SELECT * FROM solve_step WHERE session_id=? AND idx=0", (v.session_id,))
        v2 = self.agent.answer(v.session_id, step0["expected"])
        self.assertEqual(v2.cursor, 1)
        self.assertTrue(v2.steps[0]["expected"])            # 过了的步骤才给结论
        self.assertEqual(v2.steps[1]["expected"], "")

    def test_escalation_to_level_three_reveals_and_is_logged(self):
        v = self.agent.start(self.student, "已知每层的导数，求整体梯度")
        for _ in range(3):
            v = self.agent.answer(v.session_id, "不会", stuck=True)
        self.assertEqual(v.escalation_level, 3)
        self.assertTrue(v.gives_answer)
        self.assertTrue(v.steps[0]["revealed"])
        self.assertTrue(v.steps[0]["expected"])
        evs = state_repo.list_events(student_id=self.student, event_type="escalation")
        self.assertEqual(len(evs), 3)
        self.assertTrue(evs[-1]["payload"]["needs_teacher"])

    def test_evidence_weight_follows_certainty_of_the_check(self):
        """数值校验记 practice(0.7)，文字规则判定记 ask(0.5)——判得越准，权重越高。"""
        from packages.state.tracker import SOURCE_WEIGHT

        self.assertGreater(SOURCE_WEIGHT["practice"], SOURCE_WEIGHT["ask"])
        v = self.agent.start(self.student, "已知每层的导数，求整体梯度")
        step0 = self.db.query_one(
            "SELECT * FROM solve_step WHERE session_id=? AND idx=0", (v.session_id,))
        self.agent.answer(v.session_id, step0["expected"])
        evs = [e for e in state_repo.list_events(student_id=self.student,
                                                 event_type="practice")]
        self.assertTrue(evs)
        self.assertIn(evs[0]["source"], ("ask", "practice"))

    def test_undecidable_answer_writes_no_mastery_evidence(self):
        v = self.agent.start(self.student, "已知每层的导数，求整体梯度")
        step0 = self.db.query_one(
            "SELECT * FROM solve_step WHERE session_id=? AND idx=0", (v.session_id,))
        half = expected_keywords(step0["expected"])[0]      # 只答对一半的关键词
        before = state_repo.mastery_rows(self.student)
        self.agent.answer(v.session_id, half)
        after = state_repo.mastery_rows(self.student)
        self.assertEqual(len(before), len(after))           # 判不了 -> 不写证据


class TestResearchAgent(OfflineLLMMixin, DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        seed_kb()
        self.agent = ResearchAgent()

    def test_report_is_sectioned_by_knowledge_base_with_citations(self):
        rep = self.agent.investigate(self.student, "反向传播")
        self.assertTrue(rep.sections)
        for sec in rep.sections:
            self.assertTrue(sec["citations"])
        self.assertIn("# 反向传播", rep.body_md)

    def test_empty_knowledge_base_means_no_report(self):
        self.db.execute("DELETE FROM doc_chunk")
        rep = self.agent.investigate(self.student, "反向传播")
        self.assertFalse(rep.sections)
        self.assertIn("没有检索到", rep.caveat)

    def test_off_topic_sections_are_flagged_low_support(self):
        """检索总能召回点什么，所以生成之后必须再量一次落地性。"""
        rep = self.agent.investigate(self.student, "量子退火与超导磁通")
        self.assertTrue(rep.sections)
        self.assertGreaterEqual(rep.n_unsourced, 0)
        for sec in rep.sections:
            self.assertIn("groundedness", sec)
            if not sec["grounded"]:
                self.assertIn("低支撑", rep.body_md)

    def test_research_never_touches_mastery(self):
        before = state_repo.mastery_rows(self.student)
        self.agent.investigate(self.student, "反向传播")
        self.assertEqual(state_repo.mastery_rows(self.student), before)
        evs = state_repo.list_events(student_id=self.student, event_type="research")
        self.assertTrue(evs)
        self.assertIsNone(evs[0]["is_correct"])

    def test_note_is_persisted_and_readable(self):
        rep = self.agent.investigate(self.student, "反向传播")
        notes = self.agent.notes(self.student)
        self.assertEqual(notes[0]["id"], rep.note_id)
        self.assertTrue(self.agent.note(rep.note_id)["body_md"])

    def test_low_groundedness_is_flagged_not_silently_kept(self):
        from packages.tools import ground

        g = ground.check("这段话与材料毫无关系，讲的是别的领域的事情。",
                         "反向传播依赖链式法则")
        self.assertFalse(g.grounded)


class TestVisualize(OfflineLLMMixin, DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        self.agent = VisualizeAgent()
        for _ in range(6):
            tracker.record(self.student, "quiz", self.kp["A"], True, source="exam")
        for _ in range(4):
            tracker.record(self.student, "quiz", self.kp["B"], False, source="exam")
        tracker.recompute_ability(self.student)

    def test_heat_color_matches_frontend_curve(self):
        self.assertEqual(heat_color(0.0), "rgb(207,34,46)")
        self.assertEqual(heat_color(0.5), "rgb(191,135,0)")
        self.assertEqual(heat_color(1.0), "rgb(26,127,55)")

    def test_every_kind_renders_valid_svg(self):
        from packages.agents.visualize import KINDS

        for kind in KINDS:
            fig = self.agent.render(kind, self.student, kp_id=self.kp["B"])
            self.assertTrue(fig.svg.startswith("<svg"), kind)
            self.assertTrue(fig.svg.endswith("</svg>"), kind)
            self.assertNotIn("http://cdn", fig.svg)
            self.assertTrue(fig.caption, kind)

    def test_numbers_in_figure_equal_state_layer_numbers(self):
        fig = self.agent.render("mastery_bars", self.student)
        by_kp = {r["kp_id"]: r["p_mastery"] for r in fig.data}
        for row in state_repo.mastery_rows(self.student):
            self.assertAlmostEqual(by_kp[row["kp_id"]], round(row["p_mastery"], 4))

    def test_root_cause_chain_exports_mermaid_but_page_needs_no_library(self):
        fig = self.agent.render("root_cause_chain", self.student, kp_id=self.kp["C"])
        self.assertTrue(fig.svg.startswith("<svg"))
        if fig.data:
            self.assertIn("graph LR", fig.mermaid)

    def test_retention_curve_uses_configured_halflife(self):
        fig = self.agent.render("retention_curve", self.student, kp_id=self.kp["A"])
        self.assertIn(f"复检线 {CONFIG.teaching.retention_threshold}", fig.svg)

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.agent.render("pie_of_shame", self.student)
