"""教师工作台路由：教学大纲 / 授课计划 / 课件生成。仅路由与鉴权，业务逻辑在
packages/courseware 里。"""
from __future__ import annotations

from pathlib import Path

from packages.courseware import repo as courseware_repo
from packages.courseware.chat import ContentChatAgent
from packages.courseware.deck import DeckAgent
from packages.courseware.syllabus import SyllabusAgent
from packages.courseware.teaching_plan import TeachingPlanAgent
from packages.graph import repo as graph_repo

from .. import auth
from ..microapi import App, FileResponse, HTTPError, Request

syllabus_agent = SyllabusAgent()
plan_agent = TeachingPlanAgent()
deck_agent = DeckAgent()
chat_agent = ContentChatAgent()


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

    @app.get("/api/courseware/deck/by-session/{teaching_plan_id}", role="teacher")
    def deck_by_session(req: Request):
        """某次课已经生成过的最新一份课件（没有则返回 null）——用于教师切换到已生成
        过课件的次课时，不需要重新点「生成课件」就能看到已有内容。"""
        tid = int(req.path_params["teaching_plan_id"])
        rows = courseware_repo.list_decks(tid)
        return {"deck": courseware_repo.get_deck(rows[0]["id"]) if rows else None}

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

    @app.post("/api/courseware/deck/{deck_id}/rerender", role="teacher")
    def rerender_deck(req: Request):
        deck_id = int(req.path_params["deck_id"])
        out = deck_agent.rerender(deck_id)
        return out.to_dict()

    # ---------------- 对话式修订（大纲章节 / 授课环节 / 课件要点）----------------
    # kind: syllabus_chapter（ref_id=syllabus_id, sub_id=章节 seq）
    #     | session（ref_id=teaching_plan.id）
    #     | slide（ref_id=deck_id, sub_id=页码 index，从 0 开始）
    @app.post("/api/courseware/chat", role="teacher")
    def chat_refine(req: Request):
        body = req.json
        kind = body.get("kind")
        ref_id = int(body.get("ref_id") or 0)
        instruction = (body.get("instruction") or "").strip()
        if not ref_id or not instruction:
            raise HTTPError(400, "缺少 ref_id 或 instruction")
        if kind == "syllabus_chapter":
            return chat_agent.refine_syllabus_chapter(ref_id, int(body.get("sub_id") or 0),
                                                       instruction)
        if kind == "session":
            return chat_agent.refine_session(ref_id, instruction)
        if kind == "slide":
            return chat_agent.refine_slide(ref_id, int(body.get("sub_id") or 0), instruction)
        raise HTTPError(400, f"未知的 kind：{kind}")

    @app.post("/api/courseware/chat/save", role="teacher")
    def chat_save(req: Request):
        body = req.json
        kind = body.get("kind")
        ref_id = int(body.get("ref_id") or 0)
        if not ref_id:
            raise HTTPError(400, "缺少 ref_id")
        if kind == "syllabus_chapter":
            chat_agent.save_syllabus_chapter(ref_id, int(body.get("sub_id") or 0),
                                             body.get("text", ""))
        elif kind == "session":
            chat_agent.save_session(ref_id, body.get("text", ""))
        elif kind == "slide":
            chat_agent.save_slide(ref_id, int(body.get("sub_id") or 0), body.get("bullets") or [])
        else:
            raise HTTPError(400, f"未知的 kind：{kind}")
        return {"ok": True}

    # ---------------- 课程列表（供前端选课下拉） ----------------
    @app.get("/api/courseware/courses", role="teacher")
    def courses(req: Request):
        return {"items": graph_repo.list_courses()}
