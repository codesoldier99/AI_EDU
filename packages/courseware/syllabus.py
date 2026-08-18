"""教学大纲生成：培养方案/知识图谱 -> 章节骨架 -> （可选）逐章节文案。

plan()/express() 二段式：章节怎么分、每章覆盖哪些知识点、章节先后顺序，
全部由 packages.graph 的确定性查询与拓扑排序决定；大模型只在 fill_content=True
时被叫来把每一章"说成人话"，且一次只喂一章的要点，不会被要求凭空编排结构。
"""
from __future__ import annotations

from packages.agents.base import Agent, AgentOutput
from packages.core.models import confidence_from_evidence
from packages.graph import algo as graph_algo
from packages.graph import repo as graph_repo
from packages.rag import retriever

from .models import ChapterPlan, SyllabusPlan


class SyllabusAgent(Agent):
    name = "syllabus"
    system_prompt = (
        "你是教学大纲编写助理。只依据给定的章节名称与知识点列表写一段简短的章节说明，"
        "禁止新增知识点、禁止编造依赖关系、禁止给出与知识点列表不符的内容。"
    )

    def plan(self, course_id: int) -> SyllabusPlan:
        """确定性部分：按 knowledge_point.unit 分章，章节内部按拓扑序排知识点。"""
        course = graph_repo.get_course_by_id(course_id)
        kps = graph_repo.list_kps(course_id)
        order = graph_algo.topological_sort([k.id for k in kps])
        rank = {kp_id: i for i, kp_id in enumerate(order)}

        by_unit: dict[str, list] = {}
        first_seen: dict[str, int] = {}
        for i, k in enumerate(kps):
            unit = k.unit or "（未分章节）"
            by_unit.setdefault(unit, []).append(k)
            first_seen.setdefault(unit, i)

        chapters = []
        ordered_units = sorted(by_unit.items(), key=lambda kv: first_seen[kv[0]])
        for seq, (unit, items) in enumerate(ordered_units, start=1):
            items_sorted = sorted(items, key=lambda k: rank.get(k.id, 0))
            chapters.append(ChapterPlan(
                unit=unit, seq=seq,
                kp_codes=[k.code for k in items_sorted],
                kp_names=[k.name for k in items_sorted],
            ).to_dict())

        return SyllabusPlan(
            course_id=course_id,
            course_code=course["code"] if course else "",
            course_name=course["name"] if course else "",
            chapters=chapters,
            total_kps=len(kps),
        )

    def generate(self, course_id: int, fill_content: bool = False) -> AgentOutput:
        p = self.plan(course_id)
        citations: list = []
        degraded = False

        if p.total_kps == 0:
            return AgentOutput(
                agent=self.name, plan=p.to_dict(), narrative="",
                confidence=0.0, evidence_count=0,
                caveat="该课程知识图谱为空，无法生成大纲",
            )

        if fill_content:
            ctx = retriever.grounded_context(
                f"{p.course_name} 教学大纲 培养目标", intent="syllabus", top_k=3,
            )
            citations = [c.to_dict() for c in ctx.citations]
            for c in p.chapters:
                text, chapter_degraded = self.express(
                    {
                        "意图": "教学大纲章节说明",
                        "课程": p.course_name,
                        "章节": c["unit"],
                        "知识点": "、".join(c["kp_names"]),
                        "培养方案依据": ctx.context_text(1)[:400],
                    },
                    max_tokens=300,
                )
                c["narrative"] = text
                degraded = degraded or chapter_degraded

        narrative = f"已生成《{p.course_name}》教学大纲，共 {len(p.chapters)} 章、覆盖 {p.total_kps} 个知识点。"
        evid = p.total_kps
        return AgentOutput(
            agent=self.name, narrative=narrative, plan=p.to_dict(),
            citations=citations, confidence=confidence_from_evidence(evid),
            evidence_count=evid, degraded=degraded,
        )
