"""L1 图谱查询路由。仅路由与鉴权，业务逻辑在 packages/graph。"""
from __future__ import annotations

from packages.graph import algo, repo

from ..microapi import App, HTTPError, Request


def register(app: App) -> None:
    @app.get("/api/courses")
    def courses(req: Request):
        return {"items": repo.list_courses()}

    @app.get("/api/courses/{code}/kps")
    def kps(req: Request):
        c = repo.get_course(req.path_params["code"])
        if not c:
            raise HTTPError(404, "课程不存在")
        items = [k.to_dict() for k in repo.list_kps(c["id"])]
        for it in items:
            it["prereqs"] = repo.prereqs_of(it["id"])
            it["modules"] = repo.modules_of_kp(it["id"])
        return {"course": c, "items": items}

    @app.get("/api/kps/{kp_id}")
    def kp_detail(req: Request):
        kp = repo.get_kp(int(req.path_params["kp_id"]))
        if not kp:
            raise HTTPError(404, "知识点不存在")
        return {
            "kp": kp.to_dict(),
            "prereqs": [repo.get_kp(i).to_dict() for i in repo.prereqs_of(kp.id)],
            "dependents": [repo.get_kp(i).to_dict() for i in repo.dependents_of(kp.id)],
            "modules": repo.modules_of_kp(kp.id),
            "blocking_severity": algo.blocking_severity(kp.id),
            "tasks_requiring": repo.tasks_requiring(kp.id),
        }

    @app.get("/api/modules")
    def modules(req: Request):
        return {"items": [m.to_dict() for m in repo.list_modules()]}

    @app.get("/api/projects")
    def projects(req: Request):
        return {"items": repo.list_projects()}

    @app.get("/api/projects/{code}/tasks")
    def tasks(req: Request):
        p = repo.get_project(req.path_params["code"])
        if not p:
            raise HTTPError(404, "项目不存在")
        items = []
        for t in repo.list_tasks(p["id"]):
            d = t.to_dict()
            d["kps"] = repo.required_kps(t.id, include_helpful=True)
            items.append(d)
        return {"project": p, "items": items}

    @app.get("/api/graph/stats", public=True)
    def stats(req: Request):
        return repo.stats().to_dict()
