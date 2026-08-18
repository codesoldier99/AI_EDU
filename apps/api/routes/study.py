"""学习工作台：出题测练 / 分步解题 / 限域调研 / 图示 / 教学技能包。

"切目标，不切引擎"——这是 DeepTutor 那套 agent-native 设计里最值得抄的一句话：
学生换目标时，上下文应该跟着走，而不是换一个入口重新开始。
我们抄了这个形，没抄它的神：每个能力背后仍然是 plan()/express() 两段，
判定永远走确定性工具。

本文件只做路由与鉴权，业务逻辑一律在 packages 下。
"""
from __future__ import annotations

from packages.agents.quiz import QuizAgent
from packages.agents.research import ResearchAgent
from packages.agents.solve import SolveAgent
from packages.agents.visualize import VisualizeAgent
from packages.engagement import service as engagement
from packages.quiz import bank, selector
from packages.skills import stats as skill_stats

from .. import auth
from ..microapi import App, HTTPError, Request

quiz = QuizAgent()
solve = SolveAgent()
research = ResearchAgent()
viz = VisualizeAgent()


def _self_or_teacher(req: Request, student_id=None) -> int:
    if student_id is None:
        student_id = req.principal.get("student_id") or req.qi("student_id")
    if student_id is None:
        raise HTTPError(400, "缺少 student_id")
    auth.assert_can_view_student(req, int(student_id))
    return int(student_id)


def register(app: App) -> None:
    # ---------------- 题库：出题与审核（教师侧） ----------------
    @app.post("/api/quiz/draft", role="teacher")
    def quiz_draft(req: Request):
        b = req.json
        if not b.get("kp_id"):
            raise HTTPError(400, "缺少 kp_id")
        return quiz.draft(int(b["kp_id"]), int(b.get("n", 3)),
                          b.get("qtype", "choice")).to_dict()

    @app.get("/api/quiz/review-queue", role="teacher")
    def quiz_review_queue(req: Request):
        return quiz.review_queue(req.qi("limit", 50))

    @app.post("/api/quiz/review/{question_id}", role="teacher")
    def quiz_review(req: Request):
        b = req.json
        return bank.review(int(req.path_params["question_id"]),
                           b.get("action", "accept"), b.get("patch"))

    @app.get("/api/quiz/bank", role="teacher")
    def quiz_bank(req: Request):
        kp_id = req.qi("kp_id")
        if not kp_id:
            return {"stats": bank.stats()}
        items = bank.list_for_kp(kp_id, verified_only=req.q("all") != "1", limit=100)
        return {"items": [q.to_dict() for q in items], "stats": bank.stats()}

    # ---------------- 练习：组卷与批改（学生侧） ----------------
    @app.get("/api/practice-plan")
    def practice_plan(req: Request):
        """只看"该练什么"与理由，不出卷。理由要能被教师质疑，所以单独开一个口。"""
        sid = _self_or_teacher(req)
        plan = selector.practice_plan(sid, req.qi("task_id"), req.qi("limit"))
        out = plan.to_dict()
        out["missing_bank"] = selector.coverage_gaps(sid, plan.targets)
        return out

    @app.post("/api/quiz/assemble")
    def quiz_assemble(req: Request):
        b = req.json
        sid = _self_or_teacher(req, b.get("student_id"))
        return quiz.assemble(
            sid, task_id=b.get("task_id"), per_kp=int(b.get("per_kp", 1)),
            limit=b.get("limit"),
            verified_only=bool(b.get("verified_only", True)),
        ).to_dict()

    @app.post("/api/quiz/submit")
    def quiz_submit(req: Request):
        b = req.json
        if not b.get("paper_id"):
            raise HTTPError(400, "缺少 paper_id")
        sid = _self_or_teacher(req, b.get("student_id"))
        res = quiz.submit(int(b["paper_id"]), b.get("answers") or {}, sid)
        engagement.scan_achievements(sid)
        return res.to_dict()

    @app.get("/api/quiz/history/{student_id}")
    def quiz_history(req: Request):
        sid = _self_or_teacher(req, int(req.path_params["student_id"]))
        return quiz.history(sid, req.qi("limit", 20))

    @app.get("/api/quiz/pending", role="teacher")
    def quiz_pending(req: Request):
        klass = req.q("klass")
        auth.assert_can_view_class(req, klass)
        return {"items": quiz.pending_grading(klass, req.qi("limit", 50)),
                "note": "确定性判分放弃的作答；人工判分会追加新事件，不修改旧事件"}

    @app.post("/api/quiz/grade/{event_id}", role="teacher")
    def quiz_grade(req: Request):
        b = req.json
        if "is_correct" not in b:
            raise HTTPError(400, "缺少 is_correct")
        return quiz.teacher_grade(int(req.path_params["event_id"]),
                                  bool(b["is_correct"]), b.get("note", ""))

    # ---------------- 分步解题 ----------------
    @app.post("/api/solve/start")
    def solve_start(req: Request):
        b = req.json
        sid = _self_or_teacher(req, b.get("student_id"))
        if not (b.get("problem") or "").strip():
            raise HTTPError(400, "缺少 problem")
        return solve.start(sid, b["problem"], b.get("kp_id")).to_dict()

    @app.post("/api/solve/answer")
    def solve_answer(req: Request):
        b = req.json
        if not b.get("session_id"):
            raise HTTPError(400, "缺少 session_id")
        v = solve.view(int(b["session_id"]))
        auth.assert_can_view_student(req, v.student_id)
        return solve.answer(int(b["session_id"]), b.get("text", ""),
                            bool(b.get("stuck"))).to_dict()

    @app.get("/api/solve/{session_id}")
    def solve_view(req: Request):
        v = solve.view(int(req.path_params["session_id"]))
        auth.assert_can_view_student(req, v.student_id)
        return v.to_dict()

    @app.get("/api/solve/sessions/{student_id}")
    def solve_sessions(req: Request):
        sid = _self_or_teacher(req, int(req.path_params["student_id"]))
        return {"items": solve.sessions(sid, req.qi("limit", 10))}

    # ---------------- 限域调研 ----------------
    @app.post("/api/research")
    def research_run(req: Request):
        b = req.json
        sid = _self_or_teacher(req, b.get("student_id"))
        if not (b.get("topic") or "").strip():
            raise HTTPError(400, "缺少 topic")
        return research.investigate(sid, b["topic"], b.get("project", "")).to_dict()

    @app.get("/api/notes/{student_id}")
    def notes(req: Request):
        sid = _self_or_teacher(req, int(req.path_params["student_id"]))
        return {"items": research.notes(sid, req.qi("limit", 20))}

    @app.get("/api/note/{note_id}")
    def note_detail(req: Request):
        n = research.note(int(req.path_params["note_id"]))
        if not n:
            raise HTTPError(404, "笔记不存在")
        auth.assert_can_view_student(req, n["student_id"])
        return n

    # ---------------- 图示 ----------------
    @app.get("/api/figure")
    def figure(req: Request):
        sid = _self_or_teacher(req)
        kind = req.q("kind", "mastery_bars")
        try:
            return viz.render(kind, sid, req.qi("kp_id"), req.qi("limit", 12)).to_dict()
        except ValueError as exc:
            raise HTTPError(400, str(exc)) from exc

    # ---------------- 教学技能包 ----------------
    @app.get("/api/skills", public=True)
    def skills_list(req: Request):
        """技能包是教师资产，公开可查——看不见的扩展点等于不存在的扩展点。"""
        return skill_stats()
