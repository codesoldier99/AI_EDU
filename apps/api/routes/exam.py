"""在线考试路由。

两类身份严格分开：
  · 考生（role='examinee'）——凭学号 + 一次性口令换取会话令牌，
    只能看自己那张卷子，看不到答案、看不到别人、看不到掌握度。
  · 教师——发布、签发准考证、监考、人工判分、排名切线、导出分析数据集。

本文件只做路由与鉴权，判分与排名一律在 packages/exam 下。
"""
from __future__ import annotations

from packages.exam import paper, scoring, session

from .. import auth
from ..microapi import App, HTTPError, Request


def register(app: App) -> None:
    # ---------------- 考生侧 ----------------
    @app.post("/api/exam/login", public=True)
    def exam_login(req: Request):
        """凭学号 + 一次性口令开考。这是整个系统里唯一对未鉴权请求开放的写接口。"""
        b = req.json
        for k in ("exam_code", "sid", "ticket"):
            if not (b.get(k) or "").strip():
                raise HTTPError(400, f"缺少 {k}")
        try:
            r = session.start(
                b["exam_code"].strip(), b["sid"].strip(), b["ticket"].strip(),
                client_ip=req.headers.get("x-forwarded-for", "")
                or req.headers.get("remote-addr", ""),
                user_agent=req.headers.get("user-agent", ""),
            )
        except ValueError as exc:
            raise HTTPError(403, str(exc)) from exc
        return {"token": f"exam:{r['token']}", "session_id": r["session_id"],
                "resumed": r["resumed"]}

    @app.get("/api/exam/paper", role="examinee")
    def exam_paper(req: Request):
        return session.view(req.principal["exam_token"]).to_dict()

    @app.post("/api/exam/save", role="examinee")
    def exam_save(req: Request):
        b = req.json
        if not b.get("question_id"):
            raise HTTPError(400, "缺少 question_id")
        try:
            return session.save(req.principal["exam_token"], int(b["question_id"]),
                                b.get("response", ""))
        except ValueError as exc:
            raise HTTPError(409, str(exc)) from exc

    @app.post("/api/exam/submit", role="examinee")
    def exam_submit(req: Request):
        return session.submit(req.principal["exam_token"])

    @app.get("/api/exam/heartbeat", role="examinee")
    def exam_heartbeat(req: Request):
        """前端每 15 秒问一次「还剩多久」。倒计时以这个返回值为准，不信本机时钟。"""
        v = session.view(req.principal["exam_token"])
        return {"remaining_sec": v.remaining_sec, "status": v.status,
                "answered": v.answered, "total": len(v.items)}

    # ---------------- 教师侧 ----------------
    @app.get("/api/exam/list", role="teacher")
    def exam_list(req: Request):
        from packages.core.db import get_db

        rows = get_db().query(
            "SELECT id, code, title, status, duration_min, total_score, opens_at,"
            " closes_at, published_at FROM exam ORDER BY id DESC")
        return {"items": rows}

    @app.get("/api/exam/{exam_id}/spec", role="teacher")
    def exam_spec(req: Request):
        return paper.spec_of(int(req.path_params["exam_id"])).to_dict()

    @app.post("/api/exam/{exam_id}/publish", role="teacher")
    def exam_publish(req: Request):
        b = req.json
        try:
            return paper.publish(int(req.path_params["exam_id"]),
                                 b.get("opens_at", ""), b.get("closes_at", ""))
        except ValueError as exc:
            raise HTTPError(400, str(exc)) from exc

    @app.post("/api/exam/{exam_id}/tickets", role="teacher")
    def exam_tickets(req: Request):
        b = req.json
        return {"items": session.issue_tickets(
            int(req.path_params["exam_id"]), b.get("student_ids"),
            bool(b.get("regenerate")))}

    @app.get("/api/exam/{exam_id}/monitor", role="teacher")
    def exam_monitor(req: Request):
        return session.monitor(int(req.path_params["exam_id"]))

    @app.post("/api/exam/{exam_id}/sweep", role="teacher")
    def exam_sweep(req: Request):
        """把超时未交的会话统一收上来。考试结束后点一次。"""
        return {"closed": session.sweep_expired(int(req.path_params["exam_id"]))}

    @app.get("/api/exam/{exam_id}/pending", role="teacher")
    def exam_pending(req: Request):
        return {"items": scoring.pending_queue(int(req.path_params["exam_id"])),
                "note": "程序题与规则判不出来的题在此；判完之后排名才作数"}

    @app.post("/api/exam/score", role="teacher")
    def exam_score(req: Request):
        b = req.json
        for k in ("session_id", "question_id", "score"):
            if b.get(k) is None:
                raise HTTPError(400, f"缺少 {k}")
        try:
            return scoring.teacher_score(int(b["session_id"]), int(b["question_id"]),
                                         float(b["score"]), b.get("note", ""))
        except ValueError as exc:
            raise HTTPError(400, str(exc)) from exc

    @app.get("/api/exam/{exam_id}/ranking", role="teacher")
    def exam_ranking(req: Request):
        return scoring.ranking(int(req.path_params["exam_id"]),
                               req.qi("cutoff", 53)).to_dict()

    @app.get("/api/exam/{exam_id}/export", role="teacher")
    def exam_export(req: Request):
        """导出分析数据集：含 RDD 的 running variable、处理变量与中心化分数。"""
        return {"rows": scoring.export_rows(int(req.path_params["exam_id"])),
                "note": "centered_score = 总分 − 分数线，即断点回归里的 (R − c)"}
