"""课件生成：授课计划的一次课 -> 结构化 DeckPlan -> 渲染成 pptx。

这是"质量要比大模型直接生成更高"的落点（CLAUDE.md 用户需求）：
  1. 每页覆盖哪些知识点、有没有实践环节页、配不配图表，全部由确定性查询决定——
     大模型只被要求把"已经定好的一页内容边界"写成类比/要点/例子，不许自由发挥结构；
  2. "常见误区"优先取自真实错误模式库（packages.errors），不是模型现编——
     没有真实数据时才退到模型建议，且明确标注来源（pitfalls_grounded），前端/渲染都要体现；
  3. 每份课件固定带一页"知识点难度分布"图（数据来自知识图谱标注，不依赖 LLM、不依赖学生数据），
     保证"图文并茂"里的"图"是真图，不是文字硬凑出来的排版效果；
  4. 渲染引擎（OfficeCLI / 内建 stdlib 引擎）与内容生成完全解耦，换渲染器不用重新生成内容。

LLM 输出契约：要求模型按 `- 字段名：内容` 逐行回复（与 Agent.express() 的输入格式同源），
用 packages.core.textfmt.parse_kv_fields 确定性解析——不信任模型输出的自由格式，
也不需要 JSON mode（不是所有底座都稳定支持）。
"""
from __future__ import annotations

from pathlib import Path

from packages.agents.base import Agent, AgentOutput
from packages.core.config import CONFIG, ROOT
from packages.core.models import confidence_from_evidence
from packages.core.textfmt import parse_kv_fields, split_items
from packages.errors import service as errors
from packages.graph import repo as graph_repo
from packages.rag import retriever

from . import officecli_render, repo
from .models import DeckPlan, SlidePlan

DECK_OUT_DIR = ROOT / "var" / "courseware"

# 知识点类型 -> 图标：纯装饰性的确定性映射，不需要模型判断，也不需要额外的图标资源文件
ICON_BY_TYPE = {"concept": "💡", "method": "🔧", "skill": "🎯", "tool": "🛠"}


