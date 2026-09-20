"""求职智能体：岗位需求三层图谱与简历生成的路由。

第一层（岗位）与第二层（需求分解树）走 GET /api/jobs 与 /api/career/{job_code}，
第三层由 CareerAgent 一次性拼进同一份 3D 视图数据里。生成简历是唯一的写请求，
但它只产出一段文字，不写任何学生状态——本文件只做路由与鉴权，
判定逻辑全部在 packages/agents/career.py。
"""
from __future__ import annotations

from packages.agents.career import CareerAgent
from packages.graph import repo as graph_repo

from .. import auth
from ..microapi import App, HTTPError, Request

_agent = CareerAgent()


def _resolve_student(req: Request) -> int | None:
    sid = req.qi("student_id") or req.principal.get("student_id")
    if sid:
        auth.assert_can_view_student(req, int(sid))
    return int(sid) if sid else None


def register(app: App) -> None:
    @app.get("/api/jobs")
    def jobs(_req: Request):
        return {"jobs": [j.to_dict() for j in graph_repo.list_jobs()]}

    @app.get("/api/career/{job_code}")
    def career(req: Request):
        student_id = _resolve_student(req)
        data = _agent.build_universe(req.path_params["job_code"], student_id)
        if data is None:
            raise HTTPError(404, "岗位不存在")
        return data

    @app.get("/api/career/{job_code}/fit")
    def fit(req: Request):
        student_id = _resolve_student(req)
        if not student_id:
            raise HTTPError(400, "缺少 student_id")
        try:
            return _agent.fit_report(student_id, req.path_params["job_code"]).to_dict()
        except KeyError as exc:
            raise HTTPError(404, str(exc)) from exc

    @app.post("/api/career/{job_code}/resume")
    def resume(req: Request):
        student_id = req.qi("student_id") or req.json.get("student_id") \
            or req.principal.get("student_id")
        if not student_id:
            raise HTTPError(400, "缺少 student_id")
        auth.assert_can_view_student(req, int(student_id))
        try:
            out = _agent.resume(int(student_id), req.path_params["job_code"])
        except KeyError as exc:
            raise HTTPError(404, str(exc)) from exc
        return out.to_dict()
