"""模块一：个性化学习诊断路由。"""
from __future__ import annotations

from packages.agents.diagnosis import DiagnosisAgent
from packages.errors import service as errors
from packages.state import repo as state_repo

from .. import auth
from ..microapi import App, HTTPError, Request

agent = DiagnosisAgent()


def register(app: App) -> None:
    @app.get("/api/diagnosis/class/{course_code}", role="teacher")
    def class_report(req: Request):
        klass = req.q("klass")
        auth.assert_can_view_class(req, klass)
        auth.log_report_open(req.principal["code"], "class_diagnosis")
        out = agent.class_report(req.path_params["course_code"], klass)
        return out.to_dict()

    @app.get("/api/diagnosis/student/{student_id}")
    def student_report(req: Request):
        sid = int(req.path_params["student_id"])
        auth.assert_can_view_student(req, sid)
        return agent.student_report(sid, req.q("course")).to_dict()

    @app.get("/api/students", role="teacher")
    def students(req: Request):
        klass = req.q("klass")
        auth.assert_can_view_class(req, klass)
        return {"items": state_repo.list_students(klass)}

    @app.get("/api/students/{student_id}/mastery")
    def mastery(req: Request):
        sid = int(req.path_params["student_id"])
        auth.assert_can_view_student(req, sid)
        return {"items": state_repo.mastery_rows(sid)}

    @app.get("/api/students/{student_id}/events")
    def events(req: Request):
        sid = int(req.path_params["student_id"])
        auth.assert_can_view_student(req, sid)
        return {"items": state_repo.list_events(student_id=sid, limit=req.qi("limit", 200))}

    # ---- 错误模式库 ----
    @app.get("/api/errors/patterns", role="teacher")
    def patterns(req: Request):
        return {"items": [p.to_dict() for p in errors.list_patterns(
            req.qi("kp_id"), req.q("verified") == "1")]}

    @app.post("/api/errors/patterns/{pattern_id}/verify", role="teacher")
    def verify(req: Request):
        body = req.json
        errors.verify_pattern(
            int(req.path_params["pattern_id"]), body.get("note", ""),
            body.get("description"), body.get("root_cause_kp_id"),
        )
        return {"ok": True}

    @app.get("/api/errors/stats", role="teacher")
    def stats(req: Request):
        return errors.stats()
