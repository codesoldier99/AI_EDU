"""运维与可审计性路由：健康检查、事件流重算、模型用量、知识库状态。"""
from __future__ import annotations

from packages.core.config import CONFIG
from packages.engagement import service as engagement
from packages.graph import repo as graph_repo
from packages.llm import gateway
from packages.rag import store
from packages.state import replay
from packages.state import repo as state_repo

from .. import auth
from ..microapi import App, Request


def register(app: App) -> None:
    @app.get("/api/health", public=True)
    def health(req: Request):
        g = graph_repo.stats()
        return {
            "ok": True,
            "llm": {"degraded": gateway.is_degraded(), "model": CONFIG.llm_model},
            "graph": g.to_dict(),
            "students": len(state_repo.list_students()),
            "kb": store.stats(),
            "stale_embeddings": store.stale_embeddings(),
        }

    @app.get("/api/config", public=True)
    def config(req: Request):
        """公开教学参数，便于教师核对系统当前用的是哪套阈值。"""
        t = CONFIG.teaching
        return {
            "mastery_threshold": t.mastery_threshold,
            "gap_push_limit": t.gap_push_limit,
            "escalation_attempts": t.escalation_attempts,
            "comeback_gap_days": t.comeback_gap_days,
            "min_evidence_for_report": t.min_evidence_for_report,
            "bkt_default": t.bkt_default,
            "signal_weights": t.signal_weights,
            "note": "以上参数均可配置，初值待教师团队拍板",
        }

    @app.get("/api/whoami")
    def whoami(req: Request):
        return req.principal

    @app.get("/api/replay/{student_id}")
    def replay_check(req: Request):
        sid = int(req.path_params["student_id"])
        auth.assert_can_view_student(req, sid)
        d = replay.verify(sid)
        e = engagement.verify_streak(sid)
        out = d.to_dict()
        out["engagement"] = e
        out["ok"] = d.ok and e["match"]
        return out

    @app.get("/api/llm/usage", role="teacher")
    def usage(req: Request):
        return {"items": gateway.usage_summary(), "degraded": gateway.is_degraded()}

    @app.get("/api/teacher/opens", role="teacher")
    def opens(req: Request):
        """Phase 1 验收指标：教师周主动打开诊断报告次数（对系统的观测，非对教师的评价）。"""
        return {"items": auth.report_open_stats(req.qi("days", 7)),
                "note": "本数据仅用于评估系统是否被真正使用，不进入任何教师考核"}
