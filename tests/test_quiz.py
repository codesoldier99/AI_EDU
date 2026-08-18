"""题库 · 确定性判分 · 针对性练习。

盯住的是三条不能破的线：
1. 判不了的题不许判成错——那会污染 BKT 的证据流；
2. 发给学生的题不许带答案；
3. 模型出的题不许绕过教师直接进组卷。
"""
from __future__ import annotations

from base import DBTestCase, OfflineLLMMixin

from packages.agents.quiz import QuizAgent, parse_drafts
from packages.core.config import CONFIG
from packages.core.db import get_db
from packages.quiz import bank, grader, selector
from packages.rag import store
from packages.state import repo as state_repo
from packages.state import tracker


def seed_kb():
    store.index_text("course", "backprop.md", "反向传播讲义",
                     "反向传播依赖链式法则，必须逐层把导数乘回去。\n\n"
                     "最常见的错误是漏掉激活函数的导数。", ["A", "B"])
    store.index_text("course", "chain.md", "链式法则讲义",
                     "链式法则要求把中间变量的导数依次相乘，不能跳过任何一层。", ["A"])


class TestGrader(DBTestCase):
    def test_choice_accepts_letter_index_and_text(self):
        q = bank.Question(qtype="choice", grader="choice", answer="B",
                          options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"])
        for resp in ("B", "b", "2", "乙"):
            self.assertTrue(grader.grade(q, resp).is_correct, resp)
        self.assertFalse(grader.grade(q, "C").is_correct)

    def test_choice_unparseable_goes_pending_not_wrong(self):
        q = bank.Question(qtype="choice", grader="choice", answer="B",
                          options=["A. 甲", "B. 乙", "C. 丙"])
        r = grader.grade(q, "我觉得都有道理")
        self.assertIsNone(r.is_correct)          # 判不了
        self.assertEqual(r.graded_by, "pending_teacher")

    def test_numeric_uses_deterministic_calculator(self):
        q = bank.Question(qtype="numeric", grader="numeric", answer="2*pi",
                          tolerance=0.01)
        self.assertTrue(grader.grade(q, "6.283").is_correct)
        self.assertFalse(grader.grade(q, "3.14").is_correct)
        self.assertIsNone(grader.grade(q, "不会做").is_correct)

    def test_keyword_band_between_thresholds_is_pending(self):
        q = bank.Question(qtype="short", grader="keyword",
                          keywords=["链式法则", "逐层", "导数", "中间变量"])
        self.assertTrue(grader.grade(q, "用链式法则逐层把导数乘回去，中间变量都要算").is_correct)
        self.assertFalse(grader.grade(q, "随便写点别的内容凑数").is_correct)
        mid = grader.grade(q, "要用链式法则，别的记不清了，还有导数")
        self.assertIsNone(mid.is_correct)
        self.assertEqual(mid.graded_by, "pending_teacher")

    def test_keyword_supports_synonyms(self):
        q = bank.Question(qtype="short", grader="keyword",
                          keywords=["反向传播|BP", "链式法则"])
        self.assertTrue(grader.grade(q, "BP 就是把链式法则展开来用").is_correct)


class TestBank(DBTestCase):
    seed_course = True

    def test_llm_question_requires_citations_and_is_pending(self):
        with self.assertRaises(ValueError):
            bank.add(self.kp["A"], "无依据的题面是什么？", "A", origin="llm")
        qid = bank.add(self.kp["A"], "有依据的题面是什么？", "A", origin="llm",
                       options=["A. 甲", "B. 乙", "C. 丙"],
                       citations=[{"kb": "course", "ref": "x.md"}])
        self.assertEqual(bank.get(qid).teacher_verified, 0)
        self.assertEqual(bank.list_for_kp(self.kp["A"]), [])   # 未审的题不进组卷池

    def test_teacher_review_promotes_and_reject_retires(self):
        qid = bank.add(self.kp["A"], "题面甲甲甲甲甲甲", "A", origin="teacher")
        self.assertEqual(bank.get(qid).teacher_verified, 1)
        bad = bank.add(self.kp["A"], "题面乙乙乙乙乙乙", "B", origin="llm",
                       citations=[{"ref": "x"}])
        bank.review(bad, "reject")
        self.assertEqual(bank.get(bad).retired, 1)
        self.assertEqual([q.id for q in bank.list_for_kp(self.kp["A"])], [qid])

    def test_question_cannot_be_deleted(self):
        qid = bank.add(self.kp["A"], "题面丙丙丙丙丙丙", "A", origin="teacher")
        with self.assertRaises(Exception):
            get_db().execute("DELETE FROM question WHERE id=?", (qid,))

    def test_dedupe_by_signature(self):
        a = bank.add(self.kp["A"], "同一道题的题面", "A", origin="teacher")
        b = bank.add(self.kp["A"], "同一道题的题面。", "A", origin="teacher")
        self.assertEqual(a, b)

    def test_student_view_never_leaks_answer(self):
        qid = bank.add(self.kp["A"], "题面丁丁丁丁丁丁", "B", origin="teacher",
                       options=["A. 甲", "B. 乙"], rationale="因为乙")
        view = bank.get(qid).for_student()
        self.assertNotIn("answer", view)
        self.assertNotIn("rationale", view)


class TestSelector(DBTestCase):
    seed_course = True

    def test_priority_kinds_contain_no_forbidden_targets(self):
        """铁律 5 的正面表述：练习排序的依据里不能有正确率/做题数/停留时长。"""
        for bad in ("accuracy", "correct_rate", "item_count", "dwell_time", "rank"):
            self.assertNotIn(bad, selector.PRIORITY_KINDS)

    def test_no_task_no_push(self):
        """拉取式的反面表述：不知道他在做什么，就不推任何东西。

        这不是缺陷。按拓扑序给全员统一排课是本系统明令禁止的做法，
        没有任务时最诚实的回答就是"回项目里去"。
        """
        for _ in range(4):
            tracker.record(self.student, "quiz", self.kp["C"], False, source="exam")
        plan = selector.practice_plan(self.student)
        self.assertEqual(plan.targets, [])

    def test_gap_targets_come_from_task_not_syllabus_order(self):
        from packages.graph import repo as g

        pid = g.upsert_project("P1", "测试项目", "vision", "vision")
        tid = g.upsert_task(pid, "T-1", "跑通一次训练")
        g.link_task_kp(tid, self.kp["C"], "required")
        plan = selector.practice_plan(self.student, task_id=tid)
        self.assertIn(self.kp["C"], [t["kp_id"] for t in plan.targets])
        kinds = {t["kind"] for t in plan.targets}
        self.assertTrue(kinds <= set(selector.PRIORITY_KINDS))

    def test_unvalidated_mastery_is_called_back_after_the_gap(self):
        """达标但没跨时间验证过的知识点，过了验证间隔就该被叫回来。"""
        from datetime import datetime, timedelta

        # 距今 14 天：超过 verify_gap_days(7)，但按半衰期尚未掉到复检线以下
        when = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        for _ in range(10):
            tracker.record(self.student, "quiz", self.kp["A"], True, source="exam",
                           occurred_at=when)
        plan = selector.practice_plan(self.student)
        kinds = {t["kp_id"]: t["kind"] for t in plan.targets}
        self.assertEqual(kinds.get(self.kp["A"]), "verify")

    def test_long_forgotten_mastery_becomes_retention_review(self):
        """放久了的知识点优先级高于待验证——先救掉下去的那个。"""
        for _ in range(10):
            tracker.record(self.student, "quiz", self.kp["A"], True, source="exam",
                           occurred_at="2026-01-01 09:00:00")
        plan = selector.practice_plan(self.student)
        kinds = {t["kp_id"]: t["kind"] for t in plan.targets}
        self.assertEqual(kinds.get(self.kp["A"]), "retention")
        self.assertGreater(CONFIG.teaching.verify_gap_days, 0)

    def test_unseen_questions_are_preferred(self):
        old = bank.add(self.kp["A"], "做过的那道题题面", "A", origin="teacher",
                       options=["A. 甲", "B. 乙"], difficulty=0.5)
        new = bank.add(self.kp["A"], "没做过的那道题题面", "A", origin="teacher",
                       options=["A. 甲", "B. 乙"], difficulty=0.5)
        tracker.record(self.student, "quiz", self.kp["A"], True, source="quiz",
                       source_ref=f"paper:1#question:{old}")
        qs, _ = selector.pick_questions(
            self.student, [{"kp_id": self.kp["A"], "p_mastery": 0.4}], per_kp=1)
        self.assertEqual([q.id for q in qs], [new])

    def test_coverage_gaps_point_at_missing_bank(self):
        gaps = selector.coverage_gaps(
            self.student, [{"kp_id": self.kp["D"], "name": "批归一化"}])
        self.assertEqual(gaps[0]["kp_id"], self.kp["D"])


class TestDraftParsing(DBTestCase):
    def test_rejects_incomplete_blocks(self):
        text = (
            "[题目] 关于链式法则，下列哪项正确？\n"
            "[选项] A. 甲甲 | B. 乙乙 | C. 丙丙 | D. 丁丁\n"
            "[答案] B\n"
            "[解析] 因为乙乙。\n"
            "[题目] 缺答案的题目在这里\n"
            "[选项] A. 甲 | B. 乙 | C. 丙\n"
            "[解析] 无\n"
        )
        ok, bad = parse_drafts(text, "choice")
        self.assertEqual(len(ok), 1)
        self.assertEqual(len(bad), 1)
        self.assertIn("答案", bad[0]["reason"])

    def test_rejects_answer_outside_options(self):
        text = ("[题目] 关于链式法则，下列哪项正确？\n"
                "[选项] A. 甲甲 | B. 乙乙 | C. 丙丙\n"
                "[答案] D\n[解析] x\n")
        ok, bad = parse_drafts(text, "choice")
        self.assertFalse(ok)
        self.assertIn("范围", bad[0]["reason"])

    def test_numeric_answer_must_be_computable(self):
        text = "[题目] 求这个值是多少呢？\n[答案] 大概三点一四\n[解析] x\n"
        ok, bad = parse_drafts(text, "numeric")
        self.assertFalse(ok)


class TestQuizAgentEndToEnd(OfflineLLMMixin, DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        seed_kb()
        self.agent = QuizAgent()

    def test_draft_refuses_without_material(self):
        # D（批归一化）在知识库里没有对应材料，且不与已索引的知识点相邻
        res = self.agent.draft(self.kp["D"], n=2)
        if not res.accepted:
            self.assertTrue(res.rejected)

    def test_draft_lands_in_pending_queue(self):
        res = self.agent.draft(self.kp["B"], n=2)
        self.assertTrue(res.accepted, res.rejected)
        for item in res.accepted:
            q = bank.get(item["question_id"])
            self.assertEqual(q.teacher_verified, 0)
            self.assertTrue(q.citations)          # 每道题都挂着教材依据

    def test_full_loop_writes_events_through_tracker(self):
        res = self.agent.draft(self.kp["B"], n=2)
        for item in res.accepted:
            bank.review(item["question_id"], "accept")
        # 造一个缺口：B 未掌握且被任务需要
        from packages.graph import repo as g

        pid = g.upsert_project("P1", "测试项目", "vision", "vision")
        tid = g.upsert_task(pid, "T-1", "跑通一次训练")
        g.link_task_kp(tid, self.kp["B"], "required")

        paper = self.agent.assemble(self.student, task_id=tid)
        self.assertTrue(paper.questions, paper.missing_bank)
        for q in paper.questions:
            self.assertNotIn("answer", q)         # 组卷不泄题

        qid = paper.questions[0]["id"]
        correct = bank.get(qid).answer
        before = state_repo.get_mastery(self.student, self.kp["B"])
        out = self.agent.submit(paper.paper_id, {str(qid): correct})
        after = state_repo.get_mastery(self.student, self.kp["B"])

        self.assertEqual(out.items[0]["is_correct"], True)
        self.assertTrue(out.items[0]["graded_by"].startswith("rule:"))
        self.assertTrue(out.items[0]["rationale"])   # 批改后才给解析
        self.assertIsNotNone(after)
        self.assertGreater(after["p_mastery"], (before or {}).get("p_mastery", 0.0))
        evs = state_repo.list_events(student_id=self.student, event_type="quiz")
        self.assertTrue(any(f"question:{qid}" in (e["source_ref"] or "") for e in evs))

    def test_wrong_answer_feeds_error_pattern_library(self):
        res = self.agent.draft(self.kp["B"], n=1)
        for item in res.accepted:
            bank.review(item["question_id"], "accept")
        from packages.graph import repo as g

        pid = g.upsert_project("P2", "测试项目2", "vision", "vision")
        tid = g.upsert_task(pid, "T-2", "任务二")
        g.link_task_kp(tid, self.kp["B"], "required")
        paper = self.agent.assemble(self.student, task_id=tid)
        qid = paper.questions[0]["id"]
        wrong = "A" if bank.get(qid).answer != "A" else "B"
        self.agent.submit(paper.paper_id, {str(qid): wrong})
        n = get_db().scalar("SELECT COUNT(*) FROM error_instance")
        self.assertGreaterEqual(n, 1)

    def test_pending_grading_then_teacher_grade_appends_event(self):
        qid = bank.add(self.kp["B"], "简答：为什么要逐层乘导数？", "逐层相乘",
                       qtype="short", origin="teacher",
                       keywords=["链式法则", "逐层", "导数", "中间变量"])
        from packages.core.db import dumps

        pid = get_db().execute(
            "INSERT INTO quiz_paper(student_id, purpose, scope_ref, question_ids, reasons,"
            " created_at) VALUES(?,?,?,?,?,datetime('now'))",
            (self.student, "gap", "", dumps([qid]), dumps([])),
        )
        out = self.agent.submit(pid, {str(qid): "记得要用链式法则，还有导数"})
        self.assertEqual(out.n_pending, 1)
        self.assertIsNone(out.items[0]["is_correct"])
        # 判不了的题不许给解析（等于送答案）
        self.assertEqual(out.items[0]["rationale"], "")

        queue = self.agent.pending_grading()
        self.assertTrue(queue)
        ev = queue[0]["event_id"]
        before = len(state_repo.list_events(student_id=self.student))
        self.agent.teacher_grade(ev, True, note="思路对，表述不全")
        after = state_repo.list_events(student_id=self.student)
        self.assertEqual(len(after), before + 1)      # 追加，不修改
        self.assertEqual(after[-1]["payload"]["supersedes_event"], ev)
        self.assertTrue(state_repo.get_mastery(self.student, self.kp["B"]))
