"""确定性判分。

铁律 3：成绩判定必须走确定性工具，模型只做解释与表达。
所以这里没有任何模型调用——判不了的题不猜，返回 `is_correct=None` 进人工队列
（铁律 4：不确定必须显式表达）。

判不了 ≠ 判错。把"判不了"记成"判错"会污染 BKT 的证据流，
而 BKT 的证据流是本系统最贵的东西。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.models import Message
from packages.tools import calc

CHOICE_LETTERS = "ABCDEFGH"


@dataclass
class GradeResult(Message):
    question_id: int = 0
    is_correct: bool | None = None
    graded_by: str = "manual"        # rule:choice | rule:numeric | rule:keyword | pending_teacher
    confidence: float = 0.0
    detail: str = ""
    hits: list = field(default_factory=list)
    misses: list = field(default_factory=list)

    @property
    def pending(self) -> bool:
        return self.is_correct is None


# ---------------------------------------------------------------- 选择题
def _choice_set(text: str, options: list) -> set[str]:
    """把作答归一成选项字母集合。支持 'B' / 'b' / '2' / 选项原文。"""
    t = (text or "").strip()
    if not t:
        return set()
    out: set[str] = set()
    for ch in re.findall(r"[A-Ha-h]", t):
        out.add(ch.upper())
    for num in re.findall(r"\d+", t):
        i = int(num)
        if 1 <= i <= len(options):
            out.add(CHOICE_LETTERS[i - 1])
    if not out and options:
        norm = re.sub(r"\s", "", t)
        for i, opt in enumerate(options):
            body = re.sub(r"^[A-Ha-h][\.、\)]\s*", "", str(opt))
            if re.sub(r"\s", "", body) and re.sub(r"\s", "", body) in norm:
                out.add(CHOICE_LETTERS[i])
    return out


def grade_choice(response: str, answer: str, options: list) -> GradeResult:
    want = _choice_set(answer, options)
    got = _choice_set(response, options)
    if not want:
        return GradeResult(graded_by="pending_teacher", detail="标准答案无法解析为选项")
    if not got:
        return GradeResult(graded_by="pending_teacher", detail="作答无法解析为选项，需人工判定")
    ok = got == want
    return GradeResult(
        is_correct=ok, graded_by="rule:choice", confidence=1.0,
        detail=f"作答 {''.join(sorted(got))}，标准 {''.join(sorted(want))}",
    )


# ---------------------------------------------------------------- 数值题
def grade_numeric(response: str, answer: str, tolerance: float) -> GradeResult:
    chk = calc.check_numeric(response, answer, tolerance)
    if chk.got is None:
        return GradeResult(graded_by="pending_teacher", detail=chk.note or "无法取得数值作答")
    return GradeResult(
        is_correct=chk.ok, graded_by="rule:numeric", confidence=1.0,
        detail=(f"作答 {chk.got:g}，标准 {chk.expected:g}"
                + (f"，相对误差 {chk.rel_error:.4g}" if chk.rel_error is not None else "")),
    )


# ---------------------------------------------------------------- 主观题
def grade_keyword(response: str, keywords: list) -> GradeResult:
    """关键词覆盖：确定性，但**不自负**。

    每个关键词可写成 "反向传播|BP|链式" 表示同义任一命中。
    命中率落在两条线中间时不判，交给教师——这一步的克制换来的是证据流的干净。
    """
    kws = [str(k) for k in (keywords or []) if str(k).strip()]
    text = re.sub(r"\s", "", (response or "")).lower()
    if not kws:
        return GradeResult(graded_by="pending_teacher", detail="该题未配置关键词，需人工判定")
    if len(text) < 4:
        return GradeResult(is_correct=False, graded_by="rule:keyword", confidence=0.9,
                           detail="作答过短", misses=kws)
    hits, misses = [], []
    for k in kws:
        alts = [re.sub(r"\s", "", a).lower() for a in k.split("|") if a.strip()]
        (hits if any(a and a in text for a in alts) else misses).append(k)
    # 两条判定线均为配置项（decisions.md #17/#18）；带间区间一律交给人
    rate = len(hits) / len(kws)
    if rate >= CONFIG.teaching.keyword_pass_ratio:
        return GradeResult(is_correct=True, graded_by="rule:keyword",
                           confidence=round(rate, 4), hits=hits, misses=misses,
                           detail=f"关键词覆盖 {len(hits)}/{len(kws)}")
    if rate <= CONFIG.teaching.keyword_fail_ratio:
        return GradeResult(is_correct=False, graded_by="rule:keyword",
                           confidence=round(1.0 - rate, 4), hits=hits, misses=misses,
                           detail=f"关键词覆盖 {len(hits)}/{len(kws)}，主要缺口：{'、'.join(misses[:3])}")
    return GradeResult(graded_by="pending_teacher", confidence=round(rate, 4),
                       hits=hits, misses=misses,
                       detail=f"关键词覆盖 {len(hits)}/{len(kws)}，落在判定带间，需人工确认")


# ---------------------------------------------------------------- 入口
def grade(question, response: str) -> GradeResult:
    """按题目配置的判分器判分。question 为 packages.quiz.bank.Question。"""
    g = getattr(question, "grader", "manual")
    if g == "choice":
        r = grade_choice(response, question.answer, question.options)
    elif g == "numeric":
        r = grade_numeric(response, question.answer, question.tolerance)
    elif g == "keyword":
        r = grade_keyword(response, question.keywords)
    else:
        r = GradeResult(graded_by="pending_teacher", detail="该题配置为人工判分")
    r.question_id = getattr(question, "id", 0)
    return r
