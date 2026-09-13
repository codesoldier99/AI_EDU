"""学生报名实验班的路由。本文件只做路由与鉴权，写入逻辑在 packages/enroll。

跟 signup.py 同一种不对称：**提交公开，名单教师可见**。
"""
from __future__ import annotations

from packages.enroll import repo

from ..microapi import App, HTTPError, Request


def register(app: App) -> None:
    @app.post("/api/enroll", public=True)
    def create(req: Request):
        b = req.json or {}
        try:
            eid = repo.create(
                name=b.get("name", ""), phone=b.get("phone", ""),
                source=b.get("source", ""))
        except repo.EnrollError as exc:
            raise HTTPError(400, str(exc)) from exc
        # 只回执 id 与总数，不回显填过的内容——公开接口不做数据回声
        return {"ok": True, "id": eid, "total": repo.stats()["total"]}

    @app.get("/api/enroll/stats", public=True)
    def stats(req: Request):
        return repo.stats()

    @app.get("/api/enroll", role="teacher")
    def listing(req: Request):
        return {"items": repo.list_all(req.qi("limit", 500)), "stats": repo.stats()}
