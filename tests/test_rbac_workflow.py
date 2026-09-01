"""RBAC 与统一流程：权限矩阵、会话令牌、对象级数据权限、流程状态机。"""
from __future__ import annotations

import json

from base import DBTestCase

from apps.api.microapi import HTTPError, Request
from apps.api.server import create_app
from packages.auth import accounts, rbac, sessions
from packages.errors import service as errors
from packages.graph import repo as g
from packages.quiz import bank
from packages.state import repo as s
from packages.workflow import service as wf
from packages.workflow.service import WorkflowError


def req(method: str, path: str, token: str = "", body: dict | None = None,
        query: dict | None = None) -> Request:
    return Request(
        method=method, path=path,
        query={k: [v] for k, v in (query or {}).items()},
        headers={"x-auth-token": token} if token else {},
        body=json.dumps(body).encode() if body else b"",
    )


class TestRBAC(DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        self.db.execute(
            "INSERT INTO teacher(code, name, klasses) VALUES('T1','甲老师','[\"实验班A\"]')")
        self.db.execute(
            "INSERT INTO teacher(code, name, klasses) VALUES('T2','乙老师','[\"实验班B\"]')")
        rbac.ensure_matrix()
        accounts.sync_from_legacy()
        # 助教账号：本班可见，无发布考/采纳映射权
        accounts.upsert_account(
            "teacher", "TA1", "助教小陈", "ta", ["实验班A"],
        )
        self.other = s.upsert_student("S002", "他班同学", "2026级", "实验班B")
        accounts.sync_from_legacy()
        self.app = create_app()

    def test_matrix_seeded(self):
        m = rbac.list_matrix()
        self.assertIn("teacher", m["by_role"])
        self.assertIn("quiz.review", m["by_role"]["teacher"])
        self.assertNotIn("admin.seed", m["by_role"]["teacher"])
        self.assertIn("admin.seed", m["by_role"]["admin"])

    def test_legacy_token_still_works(self):
        r = self.app.dispatch(req("GET", "/api/whoami", "teacher:T1"))
        self.assertEqual(r["role"], "teacher")
        self.assertIn("quiz.review", r["permissions"])

    def test_session_token_login_and_revoke(self):
        out = self.app.dispatch(req("POST", "/api/auth/login", body={
            "kind": "teacher", "ident": "T1",
        }))
        self.assertTrue(out["token"].startswith("session:"))
        who = self.app.dispatch(req("GET", "/api/whoami", out["token"]))
        self.assertEqual(who["code"], "T1")
        self.app.dispatch(req("POST", "/api/auth/logout", out["token"]))
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(req("GET", "/api/whoami", out["token"]))
        self.assertEqual(cm.exception.status, 401)

    def test_admin_token(self):
        r = self.app.dispatch(req("GET", "/api/whoami", "admin:A001"))
        self.assertEqual(r["role"], "admin")
        self.assertIn("admin.seed", r["permissions"])

    def test_student_forbidden_from_workflow(self):
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(req("GET", "/api/workflow/queue", "student:S001"))
        self.assertEqual(cm.exception.status, 403)

    def test_ta_cannot_decide_kpmatch(self):
        """助教有 workflow.act，但缺 kpmatch.decide —— 细粒度权限拦住。"""
        pid = g.upsert_project("P1", "测试项目", "data", "demo")
        tid = g.upsert_task(pid, "T1", "任务1")
        g.add_candidate(tid, self.kp["A"], "required", 0.9, 0.8, ["链式"])
        self.app.dispatch(req("POST", "/api/workflow/sync", "teacher:T1"))
        items = self.app.dispatch(
            req("GET", "/api/workflow/queue", "teacher:TA1",
                query={"kind": "kp_mapping", "state": "open"})
        )["items"]
        self.assertTrue(items)
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(req(
                "POST", f"/api/workflow/items/{items[0]['id']}/act",
                "teacher:TA1", body={"action": "approve"},
            ))
        self.assertEqual(cm.exception.status, 403)

    def test_teacher_cross_class_still_403(self):
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(
                req("GET", f"/api/students/{self.other}/mastery", "teacher:T1"))
        self.assertEqual(cm.exception.status, 403)

    def test_admin_sees_all_classes(self):
        r = self.app.dispatch(
            req("GET", f"/api/students/{self.other}/mastery", "admin:A001"))
        self.assertIn("items", r)

    def test_missing_token_is_401(self):
        with self.assertRaises(HTTPError) as cm:
            self.app.dispatch(req("GET", "/api/whoami"))
        self.assertEqual(cm.exception.status, 401)

    def test_demo_endpoint_public(self):
        r = self.app.dispatch(req("GET", "/api/auth/demo"))
        self.assertTrue(r["legacy_tokens"])


class TestWorkflow(DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        self.db.execute(
            "INSERT INTO teacher(code, name, klasses) VALUES('T1','甲老师','[\"实验班A\"]')")
        rbac.ensure_matrix()
        accounts.sync_from_legacy()
        self.app = create_app()

    def test_quiz_draft_approve_via_workflow(self):
        qid = bank.add(
            self.kp["A"], "1+1=?", "2", qtype="choice", options=["1", "2"],
            origin="llm", citations=["kb:x"], teacher_verified=0,
        )
        synced = wf.sync_pending()
        self.assertGreaterEqual(synced["synced"]["quiz_draft"], 1)
        items = wf.list_items("quiz_draft", "pending_review")
        self.assertTrue(items)
        iid = items[0]["id"]
        claimed = wf.claim(iid, "T1")
        self.assertEqual(claimed["state"], "claimed")
        out = wf.act(iid, "approve", "T1")
        self.assertEqual(out["item"]["state"], "approved")
        self.assertEqual(bank.get(qid).teacher_verified, 1)
        ev = wf.list_events(iid)
        self.assertGreaterEqual(len(ev), 3)  # create + claim + approve

    def test_illegal_transition_raises(self):
        qid = bank.add(
            self.kp["A"], "题", "a", qtype="choice", options=["a"],
            origin="llm", citations=["kb:x"], teacher_verified=0,
        )
        wf.sync_pending()
        iid = wf.list_items("quiz_draft", "pending_review")[0]["id"]
        wf.act(iid, "approve", "T1")
        with self.assertRaises(WorkflowError):
            wf.act(iid, "reject", "T1")

    def test_kp_mapping_reject_via_api(self):
        pid = g.upsert_project("P1", "测试项目", "data", "demo")
        tid = g.upsert_task(pid, "T1", "任务1")
        g.add_candidate(tid, self.kp["A"], "required", 0.5, 0.4, ["链式"])
        self.app.dispatch(req("POST", "/api/workflow/sync", "teacher:T1"))
        items = self.app.dispatch(req(
            "GET", "/api/workflow/queue", "teacher:T1",
            query={"kind": "kp_mapping", "state": "open"},
        ))["items"]
        self.assertEqual(len(items), 1)
        r = self.app.dispatch(req(
            "POST", f"/api/workflow/items/{items[0]['id']}/act",
            "teacher:T1", body={"action": "reject", "comment": "不对"},
        ))
        self.assertEqual(r["item"]["state"], "rejected")
        cand = g.get_candidate(items[0]["ref_id"])
        self.assertEqual(cand["status"], "rejected")

    def test_error_pattern_external_decision_from_legacy_api(self):
        errors.record_error(self.student, self.kp["A"], "把导数当成积分了")
        pats = errors.list_patterns(self.kp["A"], verified_only=False)
        self.assertTrue(pats)
        pid = pats[0].id
        self.app.dispatch(req(
            "POST", f"/api/errors/patterns/{pid}/verify", "teacher:T1",
            body={"note": "确认"},
        ))
        # 同步后应已是 approved（外部裁决回写）
        item = self.db.query_one(
            "SELECT * FROM workflow_item WHERE kind='error_pattern' AND ref_id=?",
            (pid,),
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["state"], "approved")

    def test_workflow_event_append_only(self):
        qid = bank.add(
            self.kp["A"], "题二", "a", qtype="choice", options=["a"],
            origin="llm", citations=["kb:x"], teacher_verified=0,
        )
        wf.sync_pending()
        iid = wf.list_items("quiz_draft", "pending_review")[0]["id"]
        eid = self.db.scalar(
            "SELECT id FROM workflow_event WHERE item_id=? LIMIT 1", (iid,))
        with self.assertRaises(Exception):
            self.db.execute(
                "UPDATE workflow_event SET action='hack' WHERE id=?", (eid,))

    def test_session_login_can_act_on_queue(self):
        bank.add(
            self.kp["A"], "题三", "a", qtype="choice", options=["a"],
            origin="llm", citations=["kb:x"], teacher_verified=0,
        )
        login = self.app.dispatch(req("POST", "/api/auth/login", body={
            "kind": "teacher", "ident": "T1",
        }))
        tok = login["token"]
        self.app.dispatch(req("POST", "/api/workflow/sync", tok))
        q = self.app.dispatch(req("GET", "/api/workflow/queue", tok,
                                  query={"kind": "quiz_draft"}))
        self.assertTrue(q["items"])
        iid = q["items"][0]["id"]
        out = self.app.dispatch(req(
            "POST", f"/api/workflow/items/{iid}/act", tok,
            body={"action": "approve"},
        ))
        self.assertEqual(out["item"]["state"], "approved")
