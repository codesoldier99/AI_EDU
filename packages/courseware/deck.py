"""课件生成：授课计划的一次课 -> 结构化 DeckPlan -> 渲染成 pptx。

这是"质量要比大模型直接生成更高"的落点（CLAUDE.md 用户需求）：
  1. 每页覆盖哪些知识点、有没有实践环节页，全部由确定性查询决定——
     大模型只被要求把"已经定好的一页要点"写成 3~5 条通顺的话，不许自由发挥结构；
  2. DeckPlan.kp_coverage() 可以机器校验"这份课件是否覆盖了授课计划要求的全部知识点"，
     纯 LLM 一次性吐一份 PPT 文案做不到这种可验证的完整性；
  3. 渲染引擎（OfficeCLI / 内建 stdlib 引擎）与内容生成完全解耦，换渲染器不用重新生成内容。
"""
from __future__ import annotations

from pathlib import Path

from packages.agents.base import Agent, AgentOutput
from packages.core.config import CONFIG, ROOT
from packages.core.models import confidence_from_evidence
from packages.graph import repo as graph_repo
from packages.rag import retriever

from . import officecli_render, repo
from .models import DeckPlan, SlidePlan

DECK_OUT_DIR = ROOT / "var" / "courseware"


class DeckAgent(Agent):
    name = "deck"
    system_prompt = (
        "你是课件文案助理。只依据给定的知识点名称与教材依据，写 3 到 5 条简短的课件要点"
        "（每条一行，不要编号），禁止引入给定材料之外的事实，禁止出现具体数值除非材料里有。"
    )

    def plan_deck(self, teaching_plan_id: int) -> DeckPlan:
        """确定性部分：标题页 + 每个知识点一页骨架 + 关联实践任务页。"""
        item = repo.get_teaching_plan_item(teaching_plan_id)
        if not item:
            raise ValueError(f"授课计划条目不存在：id={teaching_plan_id}")
        kps = [graph_repo.get_kp_by_code(c) for c in item["kp_codes"]]
        kps = [k for k in kps if k]

        slides = [SlidePlan(
            layout="title", title=item["title"],
            subtitle=f"共 {len(kps)} 个知识点 · {item['duration_min']} 分钟",
        ).to_dict()]

        for kp in kps:
            slides.append(SlidePlan(
                layout="bullets", title=kp.name, kp_codes=[kp.code], bullets=[],
            ).to_dict())

        related_tasks: set[int] = set()
        for kp in kps:
            related_tasks |= set(graph_repo.tasks_requiring(kp.id))
        if related_tasks:
            names = [t.name for tid in sorted(related_tasks)
                     if (t := graph_repo.get_task(tid)) is not None]
            if names:
                slides.append(SlidePlan(
                    layout="bullets", title="实践环节",
                    bullets=[f"结合任务：{n}" for n in names[:5]],
                ).to_dict())

        return DeckPlan(
            teaching_plan_id=teaching_plan_id, course_code=item.get("course_code", ""),
            title=item["title"], slides=slides,
        )

    def fill_content(self, plan: DeckPlan) -> tuple[DeckPlan, bool]:
        """按页逐个 express()，每页只喂"这一页要讲的知识点"，不喂整份课件结构。"""
        max_bul = CONFIG.teaching.deck_max_bullets_per_slide
        degraded = False
        for s in plan.slides:
            if s["layout"] != "bullets" or s["bullets"] or not s.get("kp_codes"):
                continue
            kp_code = s["kp_codes"][0]
            kp = graph_repo.get_kp_by_code(kp_code)
            ctx = retriever.grounded_context(
                f"{s['title']} {kp.description if kp else ''}", intent="deck",
                kp_id=kp.id if kp else None, top_k=2,
            )
            text, d = self.express(
                {
                    "意图": "课件要点",
                    "知识点": s["title"],
                    "教材依据": ctx.context_text(2)[:500],
                },
                max_tokens=300,
            )
            s["bullets"] = _split_bullets(text, max_bul)
            s["citations"] = [c.to_dict() for c in ctx.citations]
            degraded = degraded or d
        return plan, degraded

    def render(self, plan: DeckPlan) -> AgentOutput:
        out_path = DECK_OUT_DIR / f"deck_tp{plan.teaching_plan_id}" / "deck.pptx"
        result = officecli_render.render_deck(plan, out_path)
        coverage = plan.kp_coverage()
        required = {code for s in plan.slides for code in s.get("kp_codes", [])}
        missing = sorted(required - set(coverage))
        deck_id = repo.save_deck(plan.teaching_plan_id, plan.to_dict(), result.to_dict())

        caveat = "课件渲染引擎当前不可用，已降级生成，请稍后重试获取正式版" if result.degraded else ""
        if missing:
            caveat = (caveat + "；" if caveat else "") + f"有 {len(missing)} 个知识点未覆盖到课件里"
        return AgentOutput(
            agent=self.name,
            narrative=f"已生成课件《{plan.title}》，共 {len(plan.slides)} 页，"
                      f"覆盖 {len(coverage)} 个知识点。",
            plan={"deck_id": deck_id, **result.to_dict(), "kp_coverage": coverage,
                  "missing_kp_codes": missing},
            confidence=confidence_from_evidence(len(plan.slides)),
            evidence_count=len(plan.slides),
            degraded=result.degraded, caveat=caveat,
        )


def _split_bullets(text: str, max_items: int) -> list[str]:
    """把 express() 产出的一段文字切成若干条要点——永远不直接把整段话糊到一页上。

    确定性后处理：不信任模型输出的格式，统一按标点/换行切、去空、裁数量。
    """
    import re

    parts = [p.strip(" 　·-•").strip() for p in re.split(r"[。；\n]", text)]
    parts = [p for p in parts if p]
    return parts[:max_items] if parts else [text.strip()[:60]]