class DeckAgent(Agent):
    name = "deck"
    system_prompt = (
        "你是课件文案助理，负责把一个知识点讲透、讲生动。只依据给定的知识点名称与教材依据组织内容，"
        "禁止引入给定材料之外的事实，禁止编造材料里没有的具体数值。"
        "必须严格按「- 字段名：内容」逐行输出，不要输出其他任何文字、不要编号、不要加粗符号。"
    )

    def plan_deck(self, teaching_plan_id: int) -> DeckPlan:
        """确定性部分：标题页 + 难度分布图 + 每个知识点一页骨架 + 关联实践任务页。"""
        item = repo.get_teaching_plan_item(teaching_plan_id)
        if not item:
            raise ValueError(f"授课计划条目不存在：id={teaching_plan_id}")
        kps = [graph_repo.get_kp_by_code(c) for c in item["kp_codes"]]
        kps = [k for k in kps if k]

        slides = [SlidePlan(
            layout="title", title=item["title"],
            subtitle=f"共 {len(kps)} 个知识点 · {item['duration_min']} 分钟",
        ).to_dict()]

        if kps:
            slides.append(SlidePlan(
                layout="barchart", title="本次课知识点难度分布",
                chart={
                    "chart_type": "bar", "title": "难度（0～1，来自知识图谱标注，非学生实测）",
                    "categories": [k.name for k in kps],
                    "series": [{"name": "难度", "values": [k.difficulty for k in kps]}],
                    "source": "knowledge_point.difficulty",
                },
            ).to_dict())

        for kp in kps:
            pitfalls, grounded = self._pitfalls_for(kp.id)
            slides.append(SlidePlan(
                layout="concept", title=f"{ICON_BY_TYPE.get(kp.kp_type, '💡')} {kp.name}",
                kp_codes=[kp.code], pitfalls=pitfalls, pitfalls_grounded=grounded,
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

    @staticmethod
    def _pitfalls_for(kp_id: int, limit: int = 2) -> tuple[list[str], bool]:
        """优先用真实错误模式库；没有真实数据才留空，交给 fill_content 用模型建议兜底
        （并标注 grounded=False，不能和真实数据混为一谈）。"""
        rows = errors.typical_errors_for(kp_id, limit=limit)
        if rows:
            return [r["description"] for r in rows], True
        return [], False

    def fill_content(self, plan: DeckPlan) -> tuple[DeckPlan, bool]:
        """按页逐个 express()，每页只喂"这一页要讲的知识点"，不喂整份课件结构。

        concept 布局要求模型一次性给出类比/要点/例子/（可选）误区建议，
        用统一的 `- 字段名：内容` 格式回复，再确定性解析——比"甩一段话再切句子"
        能拿到明显更有层次的内容，也不需要多轮调用。
        """
        max_bul = CONFIG.teaching.deck_max_bullets_per_slide
        degraded = False
        for s in plan.slides:
            if s["layout"] != "concept" or s.get("analogy") or not s.get("kp_codes"):
                continue
            kp_code = s["kp_codes"][0]
            kp = graph_repo.get_kp_by_code(kp_code)
            ctx = retriever.grounded_context(
                f"{s['title']} {kp.description if kp else ''}", intent="deck",
                kp_id=kp.id if kp else None, top_k=2,
            )
            text, d = self.express(
                {
                    "意图": "课件概念讲解",
                    "知识点": kp.name if kp else s["title"],
                    "教材依据": ctx.context_text(2)[:500],
                    "输出格式": "严格按下面几行输出——"
                        "- 一句话类比：用一个生活化的比喻讲清楚这个概念；"
                        "- 讲解要点：2到4条，用「；」分隔；"
                        "- 应用例子：一个具体的项目/生活场景；"
                        "- 易错点建议：0到2条，用「；」分隔，没有把握就留空，不要编",
                },
                max_tokens=400,
            )
            fields = parse_kv_fields(text)
            s["analogy"] = fields.get("一句话类比", "")
            s["bullets"] = split_items(fields.get("讲解要点", ""))[:max_bul]
            s["example"] = fields.get("应用例子", "")
            if not s.get("pitfalls"):  # 真实错误模式库已有数据时不用模型建议覆盖
                s["pitfalls"] = split_items(fields.get("易错点建议", ""))[:2]
            s["citations"] = [c.to_dict() for c in ctx.citations]
            degraded = degraded or d
        return plan, degraded

    def rerender(self, deck_id: int) -> AgentOutput:
        """把已保存（可能经聊天修订过要点文字）的 deck_plan_json 重新渲染成文件，
        不重新跑 plan_deck()——结构（哪些知识点、有几页）保持教师编辑前的样子，
        只有渲染这一步重来，呼应"内容与渲染分离"。"""
        deck = repo.get_deck(deck_id)
        if not deck:
            raise ValueError(f"课件不存在：deck_id={deck_id}")
        plan = DeckPlan.from_dict(deck["deck_plan"])
        out_path = DECK_OUT_DIR / f"deck_tp{plan.teaching_plan_id}" / "deck.pptx"
        result = officecli_render.render_deck(plan, out_path)
        repo.update_deck_render(deck_id, result.to_dict())
        caveat = "课件渲染引擎当前不可用，已降级生成，请稍后重试获取正式版" if result.degraded else ""
        return AgentOutput(
            agent=self.name, narrative=f"已重新渲染课件《{plan.title}》。",
            plan={"deck_id": deck_id, **result.to_dict()},
            degraded=result.degraded, caveat=caveat,
        )

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


def split_bullets(text: str, max_items: int) -> list[str]:
    """把一段文字切成若干条要点——供聊天式修订（packages.courseware.chat）复用，
    那里教师给的是自由改写指令，模型仍可能回一整段话，不走 `- 字段：值` 契约。

    确定性后处理：不信任模型输出的格式，统一按标点/换行切、去空、裁数量。
    """
    import re

    parts = [p.strip(" 　·-•").strip() for p in re.split(r"[。；\n]", text)]
    parts = [p for p in parts if p]
    return parts[:max_items] if parts else [text.strip()[:60]]
