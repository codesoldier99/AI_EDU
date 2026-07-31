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
