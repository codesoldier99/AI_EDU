"""模块五：学习成效画像。

维度：能力雷达图 / 成长曲线 / 知识覆盖图 / 项目贡献画像。
用途：导师精准指导、项目分组、动态分流与替补管理、学分认定佐证。

禁止项：不展示"课程进度百分比"，不做全班排行榜（避免挫伤后进者），
只做个人成长曲线。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.db import get_db
from packages.core.models import Message, confidence_from_evidence
from packages.core.timeutil import date_of, now_str
from packages.engagement import service as engagement
from packages.graph import algo as graph_algo
from packages.graph import repo as graph_repo
from packages.state import repo as state_repo
from packages.state import tracker

from .base import Agent, AgentOutput


@dataclass
class ProfileView(Message):
    student: dict = field(default_factory=dict)
    radar: list = field(default_factory=list)
    growth: list = field(default_factory=list)
    coverage: list = field(default_factory=list)
    projects: list = field(default_factory=list)
    engagement: dict = field(default_factory=dict)
    confidence: float = 0.0
    evidence_count: int = 0
    caveat: str = ""


class ProfileAgent(Agent):
    name = "profile"

    def build(self, student_id: int, course_code: str | None = None) -> ProfileView:
        student = state_repo.get_student(student_id)
        radar = tracker.recompute_ability(student_id)
        mastery = state_repo.mastery_vector(student_id)
        coverage = []
        if course_code:
            c = graph_repo.get_course(course_code)
            if c:
                coverage = graph_algo.coverage_map(c["id"], mastery)
        growth = self.growth_curve(student_id)
        projects = self.project_contribution(student_id)
        eng = engagement.view(student_id).to_dict()
        evid = sum(r["evidence_count"] for r in state_repo.mastery_rows(student_id))
        return ProfileView(
            student=student or {},
            radar=radar,
            growth=growth,
            coverage=coverage,
            projects=projects,
            engagement=eng,
            confidence=confidence_from_evidence(evid // max(1, len(mastery) or 1)),
            evidence_count=evid,
            caveat="" if evid >= CONFIG.teaching.min_evidence_for_report * 3
            else "数据不足，仅供参考",
        )

    def growth_curve(self, student_id: int, buckets: int = 12) -> list[dict]:
        """成长曲线：按周聚合正确事件占比与累计证据。

        注意：曲线画的是"投入与掌握的累计变化"，不是正确率排名，
        也绝不作为任何优化目标（铁律 5）。
        """
        events = state_repo.list_events(student_id=student_id)
        by_week: dict[str, dict] = {}
        for ev in events:
            d = date_of(ev["occurred_at"])
            if not d:
                continue
            wk = d[:8] + ("01" if int(d[8:10]) <= 7 else "08" if int(d[8:10]) <= 14
                          else "15" if int(d[8:10]) <= 21 else "22")
            b = by_week.setdefault(wk, {"week": wk, "events": 0, "scored": 0, "correct": 0,
                                        "escalations": 0})
            b["events"] += 1
            if ev["is_correct"] is not None:
                b["scored"] += 1
                b["correct"] += int(ev["is_correct"])
            if ev["event_type"] == "escalation":
                b["escalations"] += 1
        out = [by_week[k] for k in sorted(by_week)][-buckets:]
        # 附上每个时间片结束时的平均掌握度（用重算而非快照，保证可审计）
        for b in out:
            b["avg_mastery_at_end"] = self._avg_mastery_at(student_id, b["week"])
        return out

    def _avg_mastery_at(self, student_id: int, until_date: str) -> float:
        from packages.state.bkt import BKTParams
        from packages.state.bkt import update_with_weight
        from packages.state.tracker import SCORING_EVENTS, SOURCE_WEIGHT

        params = state_repo.all_params()
        default = BKTParams.default()
        st: dict[int, float] = {}
        for ev in state_repo.list_events(student_id=student_id):
            if date_of(ev["occurred_at"]) > until_date:
                break
            if ev["kp_id"] is None or ev["is_correct"] is None:
                continue
            if ev["event_type"] not in SCORING_EVENTS:
                continue
            prm = params.get(ev["kp_id"], default)
            cur = st.get(ev["kp_id"], prm.p_init)
            w = SOURCE_WEIGHT.get(ev["source"] or ev["event_type"], 0.6)
            st[ev["kp_id"]] = update_with_weight(cur, bool(ev["is_correct"]), prm, w)
        return round(sum(st.values()) / len(st), 4) if st else 0.0

    def project_contribution(self, student_id: int) -> list[dict]:
        rows = get_db().query(
            "SELECT p.id, p.code, p.name, p.ptype FROM project p"
            " JOIN project_member m ON m.project_id=p.id WHERE m.student_id=?",
            (student_id,),
        )
        out = []
        for p in rows:
            sig = get_db().query(
                "SELECT signal_class, COUNT(*) AS n, AVG(value) AS avg_v"
                " FROM project_signal WHERE project_id=? AND student_id=?"
                " GROUP BY signal_class",
                (p["id"], student_id),
            )
            tasks = get_db().query(
                "SELECT a.status, COUNT(*) AS n FROM task_assignment a"
                " JOIN project_task t ON t.id=a.task_id"
                " WHERE t.project_id=? AND a.student_id=? GROUP BY a.status",
                (p["id"], student_id),
            )
            out.append({**p, "signals": sig, "tasks": tasks})
        return out

    def narrative(self, student_id: int) -> AgentOutput:
        v = self.build(student_id)
        top = sorted(v.radar, key=lambda r: -r["level"])[:2]
        low = sorted(v.radar, key=lambda r: r["level"])[:2]
        text, degraded = self.express(
            {
                "意图": "周期简报",
                "连续天数": v.engagement.get("current_days", 0),
                "本周破关": "；".join(
                    a["detail"] for a in v.engagement.get("achievements", [])
                    if a["kind"] == "breakthrough"
                ) or "暂无破关记录",
                "优势维度": "、".join(f"{r['code']} {r['name']}" for r in top),
                "待补维度": "、".join(f"{r['code']} {r['name']}" for r in low),
                "下一步": "回到当前项目任务的缺口知识点，先攻最挡路的那一个",
                "数据充分性": v.caveat or "证据充分",
            },
            max_tokens=400,
        )
        return AgentOutput(
            agent=self.name, narrative=text, plan=v.to_dict(),
            confidence=v.confidence, evidence_count=v.evidence_count,
            degraded=degraded, caveat=v.caveat,
        )
