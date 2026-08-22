"""API 层：鉴权、数据权限边界、confidence 强制返回。"""
from __future__ import annotations

import json

from base import DBTestCase

from apps.api.auth import middleware
from apps.api.microapi import HTTPError, Request
from apps.api.server import create_app
from packages.graph import repo as g
from packages.state import repo as s
from packages.state import tracker


def req(method: str, path: str, token: str = "", body: dict | None = None,
        query: dict | None = None) -> Request:
    return Request(
        method=method, path=path,
        query={k: [v] for k, v in (query or {}).items()},
        headers={"x-auth-token": token} if token else {},
        body=json.dumps(body).encode() if body else b"",
    )


class TestAPI(DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        self.db.execute(
            "INSERT INTO teacher(code, name, klasses) VALUES('T1','甲老师','[\"实验班A\"]')")
        self.db.execute(
            "INSERT INTO teacher(code, name, klasses) VALUES('T2','乙老师','[\"实验班B\"]')")
        self.other = s.upsert_student("S002", "他班同学", "2026级", "实验班B")
        tracker.record(self.student, "quiz", self.kp["A"], True, source="quiz")
        self.app = create_app()

    def test_health_is_public(self):
        r = self.app.dispatch(req("GET", "/api/health"))
        self.assertTrue(r["ok"])

    def test_missing_token_rejected(self):
        with self.assertRaises(HTTPError):
            self.app.dispatch(req("GET", "/api/courses"))

    def test_student_cannot_read_others(self):
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(
                req("GET", f"/api/students/{self.other}/mastery", "student:S001"))
        self.assertEqual(cm.exception.status, 403)

    def test_teacher_cannot_cross_class(self):
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(
                req("GET", f"/api/students/{self.other}/mastery", "teacher:T1"))
        self.assertEqual(cm.exception.status, 403)

    def test_teacher_can_read_own_class(self):
        r = self.app.dispatch(
            req("GET", f"/api/students/{self.student}/mastery", "teacher:T1"))
        self.assertTrue(r["items"])

    def test_student_route_denied_for_students(self):
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(req("GET", "/api/students", "student:S001"))
        self.assertEqual(cm.exception.status, 403)

    def test_diagnosis_returns_confidence(self):
        r = self.app.dispatch(req("GET", "/api/diagnosis/class/T", "teacher:T1",
                                  query={"klass": "实验班A"}))
        self.assertIn("confidence", r)
        self.assertIn("evidence_count", r)

    def test_events_route_writes_through_tracker(self):
        r = self.app.dispatch(req("POST", "/api/events", "student:S001", body={
            "items": [{"kp_id": self.kp["B"], "is_correct": True, "event_type": "quiz",
                       "source": "quiz"}]}))
        self.assertTrue(r["items"][0]["updated"])
        ev = s.list_events(student_id=self.student, kp_id=self.kp["B"])
        self.assertTrue(ev)

    def test_report_open_is_logged(self):
        self.app.dispatch(req("GET", "/api/diagnosis/class/T", "teacher:T1"))
        n = self.db.scalar("SELECT COUNT(*) FROM report_open_log")
        self.assertGreaterEqual(n, 1)

    def test_whoami(self):
        r = self.app.dispatch(req("GET", "/api/whoami", "student:S001"))
        self.assertEqual(r["role"], "student")
        self.assertEqual(r["student_id"], self.student)


class TestStudyRoutes(DBTestCase):
    """学习工作台的路由与权限。重点盯"组卷不泄题""教师专属接口不对学生开放"。"""

    seed_course = True

    def setUp(self):
        super().setUp()
        self.db.execute(
            "INSERT INTO teacher(code, name, klasses) VALUES('T1','甲老师','[\"实验班A\"]')")
        self.app = create_app()
        from packages.quiz import bank

        self.qid = bank.add(self.kp["A"], "关于链式法则，下列哪项正确？", "B",
                            options=["A. 甲甲", "B. 乙乙", "C. 丙丙"],
                            rationale="因为乙乙", origin="teacher")
        for _ in range(4):
            tracker.record(self.student, "quiz", self.kp["A"], False, source="exam")
        # 拉取式：没有在做的任务就不该推任何东西，所以这里先给他派一个任务
        pid = g.upsert_project("P1", "测试项目", "vision", "vision")
        tid = g.upsert_task(pid, "T-1", "跑通一次训练")
        g.link_task_kp(tid, self.kp["A"], "required")
        self.db.execute(
            "INSERT INTO task_assignment(task_id, student_id, status, updated_at)"
            " VALUES(?,?, 'doing', datetime('now'))", (tid, self.student))

    def test_practice_plan_returns_confidence_and_reasons(self):
        r = self.app.dispatch(req("GET", "/api/practice-plan", "student:S001"))
        self.assertIn("confidence", r)
        self.assertIn("evidence_count", r)
        self.assertIn("missing_bank", r)

    def test_assembled_paper_never_carries_answers(self):
        r = self.app.dispatch(req("POST", "/api/quiz/assemble", "student:S001", body={}))
        for q in r["questions"]:
            self.assertNotIn("answer", q)
            self.assertNotIn("rationale", q)

    def test_submit_writes_events_and_gives_rationale_after_grading(self):
        paper = self.app.dispatch(req("POST", "/api/quiz/assemble", "student:S001", body={}))
        self.assertTrue(paper["questions"], paper["missing_bank"])
        qid = paper["questions"][0]["id"]
        r = self.app.dispatch(req("POST", "/api/quiz/submit", "student:S001",
                                  body={"paper_id": paper["paper_id"],
                                        "answers": {str(qid): "B"}}))
        self.assertTrue(r["items"])
        self.assertTrue(r["items"][0]["rationale"])

    def test_quiz_draft_is_teacher_only(self):
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(req("POST", "/api/quiz/draft", "student:S001",
                                  body={"kp_id": self.kp["A"]}))
        self.assertEqual(cm.exception.status, 403)

    def test_review_queue_is_teacher_only(self):
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(req("GET", "/api/quiz/review-queue", "student:S001"))
        self.assertEqual(cm.exception.status, 403)

    def test_figure_returns_svg_without_external_assets(self):
        r = self.app.dispatch(req("GET", "/api/figure", "student:S001",
                                  query={"kind": "mastery_bars"}))
        self.assertTrue(r["svg"].startswith("<svg"))
        for bad in ("http://", "https://cdn", "<script"):
            self.assertNotIn(bad, r["svg"].replace(
                'xmlns="http://www.w3.org/2000/svg"', ""))

    def test_unknown_figure_kind_is_400(self):
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(req("GET", "/api/figure", "student:S001",
                                  query={"kind": "leaderboard"}))
        self.assertEqual(cm.exception.status, 400)

    def test_skills_endpoint_is_public(self):
        r = self.app.dispatch(req("GET", "/api/skills"))
        self.assertIn("items", r)
        self.assertEqual(r["broken"], [])

    def test_solve_session_of_another_student_is_denied(self):
        other = s.upsert_student("S002", "他班同学", "2026级", "实验班B")
        sid = self.db.execute(
            "INSERT INTO solve_session(student_id, problem, n_steps, created_at, updated_at)"
            " VALUES(?,?,?,datetime('now'),datetime('now'))", (other, "别人的题", 0))
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(req("GET", f"/api/solve/{sid}", "student:S001"))
        self.assertEqual(cm.exception.status, 403)

class TestKPMatchRoutes(DBTestCase):
    """映射审核路由：裁决权只在教师手里。"""

    seed_course = True

    def setUp(self):
        super().setUp()
        self.db.execute(
            "INSERT INTO teacher(code, name, klasses) VALUES('T1','甲老师','[]')")
        pid = g.upsert_project("P1", "测试项目", "test", "none")
        g.upsert_task(pid, "T1", "反向传播与梯度消失诊断", None, 0, 0)
        self.app = create_app()
        from packages.agents.kpmatch import KPMatchAgent
        KPMatchAgent().propose_project("P1", with_rationale=False)

    def test_queue_requires_teacher(self):
        with self.assertRaises(HTTPError):
            self.app.dispatch(req("GET", "/api/kpmatch/queue", token="student:S001"))

    def test_queue_returns_parsed_evidence(self):
        r = self.app.dispatch(req("GET", "/api/kpmatch/queue", token="teacher:T1",
                                  query={"project": "P1"}))
        self.assertTrue(r["items"])
        self.assertIsInstance(r["items"][0]["evidence"], list,
                              "证据应已解析成数组，前端不该再解一次 JSON")

    def test_accept_signs_as_teacher_and_is_idempotent(self):
        cid = g.list_candidates("P1", "pending")[0]["id"]
        r = self.app.dispatch(req("POST", f"/api/kpmatch/decide/{cid}", token="teacher:T1",
                                  body={"action": "accept"}))
        self.assertEqual(r["status"], "accepted")
        row = self.db.query_one(
            "SELECT annotated_by FROM task_kp_link WHERE task_id=? AND kp_id=?",
            (r["task_id"], r["kp_id"]))
        self.assertEqual(row["annotated_by"], "teacher")
        with self.assertRaises(HTTPError):
            self.app.dispatch(req("POST", f"/api/kpmatch/decide/{cid}", token="teacher:T1",
                                  body={"action": "accept"}))

    def test_bad_action_rejected(self):
        cid = g.list_candidates("P1", "pending")[0]["id"]
        with self.assertRaises(HTTPError):
            self.app.dispatch(req("POST", f"/api/kpmatch/decide/{cid}", token="teacher:T1",
                                  body={"action": "maybe"}))


class TestStaticDirectoryServing(DBTestCase):
    """目录路径必须给出目录里的 index.html。

    在这之前，访问 /teacher/ 会在 read_bytes() 上抛 IsADirectoryError，
    连接被直接掐断，浏览器只看到一片空白——演示现场最难查的那种故障。
    """

    def test_directory_maps_to_its_index(self):
        from packages.core.config import ROOT

        web = ROOT / "apps" / "web"
        self.assertTrue((web / "teacher" / "index.html").exists())
        d = (web / "teacher").resolve()
        self.assertTrue(d.is_dir())
        self.assertTrue((d / "index.html").exists(),
                        "静态目录缺少 index.html，/teacher/ 会退回主界面")
