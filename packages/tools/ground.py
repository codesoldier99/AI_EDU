"""落地性检查：这段生成文字，有多少是材料里真的有的。

铁律 3 说大模型输出不得作为事实来源。RAG 把材料塞进去只是**降低**了编造的概率，
并不能证明这一段没编。所以生成之后还要再量一次——用一个确定性的、
教师能自己复算的指标：n-gram 重合率。

它不聪明，但它有三个好处：不依赖模型、结果可复现、阈值可被教师调。
低于阈值的段落不会被删掉（删掉就看不见问题了），而是被显式标注"低支撑"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 中文按 n-gram，英文数字按词
_EN = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")


def _grams(text: str, n: int = 3) -> list[str]:
    zh = re.sub(r"[^一-鿿]", "", text or "")
    grams = [zh[i: i + n] for i in range(max(0, len(zh) - n + 1))]
    return grams + [w.lower() for w in _EN.findall(text or "")]


@dataclass
class Groundedness:
    ratio: float = 0.0
    n_total: int = 0
    n_hit: int = 0
    grounded: bool = False
    threshold: float = 0.0


def overlap_ratio(text: str, material: str, n: int = 3) -> float:
    """生成文字的 n-gram 有多大比例能在材料里找到。"""
    gs = _grams(text, n)
    if not gs:
        return 0.0
    pool = set(_grams(material, n))
    return round(sum(1 for g in gs if g in pool) / len(gs), 4)


def check(text: str, material: str, threshold: float = 0.35, n: int = 3) -> Groundedness:
    gs = _grams(text, n)
    pool = set(_grams(material, n))
    hit = sum(1 for g in gs if g in pool)
    ratio = round(hit / len(gs), 4) if gs else 0.0
    return Groundedness(ratio=ratio, n_total=len(gs), n_hit=hit,
                        grounded=ratio >= threshold, threshold=threshold)
