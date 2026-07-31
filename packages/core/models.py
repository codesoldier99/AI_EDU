"""结构化消息基类（替代 pydantic，零依赖）。

铁律：智能体之间只传结构化消息，禁止自由对话；所有对外 API 必须带 confidence
与 evidence_count，禁止裸返回结论。`Evidenced` 基类强制了后者。
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, get_args, get_origin

from .config import CONFIG


def _coerce(value: Any, typ: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(typ)
    if origin in (list, tuple):
        (inner,) = get_args(typ) or (Any,)
        return [_coerce(v, inner) for v in value]
    if origin is dict:
        return dict(value)
    if is_dataclass(typ) and isinstance(value, dict):
        return typ.from_dict(value) if hasattr(typ, "from_dict") else typ(**value)
    if typ in (int, float, str, bool) and not isinstance(value, typ):
        try:
            return typ(value)
        except Exception:
            return value
    return value


@dataclass
class Message:
    """所有跨层/跨智能体消息的基类。"""

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        kwargs = {}
        for f in fields(cls):
            if f.name in data:
                kwargs[f.name] = _coerce(data[f.name], f.type)
        return cls(**kwargs)

    def to_dict(self) -> dict:
        def enc(v: Any) -> Any:
            if is_dataclass(v):
                return {k: enc(x) for k, x in dataclasses.asdict(v).items()}
            if isinstance(v, list):
                return [enc(x) for x in v]
            if isinstance(v, dict):
                return {k: enc(x) for k, x in v.items()}
            return v

        return {f.name: enc(getattr(self, f.name)) for f in fields(self)}


@dataclass
class Evidenced(Message):
    """带证据的输出。铁律 4：不确定必须显式表达。"""

    confidence: float = 0.0
    evidence_count: int = 0

    @property
    def data_sufficient(self) -> bool:
        return self.evidence_count >= CONFIG.teaching.min_evidence_for_report

    @property
    def caveat(self) -> str:
        return "" if self.data_sufficient else "数据不足，仅供参考"


def confidence_from_evidence(n: int, days_since_last: float = 0.0) -> float:
    """由证据条数与新鲜度计算置信度。

    n 条证据 -> 1 - exp(-n/k)，再乘以按半衰期衰减的新鲜度因子。
    这是一个显式、可解释、可被教师质疑的公式，不是模型给的黑箱数字。
    """
    t = CONFIG.teaching
    if n <= 0:
        return 0.0
    base = 1.0 - math.exp(-n / max(t.confidence_saturation, 1e-6))
    fresh = 0.5 ** (max(days_since_last, 0.0) / max(t.confidence_halflife_days, 1e-6))
    return round(max(0.0, min(1.0, base * (0.4 + 0.6 * fresh))), 4)


@dataclass
class Citation(Message):
    """生成层输出的来源引用。"""

    kb: str = ""
    ref: str = ""
    title: str = ""
    score: float = 0.0
