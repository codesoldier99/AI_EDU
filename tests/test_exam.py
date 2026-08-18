"""在线考试：凭据、计时、判分、切线、以及考生令牌的权限边界。

考试系统出错的代价是重考，所以这里盯的都是"错了就没法补救"的地方：
  1. 卷面不许泄漏答案；
  2. 计时必须服务端说了算；
  3. 空白 = 0 分，但不写 BKT 证据（分不清不会还是没做到）；
  4. 考生令牌绝不能碰到教学系统；
  5. 答案键必须自洽——填标准答案就该拿满分。
"""
from __future__ import annotations

import json
import tempfile
from datetime import timedelta
from pathlib import Path

from base import DBTestCase, ROOT

from apps.api.microapi import HTTPError, Request
from apps.api.server import create_app
from packages.core.db import get_db
from packages.core.timeutil import now, to_str
from packages.exam import paper, scoring, session
from packages.quiz import bank, selector
from packages.state import repo as state_repo

MINI_PAPER = """
exam:
  code: T-EXAM
  title: 测试卷
  duration_min: 60
items:
  - {part: A, type: single, points: 2, kp: A, stem: "锚题：以下哪项正确？",
     options: ["A. 甲甲", "B. 乙乙", "C. 丙丙"], answer: B, rationale: "因为乙乙"}
  - {part: A, type: judge, points: 1, kp: A, stem: "锚题判断：链式法则要逐层相乘。",
     options: ["A. 正确", "B. 错误"], answer: A, rationale: "是的"}
  - {part: B, type: single, points: 2, kp: B, stem: "选拔：以下哪项正确？",
     options: ["A. 甲甲", "B. 乙乙", "C. 丙丙"], answer: C, rationale: "因为丙丙"}
  - {part: B, type: fill, points: 2, kp: B, grader: numeric,
     stem: "填空：2 的 3 次方等于 ______", answer: "2**3",
     rationale: "解析文本甲：二的三次方是八"}
  - {part: B, type: reading, points: 3, kp: B, grader: numeric,
     stem: "阅读：X=[[0,1,2],[3,4,5]]，元素总和是 ______", answer: "15",
     rationale: "解析文本乙：逐个相加得十五"}
  - {part: B, type: program, points: 5, kp: C, stem: "编程：写一个求和函数。",
     answer: "遍历累加", rationale: "评分要点：能跑、边界正确"}
"""


def write_mini(tmp: str) -> str:
    p = Path(tmp) / "mini.yaml"
    p.write_text(MINI_PAPER, encoding="utf-8")
    return str(p)


