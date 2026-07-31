"""扩展模块：AI 学习副驾驶 + 周期简报 + 映射翻译。

副驾驶与通用对话工具的三点差异（不实现这三点就没有存在意义）：
1. 接项目知识库做 RAG，回答绑定本院真实项目；
2. 读个人能力档案，输出针对性建议；
3. 主动同步提交记录，无需学生手动上传。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.db import get_db
from packages.core.models import Message
from packages.core.timeutil import now_str
from packages.engagement import service as engagement
from packages.graph import repo as graph_repo
from packages.rag import retriever
from packages.state import repo as state_repo

from .base import Agent, AgentOutput
from .profile import ProfileAgent
from .task import compute_gap


class CopilotAgent(Agent):
    name = "copilot"
    system_prompt = (
        "你是学生的学习副驾驶。硬约束：\n"
        "1) 只依据检索到的项目/课程材料与学生能力档案回答；\n"
        "2) 不直接替学生写出完整作业答案，可给思路与检查清单；\n"
        "3) 材料不足时明说不足，不要编造。"
    )

    def answer(self, student_id: int, question: str, project_code: str | None = None,
               task_id: int | None = None) -> AgentOutput:
        # 差异 2：读个人能力档案
        radar = state_repo.ability_rows(student_id)
        weak = sorted(radar, key=lambda r: r["level"])[:2]
        # 差异 1：接项目知识库做 RAG
        ctx = retriever.grounded_context(question, intent="copilot")
        if not ctx.hits:  # 项目库无料时回落到课程库，但要标注
            ctx = retriever.grounded_context(question, intent="ask")
        gap_hint = ""
        if task_id:
            g = compute_gap(student_id, task_id)
            gap_hint = "、".join(p["name"] for p in g.push)
        text, degraded = self.express(
            {
                "意图": "学习副驾驶",
                "问题": question[:300],
                "项目资料": ctx.context_text(2)[:800],
                "薄弱能力": "、".join(f"{r['code']}{r['name']}" for r in weak),
                "当前缺口": gap_hint,
                "回答要求": "先给思路与检查清单，再指出可能踩的坑；不写完整答案",
            },
            max_tokens=600,
        )
        return AgentOutput(
            agent=self.name, narrative=text,
            plan={"gap_hint": gap_hint, "used_kb": ctx.kb},
            citations=[c.to_dict() for c in ctx.citations],
            confidence=min(0.9, ctx.hits[0]["score"]) if ctx.hits else 0.2,
            evidence_count=len(ctx.hits), degraded=degraded,
            caveat="" if ctx.hits else "未检索到相关材料，以下仅为通用建议",
        )

    # ---------- 周期简报 ----------
    def student_brief(self, student_id: int) -> AgentOutput:
        """学生版：重"下一步建议 + 本周破关"。"""
        return ProfileAgent().narrative(student_id)

    def teacher_brief(self, klass: str | None = None) -> AgentOutput:
        """教师版：重"异常与需介入事项"，使导师无需逐个跟踪学生进展。"""
        eng = engagement.class_engagement(klass)
        db = get_db()
        # 按知识点聚合而非按人聚合：教师要看的是"哪个概念在全班层面塌了"，
        # 而不是"谁又不会了"。
        esc = db.query(
            "SELECT k.name AS kp, COUNT(*) AS n, COUNT(DISTINCT e.student_id) AS n_students"
            " FROM learning_event e JOIN knowledge_point k ON k.id=e.kp_id"
            " JOIN student s ON s.id=e.student_id"
            " WHERE e.event_type='escalation'"
            + (" AND s.klass=?" if klass else "")
            + " GROUP BY k.name ORDER BY n DESC LIMIT 5",
            (klass,) if klass else (),
        )
        stalled = [r for r in eng["rows"] if r["active_days"] == 0][:8]
        text, degraded = self.express(
            {
                "意图": "教师简报",
                "活跃情况": f"{eng['n_active']}/{eng['n_students']} 人（近 7 天）",
                "需介入": "、".join(f"{s['name']}（{s['active_days']} 天未活跃）"
                                   for s in stalled[:5]) or "暂无",
                "降级热点": "、".join(
                    f"{r['kp']}（{r['n_students']} 人共 {r['n']} 次）" for r in esc[:3]
                ) or "暂无",
                "下一步": "优先联系名单内学生；降级热点知识点安排集中讲解",
            },
            max_tokens=300,
        )
        return AgentOutput(
            agent=self.name, narrative=text,
            plan={
                "active_rate": eng["active_rate"],
                "need_contact": stalled,
                "escalation_top": esc,
                "note": "本简报不生成任何针对教师的评价性数据",
            },
            evidence_count=eng["n_students"], confidence=0.8, degraded=degraded,
        )


class CreditMappingAgent(Agent):
    """映射翻译 Agent：项目描述 → 课程学分映射建议。

    技术约束：语义匹配**必须叠加规则校验**（单项目学分上限、必修课不可完全替代），
    避免大模型自由发挥导致学分认定失控；所有映射结果须可追溯至具体图谱节点。
    最终由学术指导委员会审核确认，系统只出建议。
    """

    name = "credit_map"
    MAX_CREDIT_PER_PROJECT = 6.0
    NON_REPLACEABLE = ("毕业设计", "专业综合实习")

    def suggest(self, description: str, tech_stack: list[str] | None = None) -> AgentOutput:
        text = description + " " + " ".join(tech_stack or [])
        ctx = retriever.grounded_context(text, intent="credit_map", top_k=4)
        # 语义匹配：把项目描述映射到知识点，再聚合到课程
        hits = retriever.search(text, kb="course", top_k=20)
        kp_scores: dict[str, float] = {}
        for h in hits.hits:
            for code in h["kp_codes"]:
                kp_scores[code] = max(kp_scores.get(code, 0.0), h["score"])
        # 关键词直配作为补充，保证可追溯
        for kp in graph_repo.list_kps():
            if kp.name and kp.name in text:
                kp_scores[kp.code] = max(kp_scores.get(kp.code, 0.0), 0.6)

        by_course: dict[int, dict] = {}
        for code, score in kp_scores.items():
            kp = graph_repo.get_kp_by_code(code)
            if not kp:
                continue
            c = by_course.setdefault(
                kp.course_id, {"course_id": kp.course_id, "kp_codes": [], "score": 0.0}
            )
            c["kp_codes"].append(code)
            c["score"] += score

        suggestions = []
        for cid, c in sorted(by_course.items(), key=lambda x: -x[1]["score"]):
            course = get_db().query_one("SELECT * FROM course WHERE id=?", (cid,))
            if not course:
                continue
            total_kps = len(graph_repo.list_kps(cid)) or 1
            coverage = len(c["kp_codes"]) / total_kps
            raw_credit = round(min(course["credit"] or 0, coverage * (course["credit"] or 0)), 1)
            rules_applied = []
            credit = raw_credit
            if course["name"] in self.NON_REPLACEABLE:
                credit = 0.0
                rules_applied.append("必修环节不可被项目完全替代")
            if credit > self.MAX_CREDIT_PER_PROJECT:
                credit = self.MAX_CREDIT_PER_PROJECT
                rules_applied.append(f"单项目学分上限 {self.MAX_CREDIT_PER_PROJECT}")
            if coverage < 0.25:
                credit = 0.0
                rules_applied.append("知识点覆盖不足 25%，不建议认定")
            suggestions.append(
                {
                    "course_code": course["code"], "course_name": course["name"],
                    "coverage": round(coverage, 3),
                    "matched_kp_codes": sorted(c["kp_codes"])[:20],
                    "suggested_credit": credit,
                    "rules_applied": rules_applied,
                    "traceable_to": "knowledge_point.code",
                }
            )
        text_out, degraded = self.express(
            {
                "意图": "学分映射建议",
                "项目描述": description[:200],
                "建议课程": "、".join(s["course_name"] for s in suggestions[:3]) or "（无匹配）",
                "说明": "系统只出建议，须经学术指导委员会审核",
            },
            max_tokens=300,
        )
        return AgentOutput(
            agent=self.name, narrative=text_out,
            plan={"suggestions": suggestions[:5], "requires_committee_review": True},
            citations=[c.to_dict() for c in ctx.citations],
            confidence=0.5, evidence_count=len(kp_scores), degraded=degraded,
            caveat="学分认定为人工决策，本结果仅为可追溯的建议",
        )
