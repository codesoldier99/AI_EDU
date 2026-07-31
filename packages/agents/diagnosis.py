"""模块一：个性化学习诊断（Phase 1 核心）。

价值主张：教师第一次能在课后当天看到全班卡在哪里，并且看到的是**根因**，
而不只是"这道题错得多"。根因回溯依赖 L1 的显式依赖结构，纯大模型方案做不可靠。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.db import get_db
from packages.core.models import Message, confidence_from_evidence
from packages.core.timeutil import now_str
from packages.engagement import service as engagement
from packages.errors import service as errors
from packages.graph import algo as graph_algo
from packages.graph import repo as graph_repo
from packages.state import repo as state_repo

from .base import Agent, AgentOutput


@dataclass
class WeakPoint(Message):
    kp_id: int = 0
    code: str = ""
    name: str = ""
    unit: str = ""
    avg_mastery: float = 0.0
    n_students: int = 0
    evidence_count: int = 0
    root_cause_kp_id: int = 0
    root_cause_name: str = ""
    root_cause_depth: int = 0
    blocking_severity: float = 0.0
    typical_errors: list = field(default_factory=list)


class DiagnosisAgent(Agent):
    name = "diagnosis"

    # ---------- 班级诊断 ----------
    def class_report(self, course_code: str, klass: str | None = None) -> AgentOutput:
        course = graph_repo.get_course(course_code)
        if not course:
            return AgentOutput(agent=self.name, narrative=f"未找到课程 {course_code}")
        thr = CONFIG.teaching.mastery_threshold
        rows = state_repo.class_mastery(course["id"], klass)
        heat = [
            {
                "kp_id": r["kp_id"], "code": r["code"], "name": r["name"], "unit": r["unit"],
                "avg_mastery": round(r["avg_mastery"], 4),
                "n_students": r["n_students"],
                "evidence_count": r["evidence_count"] or 0,
            }
            for r in rows
        ]
        class_avg = {r["kp_id"]: r["avg_mastery"] for r in rows}

        # Top 3 待攻克点：低掌握 × 高挡路度，而不是简单按正确率排序
        candidates = [r for r in rows if r["avg_mastery"] < thr]
        ranked = []
        for r in candidates:
            sev = graph_algo.blocking_severity(r["kp_id"])
            ranked.append((r, sev, (thr - r["avg_mastery"]) * (1 + sev / 10.0)))
        ranked.sort(key=lambda x: -x[2])

        top: list[WeakPoint] = []
        for r, sev, _score in ranked[:3]:
            trace = graph_algo.trace_root_cause(r["kp_id"], class_avg, thr)
            root = graph_repo.get_kp(trace["root_kp_id"])
            top.append(
                WeakPoint(
                    kp_id=r["kp_id"], code=r["code"], name=r["name"], unit=r["unit"],
                    avg_mastery=round(r["avg_mastery"], 4), n_students=r["n_students"],
                    evidence_count=r["evidence_count"] or 0,
                    root_cause_kp_id=trace["root_kp_id"],
                    # 根因等于自身时留空，避免"A←A"这种无意义的表述
                    root_cause_name=(root.name if root and trace["root_kp_id"] != r["kp_id"]
                                     else ""),
                    root_cause_depth=trace["depth"],
                    blocking_severity=sev,
                    typical_errors=errors.typical_errors_for(r["kp_id"]),
                )
            )

        # 需个别介入的学生
        attention = self._students_needing_attention(course["id"], klass, thr)
        # 降级热点：追问引擎在哪些知识点上频繁被迫给答案
        esc = self._escalation_hotspots(klass)
        eng = engagement.class_engagement(klass)

        total_evidence = sum(h["evidence_count"] for h in heat)
        conf = confidence_from_evidence(
            total_evidence // max(1, len(heat) or 1)
        )
        plan = {
            "course": {"code": course["code"], "name": course["name"]},
            "klass": klass or "全体",
            "generated_at": now_str(),
            "heatmap": heat,
            "top_weak_points": [w.to_dict() for w in top],
            "attention_students": attention,
            "escalation_hotspots": esc,
            "engagement": {
                "active_rate": eng["active_rate"],
                "n_active": eng["n_active"],
                "n_students": eng["n_students"],
                "at_risk": eng["at_risk"],
            },
            "suggested_focus": [
                {
                    "kp": w.name,
                    "reason": (
                        f"根因在「{w.root_cause_name}」，先补这一环"
                        if w.root_cause_kp_id != w.kp_id
                        else "本身即根因，建议直接重讲并当堂检测"
                    ),
                    "teacher_can_reject": True,   # 建议非指令，教师可一键否决
                }
                for w in top
            ],
        }
        narrative, degraded = self.express(
            {
                "意图": "班级诊断",
                "学生数": eng["n_students"],
                "知识点数": len(heat),
                "待攻克点": "、".join(w.name for w in top) or "（无）",
                "根因": "、".join(
                    f"{w.name}←{w.root_cause_name}" for w in top if w.root_cause_name
                ) or "（无）",
                "典型错误": "；".join(
                    e["description"] for w in top for e in w.typical_errors[:1]
                ) or "（暂无沉淀）",
                "数据充分性": self.caveat_for(total_evidence) or "证据充分",
            }
        )
        return AgentOutput(
            agent=self.name, narrative=narrative, plan=plan, confidence=conf,
            evidence_count=total_evidence, degraded=degraded,
            caveat=self.caveat_for(total_evidence),
        )

    # ---------- 个体诊断 ----------
    def student_report(self, student_id: int, course_code: str | None = None) -> AgentOutput:
        thr = CONFIG.teaching.mastery_threshold
        mastery = state_repo.mastery_vector(student_id)
        rows = state_repo.mastery_rows(student_id)
        weak = [r for r in rows if r["p_mastery"] < thr]
        weak.sort(key=lambda r: r["p_mastery"])
        items = []
        for r in weak[:8]:
            trace = graph_algo.trace_root_cause(r["kp_id"], mastery, thr)
            root = graph_repo.get_kp(trace["root_kp_id"])
            items.append(
                {
                    "kp_id": r["kp_id"], "code": r["code"], "name": r["name"],
                    "p_mastery": round(r["p_mastery"], 4),
                    "confidence": r["confidence"], "evidence_count": r["evidence_count"],
                    "root_cause": root.name if root and root.id != r["kp_id"] else "",
                    "root_cause_kp_id": trace["root_kp_id"],
                }
            )
        strong = sorted(
            [r for r in rows if r["p_mastery"] >= thr], key=lambda r: -r["p_mastery"]
        )[:5]
        n_evidence = sum(r["evidence_count"] for r in rows)
        plan = {
            "student": state_repo.get_student(student_id),
            "weak_points": items,
            "strong_points": [
                {"name": r["name"], "p_mastery": round(r["p_mastery"], 4)} for r in strong
            ],
            "n_kps_tracked": len(rows),
        }
        return AgentOutput(
            agent=self.name, plan=plan,
            narrative="",  # 个体诊断默认不调模型，节省成本；需要时前端再请求叙述
            confidence=confidence_from_evidence(n_evidence // max(1, len(rows) or 1)),
            evidence_count=n_evidence, caveat=self.caveat_for(n_evidence),
        )

    # ---------- 辅助 ----------
    def _students_needing_attention(self, course_id: int, klass: str | None,
                                    thr: float) -> list[dict]:
        sql = (
            "SELECT s.id, s.sid, s.name, AVG(m.p_mastery) AS avg_m, SUM(m.evidence_count) AS n"
            " FROM mastery_state m JOIN student s ON s.id=m.student_id"
            " JOIN knowledge_point k ON k.id=m.kp_id WHERE k.course_id=?"
        )
        params: list = [course_id]
        if klass:
            sql += " AND s.klass=?"
            params.append(klass)
        sql += " GROUP BY s.id, s.sid, s.name ORDER BY avg_m ASC LIMIT 8"
        rows = get_db().query(sql, params)
        out = []
        for r in rows:
            if r["avg_m"] is None or r["avg_m"] >= thr:
                continue
            out.append(
                {
                    "student_id": r["id"], "sid": r["sid"], "name": r["name"],
                    "avg_mastery": round(r["avg_m"], 4),
                    "evidence_count": r["n"] or 0,
                    "caveat": self.caveat_for(r["n"] or 0),
                }
            )
        return out

    def _escalation_hotspots(self, klass: str | None) -> list[dict]:
        sql = (
            "SELECT e.kp_id, k.name, COUNT(*) AS n FROM learning_event e"
            " JOIN knowledge_point k ON k.id=e.kp_id"
            " JOIN student s ON s.id=e.student_id"
            " WHERE e.event_type='escalation'"
        )
        params: list = []
        if klass:
            sql += " AND s.klass=?"
            params.append(klass)
        sql += " GROUP BY e.kp_id, k.name ORDER BY n DESC LIMIT 5"
        return get_db().query(sql, params)