class ExamBase(DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        self._t = tempfile.TemporaryDirectory()
        self.spec = paper.import_paper(write_mini(self._t.name))
        self.exam = paper.get_exam("T-EXAM")
        paper.publish(self.exam["id"])
        self.tk = session.issue_tickets(self.exam["id"], [self.student])[0]

    def tearDown(self):
        self._t.cleanup()
        super().tearDown()

    def open_session(self):
        s = state_repo.get_student(self.student)
        return session.start("T-EXAM", s["sid"], self.tk["ticket"])


class TestPaper(ExamBase):
    def test_totals_and_parts(self):
        self.assertEqual(self.spec.total_score, 15.0)
        self.assertEqual(self.spec.by_part["A"]["points"], 3.0)
        self.assertEqual(self.spec.by_part["B"]["points"], 12.0)

    def test_anchor_items_never_enter_the_practice_pool(self):
        """A 区题是锚题，两年内要重测。它绝不能出现在日常练习里。"""
        anchors = get_db().query("SELECT * FROM question WHERE origin='anchor'")
        self.assertEqual(len(anchors), 2)
        for a in anchors:
            pool = bank.list_for_kp(a["kp_id"], verified_only=True, limit=50)
            self.assertNotIn(a["id"], [q.id for q in pool])
        qs, _ = selector.pick_questions(
            self.student, [{"kp_id": self.kp["A"], "p_mastery": 0.1}], per_kp=5)
        self.assertNotIn("anchor", [q.origin for q in qs])

    def test_published_paper_is_frozen(self):
        with self.assertRaises(ValueError):
            paper.import_paper(write_mini(self._t.name))


class TestSessionSecurity(ExamBase):
    def test_wrong_ticket_rejected(self):
        s = state_repo.get_student(self.student)
        with self.assertRaises(ValueError):
            session.start("T-EXAM", s["sid"], "WRONG1")

    def test_paper_never_leaks_answer_or_rationale(self):
        r = self.open_session()
        v = session.view(r["token"])
        blob = json.dumps(v.to_dict(), ensure_ascii=False)
        # 解析文本一个字都不能出现
        for row in get_db().query(
                "SELECT rationale FROM question WHERE length(rationale)>=4"):
            self.assertNotIn(row["rationale"], blob)
        # 逐题结构里不能有答案类字段（"answered" 这种计数字段不算，故按键名精确比对）
        for it in v.items:
            for k in ("answer", "rationale", "keywords", "tolerance", "grader"):
                self.assertNotIn(k, it, f"考生视图泄漏了 {k}")

    def test_resume_keeps_original_deadline(self):
        """续接不是重考：断网重连不能把时间续上。"""
        r1 = self.open_session()
        d1 = get_db().query_one("SELECT deadline_at FROM exam_session WHERE id=?",
                                (r1["session_id"],))["deadline_at"]
        r2 = self.open_session()
        self.assertTrue(r2["resumed"])
        d2 = get_db().query_one("SELECT deadline_at FROM exam_session WHERE id=?",
                                (r2["session_id"],))["deadline_at"]
        self.assertEqual(d1, d2)

    def test_deadline_is_server_side_and_auto_submits(self):
        r = self.open_session()
        get_db().execute("UPDATE exam_session SET deadline_at=? WHERE id=?",
                         (to_str(now() - timedelta(seconds=1)), r["session_id"]))
        v = session.view(r["token"])
        self.assertEqual(v.status, "expired")
        self.assertEqual(v.remaining_sec, 0)
        with self.assertRaises(ValueError):
            session.save(r["token"], self.spec.items[0]["question_id"], "B")

    def test_cannot_reenter_after_submit(self):
        r = self.open_session()
        session.submit(r["token"])
        s = state_repo.get_student(self.student)
        with self.assertRaises(ValueError):
            session.start("T-EXAM", s["sid"], self.tk["ticket"])

    def test_frozen_paper_rejects_edit_at_db_level(self):
        r = self.open_session()
        qid = self.spec.items[0]["question_id"]
        session.save(r["token"], qid, "B")
        session.submit(r["token"])
        with self.assertRaises(Exception):
            get_db().execute(
                "UPDATE exam_answer SET response='C' WHERE session_id=? AND question_id=?",
                (r["session_id"], qid))


class TestScoring(ExamBase):
    def answer_all_correct(self):
        r = self.open_session()
        v = session.view(r["token"])
        for it in v.items:
            q = bank.get(it["question_id"])
            if q.grader != "manual":
                session.save(r["token"], q.id, q.answer)
        session.submit(r["token"])
        return r

    def test_answer_key_is_self_consistent(self):
        """填标准答案就该拿到全部自动判分的分数。答案键错了这条会红。"""
        r = self.answer_all_correct()
        g = scoring.grade_session(r["session_id"], write_events=False)
        self.assertEqual(g.total_score, 10.0)      # 15 减去 5 分程序题
        self.assertEqual(g.n_pending, 1)

    def test_blank_scores_zero_but_writes_no_mastery_evidence(self):
        """空白 = 0 分（考试规则），但不写证据——分不清是不会还是没做到。"""
        r = self.open_session()
        session.submit(r["token"])
        g = scoring.grade_session(r["session_id"], write_events=False)
        self.assertEqual(g.total_score, 0.0)
        rows = get_db().query(
            "SELECT graded_by FROM exam_answer WHERE session_id=?", (r["session_id"],))
        self.assertTrue(any(x["graded_by"] == "rule:blank" for x in rows))
        evs = state_repo.list_events(student_id=self.student, event_type="exam")
        self.assertEqual(evs, [])

    def test_graded_answers_flow_into_event_stream(self):
        r = self.answer_all_correct()
        evs = state_repo.list_events(student_id=self.student, event_type="exam")
        self.assertTrue(evs)
        self.assertTrue(all(e["source"] == "exam" for e in evs))
        self.assertTrue(state_repo.get_mastery(self.student, self.kp["A"]))

    def test_manual_question_is_pending_until_teacher_scores(self):
        r = self.answer_all_correct()
        prog = [i for i in self.spec.items
                if bank.get(i["question_id"]).grader == "manual"][0]
        q = scoring.pending_queue(self.exam["id"])
        self.assertEqual(len(q), 1)
        out = scoring.teacher_score(r["session_id"], prog["question_id"], 4.0, "边界没处理")
        self.assertEqual(out["n_pending"], 0)
        self.assertEqual(out["total_score"], 14.0)

    def test_teacher_score_is_clamped_to_points(self):
        r = self.answer_all_correct()
        prog = [i for i in self.spec.items
                if bank.get(i["question_id"]).grader == "manual"][0]
        out = scoring.teacher_score(r["session_id"], prog["question_id"], 99.0)
        self.assertEqual(out["total_score"], 15.0)


class TestRanking(ExamBase):
    def make_taker(self, sid: str, name: str, correct_ids: list[int]):
        st = state_repo.upsert_student(sid, name, "2026级", "专升本")
        tk = session.issue_tickets(self.exam["id"], [st])[0]
        r = session.start("T-EXAM", sid, tk["ticket"])
        for qid in correct_ids:
            q = bank.get(qid)
            session.save(r["token"], qid, q.answer)
        session.submit(r["token"])
        return st

    def test_tiebreak_prefers_anchor_score(self):
        """总分相同时，A 区（概念锚题）得分高者优先。"""
        ids = {i["itype"]: i["question_id"] for i in self.spec.items}
        anchor_single = self.spec.items[0]["question_id"]   # A 区 2 分
        b_single = self.spec.items[2]["question_id"]        # B 区 2 分
        self.make_taker("X001", "甲", [anchor_single])      # 2 分，全在 A 区
        self.make_taker("X002", "乙", [b_single])           # 2 分，全在 B 区
        r = scoring.ranking(self.exam["id"], cutoff_n=1)
        top = r.rows[0]
        self.assertEqual(top["sid"], "X001")
        self.assertEqual(top["decided_by"], "A 部分得分")
        self.assertGreater(r.tie_at_cutoff, 1)
        _ = ids

    def test_warns_when_cutoff_is_a_tie(self):
        b_single = self.spec.items[2]["question_id"]
        self.make_taker("X001", "甲", [b_single])
        self.make_taker("X002", "乙", [b_single])
        r = scoring.ranking(self.exam["id"], cutoff_n=1)
        self.assertTrue(any("并列" in w or "相同" in w for w in r.warnings))

    def test_export_carries_rdd_variables(self):
        self.make_taker("X001", "甲", [self.spec.items[0]["question_id"]])
        rows = scoring.export_rows(self.exam["id"])
        self.assertTrue(rows)
        for k in ("total_score", "centered_score", "assigned_experimental",
                  "score_anchor_A"):
            self.assertIn(k, rows[0])


class TestExamAuthIsolation(ExamBase):
    """考生令牌只能碰考试接口。这条破了，考试中就能查到自己的掌握度。"""

    def setUp(self):
        super().setUp()
        self.db.execute(
            "INSERT INTO teacher(code, name, klasses) VALUES('T1','甲老师','[]')")
        self.app = create_app()
        r = self.open_session()
        self.token = f"exam:{r['token']}"

    def req(self, method, path, token="", body=None, query=None):
        return Request(
            method=method, path=path,
            query={k: [v] for k, v in (query or {}).items()},
            headers={"x-auth-token": token} if token else {},
            body=json.dumps(body).encode() if body else b"")

    def test_examinee_can_read_own_paper(self):
        r = self.app.dispatch(self.req("GET", "/api/exam/paper", self.token))
        self.assertEqual(len(r["items"]), 6)

    def test_examinee_blocked_from_teaching_apis(self):
        for path in ("/api/practice-plan", "/api/whoami", "/api/gap",
                     f"/api/students/{self.student}/mastery"):
            with self.assertRaises(HTTPError, msg=path) as cm:
                self.app.dispatch(self.req("GET", path, self.token))
            self.assertEqual(cm.exception.status, 403, path)

    def test_examinee_blocked_from_teacher_exam_apis(self):
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(
                self.req("GET", f"/api/exam/{self.exam['id']}/ranking", self.token))
        self.assertEqual(cm.exception.status, 403)

    def test_student_token_cannot_take_exam(self):
        s = state_repo.get_student(self.student)
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(
                self.req("GET", "/api/exam/paper", f"student:{s['sid']}"))
        self.assertEqual(cm.exception.status, 403)

    def test_login_is_the_only_public_write(self):
        s = state_repo.get_student(self.student)
        r = self.app.dispatch(self.req("POST", "/api/exam/login", "", body={
            "exam_code": "T-EXAM", "sid": s["sid"], "ticket": self.tk["ticket"]}))
        self.assertTrue(r["token"].startswith("exam:"))

    def test_login_with_bad_ticket_is_403(self):
        s = state_repo.get_student(self.student)
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(self.req("POST", "/api/exam/login", "", body={
                "exam_code": "T-EXAM", "sid": s["sid"], "ticket": "ZZZZZZ"}))
        self.assertEqual(cm.exception.status, 403)


class TestRealSelectionPaper(DBTestCase):
    """真卷体检：结构、分值、答案键自洽。改题之后这条会立刻红。"""

    def setUp(self):
        super().setUp()
        import sys

        sys.path.insert(0, str(ROOT / "scripts"))
        import seed as seed_mod

        seed_mod.seed_course()
        self.spec = paper.import_paper(ROOT / "data" / "seed" / "exam_ml_selection.yaml")

    def test_blueprint_matches_measurement_plan(self):
        self.assertEqual(self.spec.total_score, 100.0)
        self.assertEqual(len(self.spec.items), 50)
        self.assertEqual(self.spec.duration_min, 60)
        self.assertEqual(self.spec.by_part["A"]["points"], 40.0)
        self.assertEqual(self.spec.by_part["B"]["points"], 60.0)
        # 五种题型齐全
        for t in ("single", "judge", "fill", "reading", "program"):
            self.assertIn(t, self.spec.by_type, t)

    def test_manual_graded_points_stay_small(self):
        """程序题总分压在 10 分以内：一道主观题的判分波动不该把分数线洗牌。"""
        self.assertLessEqual(self.spec.by_type["program"]["points"], 10.0)

    def test_answer_key_is_self_consistent(self):
        paper.publish(self.spec.exam_id)
        st = state_repo.upsert_student("9001", "满分考生", "2026级", "专升本")
        tk = session.issue_tickets(self.spec.exam_id, [st])[0]
        r = session.start("ML-SELECT-2026", "9001", tk["ticket"])
        v = session.view(r["token"])
        for it in v.items:
            q = bank.get(it["question_id"])
            if q.grader != "manual":
                session.save(r["token"], q.id, q.answer)
        session.submit(r["token"])
        g = scoring.grade_session(r["session_id"], write_events=False)
        self.assertEqual(g.total_score, 90.0,
                         "有题目即使填标准答案也判不对——检查该题的判分器与答案")
        self.assertEqual(g.n_pending, 2)
