"""授课计划生成：把一版教学大纲的章节切成一次次课。

plan()：纯确定性切片——章节知识点数超过 CONFIG.teaching.max_kp_per_session 时
拆成多次课，不超过则一章一次课。这是一条可被教师质疑、可复现的规则，
不是模型"觉得"该怎么分。
"""
from __future__ import annotations

import math

from packages.agents.base import Agent, AgentOutput
from packages.core.config import CONFIG
from packages.core.models import confidence_from_evidence
from packages.rag import retriever

from . import repo
from .models import SessionPlan, TeachingPlanResult


class TeachingPlanAgent(Agent):
    name = "teaching_plan"
    system_prompt = (
        "你是授课计划编写助理。只依据给定的一次课主题与知识点列表，写一段简短的教学"
        "环节建议（导入/讲授/练习/小结），禁止新增知识点，禁止提及具体课时以外的安排。"
    )

    def plan(self, syllabus_id: int, session_minutes: int = 90) -> TeachingPlanResult:
        syl = repo.get_syllabus(syllabus_id)
        if not syl:
            raise ValueError(f"教学大纲不存在：syllabus_id={syllabus_id}")
        content = syl["content"]
        max_kp = CONFIG.teaching.max_kp_per_session

        items: list[dict] = []
        seq = 1
        for chapter in content.get("chapters", []):
            codes = chapter.get("kp_codes", [])
            names = chapter.get("kp_names", [])
            if not codes:
                continue
            n_sessions = max(1, math.ceil(len(codes) / max_kp))
            chunk = math.ceil(len(codes) / n_sessions)
            for i in range(0, len(codes), chunk):
                part_codes = codes[i:i + chunk]
                part_names = names[i:i + chunk]
                suffix = f"（{i // chunk + 1}/{n_sessions}）" if n_sessions > 1 else ""
                items.append(SessionPlan(
                    seq=seq, title=f"{chapter['unit']}{suffix}", unit=chapter["unit"],
                    kp_codes=part_codes, kp_names=part_names, duration_min=session_minutes,
                ).to_dict())
                seq += 1

        return TeachingPlanResult(
            syllabus_id=syllabus_id, course_code=content.get("course_code", ""),
            items=items,
        )

    def generate(self, syllabus_id: int, session_minutes: int = 90,
                 fill_content: bool = False) -> AgentOutput:
        p = self.plan(syllabus_id, session_minutes)
        degraded = False
        if fill_content:
            for it in p.items:
                ctx = retriever.grounded_context(
                    f"{it['title']} {'/'.join(it['kp_names'])}", intent="teaching_plan", top_k=2,
                )
                text, d = self.express(
                    {
                        "意图": "授课计划说明",
                        "本次课主题": it["title"],
                        "知识点": "、".join(it["kp_names"]),
                        "课时": f"{it['duration_min']} 分钟",
                        "教材依据": ctx.context_text(1)[:300],
                    },
                    max_tokens=300,
                )
                it["narrative"] = text
                degraded = degraded or d

        ids = repo.save_teaching_plan(syllabus_id, p.items)
        for item_id, it in zip(ids, p.items):
            it["id"] = item_id

        evid = len(p.items)
        narrative = f"已按大纲拆出 {evid} 次课，每次课 {session_minutes} 分钟。"
        return AgentOutput(
            agent=self.name, narrative=narrative, plan=p.to_dict(),
            confidence=confidence_from_evidence(evid), evidence_count=evid,
            degraded=degraded,
        )
