"""混合召回：向量检索 + 知识图谱结构化过滤。

技术方案 §3.2：学生卡在知识点 K 时，检索范围自动扩展至 K 的前置节点集合，
而非仅做语义相似匹配——这是图谱带来的、纯向量方案给不了的精度。

依据：Jill Watson 绑定课程材料后准确率 78.7% vs 通用助手 30.7%。
因此所有生成必须经本模块约束在教材与项目材料内。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from packages.core.models import Citation, Message
from packages.graph import algo as graph_algo
from packages.graph import repo as graph_repo
from packages.llm import gateway

from . import store


@dataclass
class RetrievalResult(Message):
    query: str = ""
    kb: str = ""
    hits: list = field(default_factory=list)      # [{text, title, ref, score, kp_codes}]
    citations: list = field(default_factory=list)  # [Citation]
    expanded_kps: list = field(default_factory=list)

    def context_text(self, limit: int = 3) -> str:
        return "\n---\n".join(h["text"] for h in self.hits[:limit])


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def search(
    query: str,
    kb: str = "course",
    top_k: int = 5,
    kp_id: int | None = None,
    expand_prereq: bool = True,
) -> RetrievalResult:
    qvec = gateway.embed([query])[0].values
    chunks = store.load_chunks(kb)
    res = RetrievalResult(query=query, kb=kb)

    boost_codes: set[str] = set()
    if kp_id:
        kp = graph_repo.get_kp(kp_id)
        if kp:
            boost_codes.add(kp.code)
            res.expanded_kps.append(kp.code)
        if expand_prereq:
            for anc, _d in graph_algo.ancestors(kp_id, max_depth=2):
                a = graph_repo.get_kp(anc)
                if a:
                    boost_codes.add(a.code)
                    res.expanded_kps.append(a.code)

    scored = []
    for c in chunks:
        s = cosine(qvec, c["embedding"])
        # 图谱结构化加权：命中当前知识点或其前置的材料显著提权
        overlap = boost_codes & set(c["kp_codes"])
        if overlap:
            s += 0.35 + 0.05 * len(overlap)
        # 关键词兜底：确定性的字面命中，防止纯向量在短查询上失灵
        if query and any(w and w in c["text"] for w in _keywords(query)):
            s += 0.12
        scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    for s, c in scored[:top_k]:
        if s <= 0.02:
            continue
        res.hits.append(
            {"text": c["text"], "title": c["title"], "ref": c["ref"],
             "score": round(s, 4), "kp_codes": c["kp_codes"]}
        )
        res.citations.append(
            Citation(kb=kb, ref=c["ref"], title=c["title"], score=round(s, 4))
        )
    return res


def _keywords(q: str) -> list[str]:
    import re

    zh = re.sub(r"[^一-鿿]", "", q)
    grams = [zh[i: i + 3] for i in range(max(0, len(zh) - 2))]
    en = re.findall(r"[A-Za-z]{3,}", q)
    return grams[:8] + en[:4]


def route(intent: str) -> str:
    """按场景路由到对应知识库。"""
    return {
        "ask": "course",
        "quiz": "course",
        "review": "project",
        "task": "project",
        "copilot": "project",
        "credit_map": "program",
        "syllabus": "program",
        "teaching_plan": "program",
        "deck": "course",
    }.get(intent, "course")


def grounded_context(query: str, intent: str = "ask", kp_id: int | None = None,
                     top_k: int = 3) -> RetrievalResult:
    """生成前的强制约束步骤：拿不到材料就不许自由发挥。"""
    return search(query, kb=route(intent), top_k=top_k, kp_id=kp_id)
