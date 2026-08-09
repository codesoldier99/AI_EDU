"""教师对生成内容的对话式修订（大纲章节说明 / 授课计划环节说明 / 课件要点文字）。

铁律 3 在这里的具体落点：聊天只能改"怎么说"，不能改"有哪些知识点""哪几章""哪几次课""哪几页"
这类结构事实——结构永远只能由各自的 plan()（SyllabusAgent/TeachingPlanAgent/DeckAgent）
重新生成，本文件的三个 refine_* 方法只对已经定好边界的一段文字做二次表达，
和 Agent.express() 的定位完全一致，只是多了"教师的修改指令"这一个额外字段。

草稿 / 保存两阶段：refine_* 只返回建议文本，不落库；教师看过觉得可以了，
前端再调 save_* 才真正写入——呼应 review.py 的"AI 建议、教师确认"闸门。
"""
from __future__ import annotations

from packages.agents.base import Agent, AgentOutput
from packages.graph import repo as graph_repo

from . import repo
from .deck import split_bullets


class ContentChatAgent(Agent):
    name = "courseware_chat"
    system_prompt = (
        "你是教学内容编辑助理。教师会给你一段已有文字、这段文字所属的知识点范围，"
        "以及一句修改指令。你只能在给定知识点范围内按指令改写这段文字，"
        "禁止引入知识点列表之外的新知识点或新事实，禁止编造材料中没有的具体数值。"
        "只输出修改后的文字本身，不要解释你做了什么、不要加前后缀。"
    )

    # ---------------- 教学大纲章节 ----------------
    def refine_syllabus_chapter(self, syllabus_id: int, seq: int, instruction: str) -> dict:
        syl = repo.get_syllabus(syllabus_id)
        if not syl:
            raise ValueError(f"教学大纲不存在：syllabus_id={syllabus_id}")
        chapter = next((c for c in syl["content"].get("chapters", []) if c["seq"] == seq), None)
        if chapter is None:
            raise ValueError(f"章节不存在：syllabus_id={syllabus_id} seq={seq}")
        text, degraded = self.express(
            {
                "意图": "教学大纲章节说明修订",
                "章节": chapter["unit"],
                "知识点": "、".join(chapter["kp_names"]),
                "现有说明": chapter.get("narrative") or "（暂无，请直接按指令写一段新的）",
                "教师指令": instruction,
            },
            max_tokens=350,
        )
        return {"draft": text, "degraded": degraded}

    def save_syllabus_chapter(self, syllabus_id: int, seq: int, text: str) -> dict:
        return repo.update_syllabus_chapter_narrative(syllabus_id, seq, text)

    # ---------------- 授课计划环节 ----------------
    def refine_session(self, item_id: int, instruction: str) -> dict:
        item = repo.get_teaching_plan_item(item_id)
        if not item:
            raise ValueError(f"授课计划条目不存在：id={item_id}")
        kp_names = [k.name for c in item["kp_codes"]
                    if (k := graph_repo.get_kp_by_code(c)) is not None]
        text, degraded = self.express(
            {
                "意图": "授课计划说明修订",
                "本次课主题": item["title"],
                "知识点": "、".join(kp_names),
                "课时": f"{item['duration_min']} 分钟",
                "现有说明": item.get("narrative") or "（暂无，请直接按指令写一段新的）",
                "教师指令": instruction,
            },
            max_tokens=350,
        )
        return {"draft": text, "degraded": degraded}

    def save_session(self, item_id: int, text: str) -> None:
        repo.update_session_narrative(item_id, text)

    # ---------------- 课件要点 ----------------
    def refine_slide(self, deck_id: int, slide_index: int, instruction: str) -> dict:
        deck = repo.get_deck(deck_id)
        if not deck:
            raise ValueError(f"课件不存在：deck_id={deck_id}")
        slides = deck["deck_plan"].get("slides", [])
        if not (0 <= slide_index < len(slides)):
            raise ValueError(f"页码越界：deck_id={deck_id} slide_index={slide_index}")
        slide = slides[slide_index]
        text, degraded = self.express(
            {
                "意图": "课件要点修订",
                "知识点": slide.get("title", ""),
                "现有要点": "；".join(slide.get("bullets", [])) or "（暂无，请直接按指令写几条新的）",
                "教师指令": instruction,
            },
            max_tokens=300,
        )
        if degraded:
            # 离线模板只是把原要点原样带一句免责说明返回，不是真改写——
            # 这种情况下不要再拿它去重新切分（会把免责说明和原要点糊在一起，切得七零八落），
            # 保留原有 bullets 不动，让教师一眼就能看出"这不是真的改过"。
            bullets = slide.get("bullets", [])
        else:
            max_bul = 6  # 聊天场景稍微放宽一点，教师明确要求时不必卡在生成时的默认上限
            bullets = split_bullets(text, max_bul)
        return {"draft": text, "bullets": bullets, "degraded": degraded}

    def save_slide(self, deck_id: int, slide_index: int, bullets: list[str]) -> dict:
        return repo.update_deck_slide_bullets(deck_id, slide_index, bullets)

    def rerender_after_edit(self, deck_id: int) -> AgentOutput:
        """保存要点文字后，教师若想让下载的 pptx 也同步，调这个把编辑后的内容重新渲染一遍。"""
        from .deck import DeckAgent  # 延迟导入，避免循环依赖（deck.py 不需要反过来依赖 chat.py）

        return DeckAgent().rerender(deck_id)
