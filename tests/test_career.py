"""求职智能体：岗位需求三层图谱与简历生成。"""
from __future__ import annotations

import json
import unittest

from base import DBTestCase, OfflineLLMMixin

from apps.api.microapi import Request
from apps.api.server import create_app
from packages.agents.career import CareerAgent
from packages.graph import repo as g
from packages.state import repo as s
from packages.state import tracker


def _req(method: str, path: str, token: str = "", body: dict | None = None,
        query: dict | None = None) -> Request:
    return Request(
        method=method, path=path,
        query={k: [v] for k, v in (query or {}).items()},
        headers={"x-auth-token": token} if token else {},
        body=json.dumps(body).encode() if body else b"",
    )


class TestCareerRepo(DBTestCase):
    seed_course = True

    def _build_job(self) -> int:
        jid = g.upsert_job("JOB-T", "测试算法工程师", "测试企业", "岗位说明")
        top = g.upsert_requirement(jid, "REQ-ALGO", "算法能力", weight=0.7, seq=0)
        g.upsert_requirement(jid, "REQ-ALGO-A", "链式求导", parent_code="REQ-ALGO",
                             weight=0.5, seq=0)
        g.upsert_requirement(jid, "REQ-ALGO-B", "反向传播实现", parent_code="REQ-ALGO",
                             weight=0.5, seq=1)
        g.upsert_requirement(jid, "REQ-ENG", "工程协作能力", weight=0.3,
                             signal_classes=["collaboration"], seq=1)
        leaf_a = g.get_requirement(jid, "REQ-ALGO-A")
        leaf_b = g.get_requirement(jid, "REQ-ALGO-B")
        g.link_requirement_kp(leaf_a.id, self.kp["A"], 1.0)
        g.link_requirement_kp(leaf_b.id, self.kp["B"], 1.0)
        return jid

    def test_upsert_and_list_jobs_is_idempotent(self):
        jid1 = self._build_job()
        jid2 = g.upsert_job("JOB-T", "测试算法工程师（改名）", "测试企业", "岗位说明")
        self.assertEqual(jid1, jid2)
        jobs = g.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].name, "测试算法工程师（改名）")

    def test_requirement_tree_parent_child(self):
        jid = self._build_job()
        reqs = {r.code: r for r in g.list_requirements(jid)}
        self.assertIsNone(reqs["REQ-ALGO"].parent_id)
        self.assertEqual(reqs["REQ-ALGO-A"].parent_id, reqs["REQ-ALGO"].id)
        self.assertEqual(reqs["REQ-ENG"].signal_classes, ["collaboration"])

    def test_requirement_kp_link_roundtrip(self):
        jid = self._build_job()
        leaf = g.get_requirement(jid, "REQ-ALGO-A")
        kps = g.requirement_kps(leaf.id)
        self.assertEqual([k["kp_id"] for k in kps], [self.kp["A"]])


