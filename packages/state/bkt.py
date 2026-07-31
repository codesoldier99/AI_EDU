"""贝叶斯知识追踪（BKT）标准四参数模型。

铁律 3：掌握度是算法依据真实行为产生的，不由大模型判断。
本文件是全系统唯一允许改变 p_mastery 数值的地方。
"""
from __future__ import annotations

from dataclasses import dataclass

from packages.core.config import CONFIG


@dataclass(frozen=True)
class BKTParams:
    p_init: float = 0.15
    p_transit: float = 0.20
    p_slip: float = 0.10
    p_guess: float = 0.20

    @staticmethod
    def default() -> "BKTParams":
        d = CONFIG.teaching.bkt_default
        return BKTParams(d["p_init"], d["p_transit"], d["p_slip"], d["p_guess"])

    def sane(self) -> bool:
        return (
            0 <= self.p_init <= 1
            and 0 <= self.p_transit <= 1
            and 0 <= self.p_slip < 0.5
            and 0 <= self.p_guess < 0.5
        )


def posterior(p_known: float, correct: bool, prm: BKTParams) -> float:
    """观测到一次作答后的后验。"""
    if correct:
        num = p_known * (1 - prm.p_slip)
        den = num + (1 - p_known) * prm.p_guess
    else:
        num = p_known * prm.p_slip
        den = num + (1 - p_known) * (1 - prm.p_guess)
    if den <= 1e-12:
        return p_known
    return num / den


def update(p_known: float, correct: bool, prm: BKTParams) -> float:
    """后验 + 学习转移。返回新的掌握概率。"""
    post = posterior(p_known, correct, prm)
    return min(1.0, post + (1 - post) * prm.p_transit)


def update_with_weight(p_known: float, correct: bool, prm: BKTParams, weight: float) -> float:
    """带证据权重的更新。

    weight ∈ (0,1]：不同来源的证据可信度不同——课堂测验 1.0，代码审查推断 0.5。
    实现方式是在原值与完整更新值之间线性插值，保持单调性与 [0,1] 闭合。
    """
    w = max(0.0, min(1.0, weight))
    full = update(p_known, correct, prm)
    return p_known + w * (full - p_known)


def fit_params(observations: list[bool], prm: BKTParams, min_n: int = 200) -> BKTParams:
    """朴素参数拟合：样本足够时用经验正确率反推 slip/guess 的粗略估计。

    DEVELOPMENT_PLAN §1.2：单知识点作答满 200 次后才考虑拟合，否则原样返回。
    """
    n = len(observations)
    if n < min_n:
        return prm
    acc = sum(1 for o in observations if o) / n
    # 经验规则：整体正确率高 -> slip 小；正确率极低 -> guess 小
    slip = max(0.02, min(0.30, (1 - acc) * 0.35))
    guess = max(0.05, min(0.40, acc * 0.30))
    fitted = BKTParams(prm.p_init, prm.p_transit, slip, guess)
    return fitted if fitted.sane() else prm
