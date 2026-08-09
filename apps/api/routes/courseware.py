"""教师工作台路由：教学大纲 / 授课计划 / 课件生成。仅路由与鉴权，业务逻辑在
packages/courseware 里。"""
from __future__ import annotations

from pathlib import Path

from packages.courseware import repo as courseware_repo
from packages.courseware.deck import DeckAgent
from packages.courseware.syllabus import SyllabusAgent
from packages.courseware.teaching_plan import TeachingPlanAgent
from packages.graph import repo as graph_repo

from .. import auth
from ..microapi import App, FileResponse, HTTPError, Request

syllabus_agent = SyllabusAgent()
plan_agent = TeachingPlanAgent()
deck_agent = DeckAgent()


def register(app: App) -> None:
    # ---------------- 教学大纲 ----------------
    @app.post("/api/courseware/syllabus", role="teacher")
    def generate_syllabus(req: Request):
        body = req.json
        course_id = int(body.get("course_id") or 0)
        if not course_id:
            raise HTTPError(400, "缺少 course_id")
        out = syllabus_agent.generate(course_id, fill_content=bool(body.get("fill_content")))
        syllabus_id = courseware_repo.save_syllabus(
            course_id, out.plan, generator_version=out.plan.get("generator_version", ""),
            created_by=req.principal.get("code", ""),
        )
        d = out.to_dict()
        d["syllabus_id"] = syllabus_id
        return d

    @app.get("/api/courseware/syllabus/{course_id}", role="teacher")
    def get_syllabus(req: Request):
        course_id = int(req.path_params["course_id"])
        s = courseware_repo.latest_syllabus(course_id)
        if not s:
            return {"items": [], "note": "尚未生成过教学大纲"}
        return s

    @app.post("/api/courseware/syllabus/{syllabus_id}/confirm", role="teacher")
    def confirm_syllabus(req: Request):
        sid = int(req.path_params["syllabus_id"])
        courseware_repo.confirm_syllabus(sid)
        return {"ok": True, "syllabus_id": sid}

    # ---------------- 授课计划 ----------------
    @app.post("/api/courseware/teaching-plan", role="teacher")
    def generate_teaching_plan(req: Request):
        body = req.json
        syllabus_id = int(body.get("syllabus_id") or 0)
        if not syllabus_id:
            raise HTTPError(400, "缺少 syllabus_id")
        out = plan_agent.generate(
            syllabus_id, session_minutes=int(body.get("session_minutes") or 90),
            fill_content=bool(body.get("fill_content")),
        )
        return out.to_dict()

    @app.get("/api/courseware/teaching-plan/{syllabus_id}", role="teacher")
    def list_teaching_plan(req: Request):
        syllabus_id = int(req.path_params["syllabus_id"])
        return {"items": courseware_repo.list_teaching_plan(syllabus_id)}

    # ---------------- 课件 ----------------
    @app.post("/api/courseware/deck", role="teacher")
    def generate_deck(req: Request):
        body = req.json
        teaching_plan_id = int(body.get("teaching_plan_id") or 0)
        if not teaching_plan_id:
            raise HTTPError(400, "缺少 teaching_plan_id")
        plan = deck_agent.plan_deck(teaching_plan_id)
        if body.get("fill_content"):
            plan, _ = deck_agent.fill_content(plan)
        out = deck_agent.render(plan)
        return out.to_dict()

    @app.get("/api/courseware/deck/{deck_id}", role="teacher")
    def get_deck(req: Request):
        deck_id = int(req.path_params["deck_id"])
        d = courseware_repo.get_deck(deck_id)
        if not d:
            raise HTTPError(404, "课件不存在")
        return d

    @app.get("/api/courseware/deck/{deck_id}/file", role="teacher")
    def download_deck(req: Request):
        deck_id = int(req.path_params["deck_id"])
        d = courseware_repo.get_deck(deck_id)
        if not d or not d.get("file_path"):
            raise HTTPError(404, "课件文件不存在")
        path = Path(d["file_path"])
        ctype = ("application/vnd.openxmlformats-officedocument.presentationml.presentation"
                 if d["artifact_type"] == "pptx" else "text/markdown; charset=utf-8")
        return FileResponse(path=path, content_type=ctype, filename=path.name)

    # ---------------- 课程列表（供前端选课下拉） ----------------
    @app.get("/api/courseware/courses", role="teacher")
    def courses(req: Request):
        return {"items": graph_repo.list_courses()}
