"""统一人工审核流程路由。

业务真相仍在各业务表；本路由提供列表 / 认领 / 裁决 / 审计查询。
review_finding 与 quiz_grade 的业务落库在本层调用 agents，以保持
packages/workflow 不依赖 agents（架构铁律）。
"""
from __future__ import annotations

from packages.workflow import service as wf
from packages.workflow.kinds import KINDS
from packages.workflow.service import AGENT_BACKED_KINDS, WorkflowError

from .. import auth
from ..microapi import App, HTTPError, Request


def _actor(req: Request) -> str:
    return auth.actor_id(req)


def _domain_for_agent_kind(item: dict, action: str, comment: str, extra: dict) -> dict:
    kind = item["kind"]
    ref_id = item["ref_id"]
    if kind == "review_finding":
        from packages.agents.review import ReviewAgent

        agent = ReviewAgent()
        if action == "approve":
            return agent.teacher_action(ref_id, "accepted", comment)
        if action in ("reject", "retire"):
            return agent.teacher_action(ref_id, "rejected", comment)
        return {}
    if kind == "quiz_grade":
        from packages.agents.quiz import QuizAgent

        if action != "approve":
            return {"skipped": True, "reason": "练习判分否决仅关闭流程项"}
        if "is_correct" not in extra:
            raise HTTPError(400, "quiz_grade 裁决需要 is_correct")
        return QuizAgent().teacher_grade(ref_id, bool(extra["is_correct"]), comment)
    raise HTTPError(400, f"未知 agents 流程种类 {kind}")


def register(app: App) -> None:
    @app.get("/api/workflow/kinds", perm="workflow.read")
    def kinds(req: Request):
        return {
            "items": [
                {"kind": k, "label": v["label"], "perm": v["perm"],
                 "ref_table": v["ref_table"]}
                for k, v in KINDS.items()
            ]
        }

    @app.get("/api/workflow/stats", perm="workflow.read")
    def stats(req: Request):
        return wf.stats()

    @app.post("/api/workflow/sync", perm="workflow.act")
    def sync(req: Request):
        """从各业务待审表投影进统一队列（幂等，可反复跑）。"""
        lim = (req.json or {}).get("limit_per_kind", 200)
        return wf.sync_pending(int(lim))

    @app.get("/api/workflow/queue", perm="workflow.read")
    def queue(req: Request):
        kind = req.q("kind") or None
        state = req.q("state") or "open"
        items = wf.list_items(kind, state, req.qi("limit", 100) or 100)
        return {"items": items, "stats": wf.stats()}

    @app.get("/api/workflow/items/{item_id}", perm="workflow.read")
    def item_view(req: Request):
        item = wf.get_item(int(req.path_params["item_id"]))
        if not item:
            raise HTTPError(404, "流程项不存在")
        return {"item": item, "events": wf.list_events(item["id"])}

    @app.post("/api/workflow/items/{item_id}/act", perm="workflow.act")
    def item_act(req: Request):
        b = req.json or {}
        action = b.get("action")
        if action not in ("claim", "unclaim", "approve", "reject", "retire"):
            raise HTTPError(400, "action 须为 claim/unclaim/approve/reject/retire")
        item_id = int(req.path_params["item_id"])
        item = wf.get_item(item_id)
        if not item:
            raise HTTPError(404, "流程项不存在")

        # 按种类细粒度权限（比 workflow.act 更严）
        need = wf.required_perm(item["kind"])
        auth.require_perm(req, need)

        actor = _actor(req)
        comment = b.get("comment") or ""
        extra = {k: v for k, v in b.items()
                 if k not in ("action", "comment")}
        domain_result = None
        if item["kind"] in AGENT_BACKED_KINDS and action in (
            "approve", "reject", "retire",
        ):
            domain_result = _domain_for_agent_kind(item, action, comment, extra)
        try:
            out = wf.act(
                item_id, action, actor,
                comment=comment, extra=extra, domain_result=domain_result,
            )
        except WorkflowError as exc:
            raise HTTPError(409, str(exc)) from exc
        return out
