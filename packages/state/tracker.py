"""知识追踪器：系统中唯一被允许改变学生状态的通道。

调用约定（铁律 1）：
    L3 生成层 -> tracker.record(...) -> 写事件 -> BKT 更新 -> 写掌握度
生成层不得绕过本模块直接写 mastery_state；DB 触发器与 tests/test_layering.py 双重把关。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.models import Message, confidence_from_evidence
from packages.core.timeutil import days_between, now_str
from packages.graph import repo as graph_repo

from . import repo
from .bkt import BKTParams, update_with_weight

# 不同来源的证据权重：越间接的证据权重越低（宁可慢，不可错）
SOURCE_WEIGHT = {
    "exam": 1.0,
    "teacher": 1.0,      # 教师人工判分：判定最确定，权重最高
    "quiz": 1.0,
    "homework": 0.9,
    "lab": 0.9,
    "practice": 0.7,
    "ask": 0.5,          # 追问过程中的作答
    "review": 0.5,       # 代码审查推断出的知识点问题
    "project": 0.4,      # 项目运行信号推断
}

# 只有这些事件类型携带对错判定，才参与掌握度更新
SCORING_EVENTS = {"quiz", "homework", "exam", "practice", "lab", "review_finding", "ask_turn"}


@dataclass
class TrackResult(Message):
    event_id: int = 0
    student_id: int = 0
    kp_id: int = 0
    p_before: float = 0.0
    p_after: float = 0.0
    confidence: float = 0.0
    evidence_count: int = 0
    updated: bool = False
    note: str = ""


def record(
    student_id: int,
    event_type: str,
    kp_id: int | None = None,
    is_correct: bool | None = None,
    source: str = "",
    source_ref: str = "",
    occurred_at: str | None = None,
    payload: dict | None = None,
) -> TrackResult:
    """记录一次学习行为，并在其携带对错判定时更新掌握度。"""
    event_id = repo.append_event(
        student_id=student_id,
        event_type=event_type,
        kp_id=kp_id,
        is_correct=is_correct,
        source=source,
        source_ref=source_ref,
        occurred_at=occurred_at,
        payload=payload,
    )
    res = TrackResult(event_id=event_id, student_id=student_id, kp_id=kp_id or 0)
    if kp_id is None or is_correct is None or event_type not in SCORING_EVENTS:
        res.note = "行为事件，不参与掌握度更新"
        return res

    prm = repo.get_params(kp_id)
    cur = repo.get_mastery(student_id, kp_id)
    p_before = cur["p_mastery"] if cur else prm.p_init
    n = (cur["evidence_count"] if cur else 0) + 1
    weight = SOURCE_WEIGHT.get(source or event_type, 0.6)
    # 6 位精度是掌握度的**唯一规范表示**：写库与 replay 必须用同一个精度，
    # 否则一致性校验会在浮点末位上产生假阳性（见 packages/state/replay.py）。
    p_after = round(update_with_weight(p_before, bool(is_correct), prm, weight), 6)
    conf = confidence_from_evidence(n, 0.0)
    repo.write_mastery(student_id, kp_id, p_after, conf, n, event_id)
    res.p_before, res.p_after = round(p_before, 6), p_after
    res.confidence, res.evidence_count, res.updated = conf, n, True
    return res


def record_batch(items: list[dict]) -> list[TrackResult]:
    out = []
    for it in items:
        out.append(record(**it))
    return out


def refresh_confidence(student_id: int) -> int:
    """按证据新鲜度刷新置信度（不改 p_mastery，因此不需要新事件）。"""
    rows = repo.mastery_rows(student_id)
    now = now_str()
    n = 0
    for r in rows:
        ev = repo.get_event(r["last_event_id"]) if r["last_event_id"] else None
        days = days_between(ev["occurred_at"], now) if ev else 0.0
        conf = confidence_from_evidence(r["evidence_count"], days)
        if abs(conf - r["confidence"]) > 1e-6:
            repo.write_mastery(
                student_id, r["kp_id"], r["p_mastery"], conf, r["evidence_count"],
                r["last_event_id"],
            )
            n += 1
    return n


def recompute_ability(student_id: int) -> list[dict]:
    """能力画像 = 掌握度按 KP-能力模块权重加权聚合。

    这是派生量，可随时重算；不是独立的事实来源。
    """
    weights = graph_repo.module_weight_map()
    mastery = repo.mastery_rows(student_id)
    acc: dict[str, list[tuple[float, float, int]]] = {}
    for r in mastery:
        for code, w in weights.get(r["kp_id"], {}).items():
            acc.setdefault(code, []).append((r["p_mastery"], w, r["evidence_count"]))
    out = []
    for m in graph_repo.list_modules():
        items = acc.get(m.code, [])
        if items:
            tot_w = sum(w for _, w, _ in items)
            level = sum(p * w for p, w, _ in items) / tot_w if tot_w else 0.0
            evid = sum(n for _, _, n in items)
        else:
            level, evid = 0.0, 0
        conf = confidence_from_evidence(evid)
        repo.write_ability(student_id, m.id, round(level, 4), conf)
        out.append({"code": m.code, "name": m.name, "level": round(level, 4),
                    "confidence": conf, "evidence_count": evid})
    return out


def is_mastered(student_id: int, kp_id: int) -> bool:
    m = repo.get_mastery(student_id, kp_id)
    return bool(m and m["p_mastery"] >= CONFIG.teaching.mastery_threshold)
