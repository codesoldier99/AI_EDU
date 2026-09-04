"""教师报名的路由。本文件只做路由与鉴权，写入逻辑在 packages/signup。

一个刻意的不对称：**提交是公开的，名单是教师可见的**。
报名入口要能直接扫码打开、不登录就能填，否则会场上没人填；
但填进去的姓名与联系方式不能让同一个公开接口再吐出来。
"""
from __future__ import annotations

from packages.signup import repo

from ..microapi import App, HTTPError, Request


def register(app: App) -> None:
    @app.post("/api/signup", public=True)
    def create(req: Request):
        b = req.json or {}
        try:
            sid = repo.create(
                name=b.get("name", ""), dept=b.get("dept", ""),
                contact=b.get("contact", ""), roles=b.get("roles") or [],
                topic=b.get("topic", ""), note=b.get("note", ""),
                source=b.get("source", ""))
        except repo.SignupError as exc:
            raise HTTPError(400, str(exc)) from exc
        # 只回执 id 与最新计数，不回显填过的内容——公开接口不做数据回声
        return {"ok": True, "id": sid, "total": repo.stats()["total"]}

    @app.get("/api/signup/stats", public=True)
    def stats(req: Request):
        return repo.stats()

    @app.get("/api/signup", role="teacher")
    def listing(req: Request):
        return {"items": repo.list_all(req.qi("limit", 500)), "stats": repo.stats()}
