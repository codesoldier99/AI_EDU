"""任务-知识点映射的候选审核路由。

全部要求 teacher 角色：候选的裁决权只在教师手里，这是学分认定的依据。
仅路由与鉴权，业务逻辑在 packages/graph.repo 与 packages/agents/kpmatch。
"""
from __future__ import annotations

import json

from packages.graph import repo as graph_repo

from ..microapi import App, HTTPError, Request


def register(app: App) -> None:
    @app.get("/api/kpmatch/queue", role="teacher")
    def queue(req: Request):
        """待审队列。按置信度降序，教师先看最有把握的。"""
        project = req.q("project") or None
        status = req.q("status") or "pending"
        rows = graph_repo.list_candidates(project, status, req.qi("limit", 100) or 100)
        for r in rows:
            r["evidence"] = json.loads(r.get("evidence") or "[]")
        return {"items": rows, "stats": graph_repo.candidate_stats(project)}

    @app.get("/api/kpmatch/stats", role="teacher")
    def stats(req: Request):
        return graph_repo.candidate_stats(req.q("project") or None)

    @app.post("/api/kpmatch/decide/{cand_id}", role="teacher")
    def decide(req: Request):
        """采纳或否决。采纳才写入正式映射表，且署名 teacher。"""
        b = req.json or {}
        action = b.get("action", "accept")
        if action not in ("accept", "reject"):
            raise HTTPError(400, "action 只能是 accept 或 reject")
        who = req.principal.get("code") or "teacher"
        try:
            r = graph_repo.decide_candidate(
                int(req.path_params["cand_id"]), action == "accept",
                decided_by=who, necessity=b.get("necessity"))
        except KeyError as e:
            raise HTTPError(404, str(e))
        if not r.get("ok"):
            raise HTTPError(409, r.get("reason", "已被处理过"))
        return r

    @app.post("/api/kpmatch/why/{cand_id}", role="teacher")
    def why(req: Request):
        """让模型解释某一条候选。按需调用——教师不看那一条就不必花这次调用。

        解释失败不是错误：候选与分数本来就与模型无关，退回确定性说明即可。
        """
        from packages.agents.kpmatch import Candidate, KPMatchAgent

        c = graph_repo.get_candidate(int(req.path_params["cand_id"]))
        if not c:
            raise HTTPError(404, "候选不存在")
        task, kp = graph_repo.get_task(c["task_id"]), graph_repo.get_kp(c["kp_id"])
        terms = json.loads(c.get("evidence") or "[]")
        text, degraded = KPMatchAgent().explain(
            task.name,
            Candidate(kp_id=kp.id, kp_code=kp.code, kp_name=kp.name,
                      score=c["score"], confidence=c["confidence"], terms=terms))
        return {"rationale": text, "degraded": degraded, "evidence": terms,
                "confidence": c["confidence"]}

    @app.post("/api/kpmatch/propose", role="teacher")
    def propose(req: Request):
        """为一个项目重跑匹配。已标注与已判过的不会重复提出。"""
        from packages.agents.kpmatch import KPMatchAgent

        b = req.json or {}
        code = b.get("project")
        if not code:
            raise HTTPError(400, "缺少 project")
        proj = graph_repo.get_project(code)
        if not proj:
            raise HTTPError(404, "项目不存在")
        corpus = {}
        try:
            from packages.adapters.registry import get_adapter

            ad = get_adapter(proj["adapter_key"])
            if hasattr(ad, "task_corpus"):
                corpus = ad.task_corpus()
        except Exception:
            corpus = {}       # 适配器不可用不该让匹配失败，只是召回低些
        out = KPMatchAgent().propose_project(code, corpus=corpus, with_rationale=False)
        return out.to_dict() if hasattr(out, "to_dict") else {
            "narrative": out.narrative, "plan": out.plan}
