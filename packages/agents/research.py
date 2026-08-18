"""限域调研：结构化报告，但只在我们自己的三个知识库里查。

DeepTutor 的 Deep Research 接六家网络搜索，能写出很好看的综述。
我们刻意不接，理由有三条：

1. **演示与生产必须跑同一套逻辑**，而演示环境常常没有外网（CLAUDE.md §4）。
2. Jill Watson 的证据摆在那里：绑定课程材料后准确率 78.7%，通用助手 30.7%。
   对实验班学生真正有用的不是全网综述，是"我们这门课、我们这个项目里怎么说"。
3. 学生把调研整个外包出去，产出会更好看，人会更不会。所以调研的产出
   **只计入交付物，不计入掌握度**——它证明不了任何知识点掌握。

生成之后还要过一道落地性检查（packages/tools/ground.py）：
重合率低的段落不删，标"低支撑"，让写的人和看的人都知道这一段悬着。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.db import dumps, get_db, loads
from packages.core.models import Message, confidence_from_evidence
from packages.core.timeutil import now_str
from packages.graph import repo as graph_repo
from packages.rag import retriever
from packages.state import tracker
from packages.tools import ground

from .base import Agent

# 三个库都查：教材说原理，项目材料说做法，培养方案说要求
DEFAULT_KBS = ("course", "project", "program")


@dataclass
class Section(Message):
    title: str = ""
    kb: str = ""
    body: str = ""
    citations: list = field(default_factory=list)
    groundedness: float = 0.0
    grounded: bool = True


@dataclass
class ResearchReport(Message):
    note_id: int = 0
    student_id: int = 0
    topic: str = ""
    sections: list = field(default_factory=list)
    body_md: str = ""
    citations: list = field(default_factory=list)
    n_sections: int = 0
    n_unsourced: int = 0
    kp_hits: list = field(default_factory=list)
    confidence: float = 0.0
    evidence_count: int = 0
    caveat: str = ""
    degraded: bool = False
    note: str = ("调研报告只检索本系统的三个知识库，不接外网；"
                 "产出计入交付物，不计入任何知识点掌握度")


class ResearchAgent(Agent):
    name = "research"
    system_prompt = (
        "你是资料整理助手。硬约束：\n"
        "1) 只依据给定材料写作，材料里没有的一个字都不许加；\n"
        "2) 不下结论、不做推荐，只把材料说了什么讲清楚；\n"
        "3) 不要写'据我所知''一般认为'这类没有出处的话；\n"
        "4) 每段控制在 150 字以内。"
    )

    def investigate(self, student_id: int, topic: str, project_code: str = "",
                    kbs: tuple = DEFAULT_KBS, per_kb: int = 3) -> ResearchReport:
        topic = (topic or "").strip()
        rep = ResearchReport(student_id=student_id, topic=topic)
        if len(topic) < 2:
            rep.caveat = "调研主题过短"
            return rep

        # ---- 确定性：先检索，再按库分节。章节结构不是模型想出来的 ----
        buckets: list[tuple[str, list]] = []
        for kb in kbs:
            r = retriever.search(topic, kb=kb, top_k=per_kb)
            if r.hits:
                buckets.append((kb, r))
        if not buckets:
            rep.caveat = "三个知识库中都没有检索到相关材料，未生成报告"
            return rep

        kp_codes: list[str] = []
        for _kb, r in buckets:
            for hit in r.hits:
                for c in hit.get("kp_codes") or []:
                    if c not in kp_codes:
                        kp_codes.append(c)

        # ---- 表达：每节只让模型复述这一节的材料 ----
        degraded = False
        for kb, r in buckets:
            material = r.context_text(per_kb)
            fields = {
                "意图": "限域调研",
                "调研主题": topic,
                "材料来源": _KB_TITLE.get(kb, kb),
                "材料原文": material[:1400],
                "硬约束": "只复述材料内容，不得补充材料之外的信息",
            }
            body, dg = self.express(fields, max_tokens=500)
            degraded = degraded or dg
            g = ground.check(body, material, CONFIG.teaching.groundedness_threshold)
            sec = Section(
                title=_KB_TITLE.get(kb, kb), kb=kb, body=body.strip(),
                citations=[c.to_dict() for c in r.citations],
                groundedness=g.ratio, grounded=g.grounded,
            )
            rep.sections.append(sec.to_dict())
            rep.citations.extend(sec.citations)
            if not g.grounded:
                rep.n_unsourced += 1

        rep.n_sections = len(rep.sections)
        rep.kp_hits = [
            {"code": c, "name": (kp.name if (kp := graph_repo.get_kp_by_code(c)) else "")}
            for c in kp_codes[:12]
        ]
        rep.body_md = self._render_md(rep)
        rep.degraded = degraded
        rep.evidence_count = len(rep.citations)
        rep.confidence = confidence_from_evidence(rep.evidence_count)
        if rep.n_unsourced:
            rep.caveat = (f"有 {rep.n_unsourced} 节的落地性低于 "
                          f"{CONFIG.teaching.groundedness_threshold}，"
                          "该节内容可能超出材料范围，请核对后再引用")
        elif rep.evidence_count < 3:
            rep.caveat = "数据不足，仅供参考"

        # ---- 落库与留痕：写笔记 + 写行为事件（不带对错，因此不动掌握度）----
        proj = graph_repo.get_project(project_code) if project_code else None
        rep.note_id = get_db().execute(
            "INSERT INTO study_note(student_id, kind, topic, body_md, citations,"
            " n_sections, n_unsourced, project_id, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (student_id, "research", topic[:300], rep.body_md, dumps(rep.citations),
             rep.n_sections, rep.n_unsourced, proj["id"] if proj else None, now_str()),
        )
        tracker.record(
            student_id=student_id, event_type="research", kp_id=None, is_correct=None,
            source="project", source_ref=f"note:{rep.note_id}",
            payload={"topic": topic[:200], "n_sections": rep.n_sections,
                     "n_unsourced": rep.n_unsourced, "kp_codes": kp_codes[:12]},
        )
        return rep

    @staticmethod
    def _render_md(rep: ResearchReport) -> str:
        lines = [f"# {rep.topic}", "",
                 "> 本报告只检索院内三个知识库（教材 / 项目材料 / 培养方案），未接入外网。", ""]
        for sec in rep.sections:
            lines.append(f"## {sec['title']}")
            if not sec["grounded"]:
                lines.append(f"> ⚠ 低支撑：与材料的重合率仅 {sec['groundedness']:.0%}，"
                             "以下内容可能超出材料范围")
            lines += ["", sec["body"], ""]
            if sec["citations"]:
                lines.append("来源：" + "；".join(
                    f"{c['title']}（{c['ref']}）" for c in sec["citations"][:5]))
                lines.append("")
        return "\n".join(lines)

    def notes(self, student_id: int, limit: int = 20) -> list[dict]:
        rows = get_db().query(
            "SELECT id, kind, topic, n_sections, n_unsourced, project_id, created_at"
            " FROM study_note WHERE student_id=? ORDER BY id DESC LIMIT ?",
            (student_id, limit),
        )
        return rows

    def note(self, note_id: int) -> dict | None:
        r = get_db().query_one("SELECT * FROM study_note WHERE id=?", (note_id,))
        if r:
            r["citations"] = loads(r["citations"], [])
        return r


_KB_TITLE = {
    "course": "教材与讲义怎么说",
    "project": "项目材料里的做法",
    "program": "培养方案的要求",
}
