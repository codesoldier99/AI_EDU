"""知识点自动匹配智能体：把项目里的任务、文档、提交，对上知识图谱里的知识点。

## 它解决的是什么问题

一个真实产业项目接进来，教师要回答"这个任务需要哪些知识点"。
284 个知识点，41 个任务，人工逐条比对是 1 万多次判断——这就是拉取式教学
最贵的一道人工门槛，也是多数项目制课程最后退化成"做完项目发个学分"的原因。

## 它不解决什么

**它不决定映射成立与否。** 输出一律进 task_kp_candidate 待审队列，
教师采纳后才写进 task_kp_link，署名 teacher。理由见 data/seed/projects.yaml 开头：
任务-知识点映射是学分认定与能力追溯的依据，必须有人负责。

## 分工（铁律 3 的落地）

    plan()     确定性：packages.tools.lexmatch 算相似度、抽证据词、定置信度
    express()  大模型：把命中的证据词写成一句人话理由，供教师快速判断

模型断网、换厂商、胡说，候选集与分数一个字都不会变——理由那一栏会退化成
离线表达器拼的句子，仅此而已。这是"大模型是嘴，不是脑"在本模块的具体形态。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.graph import algo as graph_algo
from packages.graph import repo as graph_repo
from packages.tools.lexmatch import Doc, Index, confidence

from .base import Agent, AgentOutput


@dataclass
class Candidate:
    kp_id: int
    kp_code: str
    kp_name: str
    score: float
    confidence: float
    terms: list[str] = field(default_factory=list)
    necessity: str = "helpful"
    source_ref: str = ""
    rationale: str = ""
    matcher: str = "lexical-v1"
    via: str = ""        # 由谁扩展而来（前置扩展专用）


def build_index(course_codes: list[str] | None = None) -> tuple[Index, dict[int, object]]:
    """把知识图谱建成可检索索引。

    每个知识点的可匹配文本 = 代码 + 名称 + 一句话说明 + 所属章节。
    章节名要进去：'第8章 FPGA 时序与硬件同步触发' 这种上位词能把
    '触发''时序'这类线索接住，而单看知识点名往往漏掉。
    """
    kps = graph_repo.list_kps()
    if course_codes:
        wanted = set()
        for c in course_codes:
            course = graph_repo.get_course(c)
            if course:
                wanted.add(course["id"])
        kps = [k for k in kps if getattr(k, "course_id", None) in wanted]
    docs, by_id = [], {}
    for k in kps:
        unit = getattr(k, "unit", "") or ""
        text = f"{k.code} {k.name} {getattr(k, 'description', '') or ''} {unit}"
        docs.append(Doc(k.id, text))
        by_id[k.id] = k
    return Index(docs), by_id


class KPMatchAgent(Agent):
    name = "kpmatch"
    system_prompt = (
        "你是课程知识图谱的标注助手。依据给定字段，用一句不超过 40 字的中文说明"
        "「这个任务为什么可能需要这个知识点」，必须点出命中的关键词。"
        "不要下结论说一定需要，不要引入字段之外的事实。只输出这一句话。"
    )

    def __init__(self, course_codes: list[str] | None = None):
        self.index, self.by_id = build_index(course_codes)

    # ------------------------------------------------------------ 确定性部分
    def plan(self, text: str, source_ref: str = "", top_k: int | None = None) -> list[Candidate]:
        """对任意一段文字给出知识点候选。这是本模块唯一的判定入口。

        可复用于任务描述、提交信息、文档章节——凡是"这段话涉及哪些知识点"
        的问题都走这里，保证全系统对同一段文字的判断一致。
        """
        t = CONFIG.teaching
        k = top_k or t.kpmatch_top_k
        hits = self.index.search(text, top_k=k)
        out: list[Candidate] = []
        for i, h in enumerate(hits):
            conf = confidence(hits, i)
            if conf < t.kpmatch_min_conf:
                continue
            kp = self.by_id[h.key]
            out.append(Candidate(
                kp_id=kp.id, kp_code=kp.code, kp_name=kp.name,
                score=h.score, confidence=conf, terms=h.terms,
                necessity="required" if conf >= t.kpmatch_required_conf else "helpful",
                source_ref=source_ref,
            ))
        return out + self._expand_prereqs(out, source_ref)

    def _expand_prereqs(self, hits: list[Candidate], source_ref: str) -> list[Candidate]:
        """沿依赖边把命中知识点的前置也提为候选。

        字面匹配有个天生的盲区：任务说"训练一个分类模型"，字面上完全提不到
        "无泄漏切分"——可它就是做这件事必须先会的。而这恰恰是知识图谱存在的理由：
        依赖边本来就是用来回答"要做这个，还缺什么"的（见 CLAUDE.md §3.1）。

        实测（拿导师已标注的 154 条映射当标准答案）：只往上追一层，
        Top-6 召回率从 47.4% 提到 64.3%，每个任务平均只多两条候选。
        追两层到 66.2% 但候选数涨到 9 条，队列变长而收益递减，不划算。

        扩展出来的候选一律降置信度、一律标 helpful——它是推理来的，不是文本证据，
        并且在 via 里写明"因为谁"，教师一眼能看出这条是怎么冒出来的。
        """
        t = CONFIG.teaching
        seen = {c.kp_id for c in hits}
        out: list[Candidate] = []
        for src in hits[:t.kpmatch_expand_top]:
            for anc, depth in graph_algo.ancestors(src.kp_id, max_depth=t.kpmatch_expand_depth):
                if anc in seen:
                    continue
                seen.add(anc)
                kp = self.by_id.get(anc)
                if not kp:
                    continue        # 跨课程前置可能不在本次索引范围内
                out.append(Candidate(
                    kp_id=kp.id, kp_code=kp.code, kp_name=kp.name,
                    score=round(src.score * 0.5, 4),
                    confidence=round(src.confidence * (0.55 ** depth), 3),
                    terms=[f"{src.kp_code} 的前置"], necessity="helpful",
                    source_ref=source_ref, matcher="prereq-v1", via=src.kp_code,
                ))
        return out

    def match_task(self, task_code: str, extra_text: str = "",
                   top_k: int | None = None) -> tuple[object, list[Candidate]]:
        """匹配一个任务。extra_text 是这个任务的额外语料（验收清单、涉及的源码文件等）。

        额外语料是召回率的关键：任务名'关卡五 硬件同步触发'只有 8 个字，
        把验收清单那几行喂进来，才能把 encoder_decoder.v、PSO、触发延迟补偿
        这些真正的线索带上。
        """
        task = graph_repo.get_task_by_code(task_code)
        if not task:
            raise KeyError(f"任务不存在：{task_code}")
        parent = graph_repo.get_task(task.parent_id) if getattr(task, "parent_id", None) else None
        parts = [task.name]
        if parent:
            parts.append(parent.name)        # 父任务名提供上下文，如"关卡四 AI 之眼"
        if extra_text:
            parts.append(extra_text)
        return task, self.plan(" ".join(parts), source_ref=f"task:{task_code}", top_k=top_k)

    # ------------------------------------------------------------ 表达部分
    def explain(self, task_name: str, c: Candidate) -> tuple[str, bool]:
        """让模型写一句理由。失败或降级都不影响候选本身。"""
        try:
            return self.express({
                "任务": task_name,
                "候选知识点": f"{c.kp_code} {c.kp_name}",
                "命中关键词": "、".join(c.terms),
                "确定性得分": c.score,
            }, max_tokens=120)
        except Exception:
            return f"命中关键词：{'、'.join(c.terms)}", True

    # ------------------------------------------------------------ 入队
    def propose_project(self, project_code: str, corpus: dict[str, str] | None = None,
                        with_rationale: bool = True, top_k: int | None = None) -> AgentOutput:
        """为一个项目的全部任务生成候选并入待审队列。

        已被教师标注过的映射不会重复提出（见 repo.add_candidate），
        所以这个命令可以反复跑——每次只会多出真正的新候选。
        """
        proj = graph_repo.get_project(project_code)
        if not proj:
            raise KeyError(f"项目不存在：{project_code}")
        corpus = corpus or {}
        tasks = graph_repo.list_tasks(proj["id"])
        n_new, n_seen, degraded_any = 0, 0, False
        preview: list[dict] = []

        for task in tasks:
            _t, cands = self.match_task(task.code, corpus.get(task.code, ""), top_k=top_k)
            for c in cands:
                n_seen += 1
                if with_rationale:
                    c.rationale, deg = self.explain(task.name, c)
                    degraded_any = degraded_any or deg
                ok = graph_repo.add_candidate(
                    task_id=task.id, kp_id=c.kp_id, necessity=c.necessity,
                    score=c.score, confidence=c.confidence, evidence=c.terms,
                    source_ref=c.source_ref, rationale=c.rationale, matcher=c.matcher,
                )
                if ok:
                    n_new += 1
                    if len(preview) < 12:
                        preview.append({
                            "task": task.code, "kp": c.kp_code, "kp_name": c.kp_name,
                            "necessity": c.necessity, "confidence": c.confidence,
                            "terms": c.terms,
                        })

        stats = graph_repo.candidate_stats(project_code)
        return AgentOutput(
            agent=self.name,
            narrative=(f"为 {proj['name']} 的 {len(tasks)} 个任务生成候选 {n_seen} 条，"
                       f"其中新增待审 {n_new} 条（其余已被教师标注或判过）。"),
            plan={"project": project_code, "tasks": len(tasks), "proposed": n_seen,
                  "new_pending": n_new, "preview": preview, "queue": stats},
            confidence=0.0,          # 候选本身不是结论，置信度只在单条候选上有意义
            evidence_count=n_new,
            degraded=degraded_any,
            caveat="候选仅供教师参考，未经采纳不进入任何计算",
        )