class TestCareerFitReport(DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        jid = g.upsert_job("JOB-T", "测试算法工程师", "测试企业", "岗位说明")
        g.upsert_requirement(jid, "REQ-ALGO", "算法能力", weight=0.7, seq=0)
        g.upsert_requirement(jid, "REQ-ALGO-A", "链式求导", parent_code="REQ-ALGO",
                             weight=0.5, seq=0)
        g.upsert_requirement(jid, "REQ-ALGO-B", "反向传播实现", parent_code="REQ-ALGO",
                             weight=0.5, seq=1)
        g.upsert_requirement(jid, "REQ-ENG", "工程协作能力", weight=0.3,
                             signal_classes=["collaboration"], seq=1)
        leaf_a = g.get_requirement(jid, "REQ-ALGO-A")
        leaf_b = g.get_requirement(jid, "REQ-ALGO-B")
        g.link_requirement_kp(leaf_a.id, self.kp["A"], 1.0)
        g.link_requirement_kp(leaf_b.id, self.kp["B"], 1.0)
        self.job_id = jid
        self.agent = CareerAgent()

    def test_unknown_job_raises(self):
        with self.assertRaises(KeyError):
            self.agent.fit_report(self.student, "NOPE")

    def test_no_evidence_gives_zero_fit_and_gap(self):
        rep = self.agent.fit_report(self.student, "JOB-T")
        self.assertEqual(rep.overall_fit, 0.0)
        self.assertTrue(rep.gaps, "尚无作答证据时应当全部进差距清单")
        self.assertFalse(rep.strengths)
        self.assertEqual(rep.caveat, "数据不足，仅供参考")

    def test_mastered_kp_becomes_strength_and_lifts_fit(self):
        for _ in range(6):
            tracker.record(self.student, "quiz", self.kp["A"], True, source="quiz")
        rep = self.agent.fit_report(self.student, "JOB-T")
        self.assertGreater(rep.overall_fit, 0.0)
        strength_codes = {x["kp_code"] for x in rep.strengths}
        self.assertIn("A", strength_codes)
        # REQ-ALGO-B（知识点 B）仍无证据，应仍在差距清单里
        gap_codes = {x["kp_code"] for x in rep.gaps}
        self.assertIn("B", gap_codes)

    def test_build_universe_three_layers(self):
        data = self.agent.build_universe("JOB-T", self.student)
        layers = {n["layer"] for n in data["nodes"]}
        self.assertEqual(layers, {0, 1, 2, 3})
        ids = {n["id"] for n in data["nodes"]}
        self.assertIn("job", ids)
        self.assertIn(f"kp:{self.kp['A']}", ids)
        self.assertEqual(data["nodes"][0]["id"], "job")

    def test_build_universe_unknown_job_returns_none(self):
        self.assertIsNone(self.agent.build_universe("NOPE"))


class TestCareerResume(OfflineLLMMixin, DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        jid = g.upsert_job("JOB-T", "测试算法工程师", "测试企业", "岗位说明")
        g.upsert_requirement(jid, "REQ-ALGO", "算法能力", weight=1.0, seq=0)
        leaf = g.get_requirement(jid, "REQ-ALGO")
        g.link_requirement_kp(leaf.id, self.kp["A"], 1.0)
        for _ in range(6):
            tracker.record(self.student, "quiz", self.kp["A"], True, source="quiz")

    def test_resume_never_invents_facts_and_reports_caveat(self):
        out = CareerAgent().resume(self.student, "JOB-T")
        self.assertIn("综合匹配度", out.narrative)
        self.assertTrue(out.degraded)  # 离线表达器
        self.assertIsInstance(out.plan, dict)
        self.assertIn("gaps", out.plan)


class TestCareerAPI(OfflineLLMMixin, DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        jid = g.upsert_job("JOB-T", "测试算法工程师", "测试企业", "岗位说明")
        g.upsert_requirement(jid, "REQ-ALGO", "算法能力", weight=1.0, seq=0)
        leaf = g.get_requirement(jid, "REQ-ALGO")
        g.link_requirement_kp(leaf.id, self.kp["A"], 1.0)
        tracker.record(self.student, "quiz", self.kp["A"], True, source="quiz")
        self.other_student = s.upsert_student("S002", "他班同学", "2026级", "实验班B")
        self.db.execute(
            "INSERT INTO teacher(code, name, klasses) VALUES('T1','甲老师','[\"实验班A\"]')")
        self.app = create_app()

    def test_jobs_listing_is_public_configuration(self):
        r = self.app.dispatch(_req("GET", "/api/jobs", token="student:S001"))
        codes = {j["code"] for j in r["jobs"]}
        self.assertIn("JOB-T", codes)

    def test_student_can_only_view_own_fit(self):
        r = self.app.dispatch(_req("GET", "/api/career/JOB-T/fit", token="student:S001"))
        self.assertEqual(r["student_id"], self.student)
        from apps.api.microapi import HTTPError
        with self.assertRaises(HTTPError):
            self.app.dispatch(_req(
                "GET", "/api/career/JOB-T/fit", token="student:S001",
                query={"student_id": str(self.other_student)}))

    def test_teacher_cannot_view_other_class_student(self):
        from apps.api.microapi import HTTPError
        with self.assertRaises(HTTPError):
            self.app.dispatch(_req(
                "GET", "/api/career/JOB-T/fit", token="teacher:T1",
                query={"student_id": str(self.other_student)}))

    def test_resume_endpoint_returns_narrative(self):
        r = self.app.dispatch(_req(
            "POST", "/api/career/JOB-T/resume", token="student:S001",
            body={"student_id": self.student}))
        self.assertIn("narrative", r)
        self.assertIn("综合匹配度", r["narrative"])


if __name__ == "__main__":
    unittest.main()
