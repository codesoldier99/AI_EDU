"""模块二/三/四/五 与副驾驶的路由。"""
from __future__ import annotations

from pathlib import Path

from packages.agents.asking import AskingAgent
from packages.agents.copilot import CopilotAgent, CreditMappingAgent
from packages.agents.profile import ProfileAgent
from packages.agents.review import ReviewAgent
from packages.agents.task import TaskAgent, compute_gap
from packages.engagement import service as engagement
from packages.graph import repo as graph_repo
from packages.state import tracker
from packages.state import verification

from .. import auth
from ..microapi import App, HTTPError, Request

asking = AskingAgent()
task_agent = TaskAgent()
review_agent = ReviewAgent()
profile_agent = ProfileAgent()
copilot = CopilotAgent()
credit = CreditMappingAgent()


def _self_or_teacher(req: Request, student_id: int | None = None) -> int:
    if student_id is None:
        student_id = req.principal.get("student_id") or req.qi("student_id")
    if student_id is None:
        raise HTTPError(400, "缺少 student_id")
    auth.assert_can_view_student(req, int(student_id))
    return int(student_id)


def register(app: App) -> None:
    # ---------------- 模块二：苏格拉底式引导 ----------------
    @app.post("/api/ask/start")
    def ask_start(req: Request):
        b = req.json
        sid = _self_or_teacher(req, b.get("student_id"))
        kp_id = b.get("kp_id")
        if not kp_id:
            raise HTTPError(400, "缺少 kp_id")
        return asking.start(sid, int(kp_id)).to_dict()

    @app.post("/api/ask/reply")
    def ask_reply(req: Request):
        b = req.json
        if not b.get("session_id"):
            raise HTTPError(400, "缺少 session_id")
        return asking.reply(int(b["session_id"]), b.get("text", ""),
                            bool(b.get("stuck"))).to_dict()

    @app.get("/api/ask/session/{session_id}")
    def ask_session(req: Request):
        v = asking.session_view(int(req.path_params["session_id"]))
        if v["session"]:
            auth.assert_can_view_student(req, v["session"]["student_id"])
        return v

    # ---------------- 模块三：拉取式任务 ----------------
    @app.get("/api/gap")
    def gap(req: Request):
        sid = _self_or_teacher(req)
        task_id = req.qi("task_id")
        if not task_id:
            raise HTTPError(400, "缺少 task_id")
        return compute_gap(sid, task_id).to_dict()

    @app.get("/api/next-target")
    def next_target(req: Request):
        sid = _self_or_teacher(req)
        task_id = req.qi("task_id")
        if not task_id:
            raise HTTPError(400, "缺少 task_id")
        return task_agent.next_learning_target(sid, task_id).to_dict()

    @app.get("/api/board")
    def board(req: Request):
        p = graph_repo.get_project(req.q("project", ""))
        if not p:
            raise HTTPError(404, "项目不存在")
        sid = req.principal.get("student_id") or req.qi("student_id")
        if sid:
            auth.assert_can_view_student(req, int(sid))
        return task_agent.board(p["id"], int(sid) if sid else None)

    @app.post("/api/tasks/status")
    def task_status(req: Request):
        b = req.json
        sid = _self_or_teacher(req, b.get("student_id"))
        return task_agent.set_status(int(b["task_id"]), sid, b.get("status", "doing"))

    # ---------------- 模块四：代码与文档审查 ----------------
    @app.post("/api/review/source")
    def review_source(req: Request):
        b = req.json
        sid = _self_or_teacher(req, b.get("student_id"))
        src = b.get("source", "")
        if not src:
            raise HTTPError(400, "缺少 source")
        return review_agent.review_source(
            sid, b.get("path", "submitted.py"), src, b.get("project_id"),
            use_llm=bool(b.get("use_llm", True)),
        ).to_dict()

    @app.get("/api/review/findings")
    def findings(req: Request):
        sid = req.qi("student_id") or req.principal.get("student_id")
        if sid:
            auth.assert_can_view_student(req, int(sid))
        return {"items": review_agent.list_findings(int(sid) if sid else None)}

    @app.post("/api/review/findings/{finding_id}/action", role="teacher")
    def finding_action(req: Request):
        b = req.json
        return review_agent.teacher_action(
            int(req.path_params["finding_id"]), b.get("action", "accepted"),
            b.get("note", ""),
        )

    @app.get("/api/review/rules", role="teacher")
    def rules(req: Request):
        return {"items": review_agent.rejection_stats()}

    # ---------------- 模块五：画像 ----------------
    @app.get("/api/profile/{student_id}")
    def profile(req: Request):
        sid = _self_or_teacher(req, int(req.path_params["student_id"]))
        return profile_agent.build(sid, req.q("course")).to_dict()

    @app.get("/api/engagement/{student_id}")
    def eng(req: Request):
        sid = _self_or_teacher(req, int(req.path_params["student_id"]))
        engagement.scan_achievements(sid)
        return engagement.view(sid).to_dict()

    @app.get("/api/engagement", role="teacher")
    def class_eng(req: Request):
        klass = req.q("klass")
        auth.assert_can_view_class(req, klass)
        return engagement.class_engagement(klass, req.qi("days", 7))

    # ---------------- 掌握的质量：验证 / 复检 / 提示依赖 ----------------
    @app.get("/api/verification/{student_id}")
    def verification_view(req: Request):
        sid = _self_or_teacher(req, int(req.path_params["student_id"]))
        v = verification.build(sid).to_dict()
        v["due_reviews"] = verification.due_reviews(sid, req.qi("limit", 5))
        v["independence"] = verification.independence(sid).to_dict()
        v["style_effectiveness"] = verification.style_effectiveness(sid)
        return v

    @app.get("/api/verification", role="teacher")
    def class_verification(req: Request):
        klass = req.q("klass")
        auth.assert_can_view_class(req, klass)
        c = graph_repo.get_course(req.q("course", "ML"))
        if not c:
            raise HTTPError(404, "课程不存在")
        return verification.class_verification(c["id"], klass)

    # ---------------- 副驾驶与简报 ----------------
    @app.post("/api/copilot")
    def copilot_ask(req: Request):
        b = req.json
        sid = _self_or_teacher(req, b.get("student_id"))
        return copilot.answer(sid, b.get("question", ""), b.get("project"),
                              b.get("task_id")).to_dict()

    @app.get("/api/brief/student/{student_id}")
    def brief_student(req: Request):
        sid = _self_or_teacher(req, int(req.path_params["student_id"]))
        return copilot.student_brief(sid).to_dict()

    @app.get("/api/brief/teacher", role="teacher")
    def brief_teacher(req: Request):
        klass = req.q("klass")
        auth.assert_can_view_class(req, klass)
        return copilot.teacher_brief(klass).to_dict()

    @app.post("/api/credit-map", role="teacher")
    def credit_map(req: Request):
        b = req.json
        return credit.suggest(b.get("description", ""), b.get("tech_stack", [])).to_dict()

    # ---------------- 作答回流（试卷/作业 → L2） ----------------
    @app.post("/api/events")
    def submit_events(req: Request):
        """作答结果回流 L2。唯一入口是 tracker，禁止直写掌握度。"""
        b = req.json
        sid = _self_or_teacher(req, b.get("student_id"))
        items = b.get("items") or []
        out = []
        for it in items:
            r = tracker.record(
                student_id=sid,
                event_type=it.get("event_type", "quiz"),
                kp_id=it.get("kp_id"),
                is_correct=it.get("is_correct"),
                source=it.get("source", "homework"),
                source_ref=it.get("source_ref", ""),
                payload=it.get("payload") or {},
            )
            out.append(r.to_dict())
        engagement.scan_achievements(sid)
        return {"items": out}
