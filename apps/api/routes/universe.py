"""知识宇宙：3D 图谱的数据接口。

一次性把渲染需要的全部信息给出去（节点、边、分层、掌握度叠加、根因路径），
避免前端在交互中反复往返——183 个知识点一次传完只有几十 KB。

本文件只做路由与鉴权，投影逻辑在 packages/agents/universe.py。
"""
from __future__ import annotations

from packages.agents import universe as universe_view

from .. import auth
from ..microapi import App, HTTPError, Request


def register(app: App) -> None:
    @app.get("/api/universe/{course_code}")
    def universe(req: Request):
        student_id = req.qi("student_id") or req.principal.get("student_id")
        if student_id:
            auth.assert_can_view_student(req, int(student_id))
        data = universe_view.build_universe(
            req.path_params["course_code"], int(student_id) if student_id else None
        )
        if data is None:
            raise HTTPError(404, "课程不存在")
        return data

    @app.get("/api/universe/{course_code}/rootcause/{kp_id}")
    def rootcause(req: Request):
        student_id = req.qi("student_id") or req.principal.get("student_id")
        if not student_id:
            raise HTTPError(400, "缺少 student_id")
        auth.assert_can_view_student(req, int(student_id))
        return universe_view.root_cause_path(
            int(student_id), int(req.path_params["kp_id"])
        )
