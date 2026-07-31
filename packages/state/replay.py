"""从事件流重算状态并校验一致性。

规范顺序的定义：状态是事件流按**记录顺序（自增 id）**折叠出来的结果。
occurred_at 是业务时间元数据，可能被补录、可能乱序；用它折叠会得到与线上不同的值。
只有把"记录顺序"钉死为唯一规范顺序，重算才是可判定的。

Phase 0 验收标准：`make replay STUDENT=<id>` 从事件流完整重算掌握度与 engagement，
与当前值一致。这是"可被第三方核查"的技术保证（对应 Alpha School 的教训）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.models import Message, confidence_from_evidence

from . import repo
from .bkt import update_with_weight
from .tracker import SCORING_EVENTS, SOURCE_WEIGHT


@dataclass
class ReplayDiff(Message):
    student_id: int = 0
    n_events: int = 0
    n_kps: int = 0
    mismatches: list = field(default_factory=list)
    engagement_match: bool = True
    engagement_detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.mismatches and self.engagement_match


def recompute_mastery(student_id: int) -> dict[int, dict]:
    """纯函数式重算：只读事件流，不落库。"""
    params = repo.all_params()
    from .bkt import BKTParams

    default = BKTParams.default()
    state: dict[int, dict] = {}
    for ev in repo.list_events(student_id=student_id, order="id"):
        if ev["kp_id"] is None or ev["is_correct"] is None:
            continue
        if ev["event_type"] not in SCORING_EVENTS:
            continue
        kp = ev["kp_id"]
        prm = params.get(kp, default)
        st = state.setdefault(
            kp, {"p": prm.p_init, "n": 0, "last_event_id": None}
        )
        w = SOURCE_WEIGHT.get(ev["source"] or ev["event_type"], 0.6)
        # 与 tracker 写库时的精度保持一致：每一步都取 6 位。
        # 否则 replay 用全精度累积、线上用截断值累积，几十条事件后会在 1e-6 处分叉，
        # 一致性校验就会给出假阳性。
        st["p"] = round(update_with_weight(st["p"], bool(ev["is_correct"]), prm, w), 6)
        st["n"] += 1
        st["last_event_id"] = ev["id"]
    for kp, st in state.items():
        st["confidence"] = confidence_from_evidence(st["n"], 0.0)
    return state


def verify(student_id: int, tol: float = 1e-6) -> ReplayDiff:
    """重算并与库中当前值逐条比对。

    注意：本函数只校验 L2 掌握度。engagement 的重算校验由
    `packages.engagement.service.verify_streak` 提供，两者在脚本层组合，
    以免 state 反向依赖 engagement（依赖方向必须单向）。
    """
    recomputed = recompute_mastery(student_id)
    current = {r["kp_id"]: r for r in repo.mastery_rows(student_id)}
    diff = ReplayDiff(
        student_id=student_id,
        n_events=len(repo.list_events(student_id=student_id, order="id")),
        n_kps=len(recomputed),
    )
    for kp, st in recomputed.items():
        cur = current.get(kp)
        if cur is None:
            diff.mismatches.append({"kp_id": kp, "reason": "库中缺失", "replay": st["p"]})
            continue
        if abs(cur["p_mastery"] - st["p"]) > tol:
            diff.mismatches.append(
                {"kp_id": kp, "reason": "掌握度不一致",
                 "stored": cur["p_mastery"], "replay": st["p"]}
            )
        elif cur["evidence_count"] != st["n"]:
            diff.mismatches.append(
                {"kp_id": kp, "reason": "证据数不一致",
                 "stored": cur["evidence_count"], "replay": st["n"]}
            )
    for kp in current:
        if kp not in recomputed:
            diff.mismatches.append({"kp_id": kp, "reason": "无事件支撑的掌握度（可疑写入）"})

    return diff


def rebuild(student_id: int) -> int:
    """按事件流重建掌握度（用于灾后恢复或参数变更后的整体重算）。"""
    recomputed = recompute_mastery(student_id)
    repo.clear_derived_state(student_id)
    for kp, st in recomputed.items():
        repo.write_mastery(
            student_id, kp, st["p"], st["confidence"], st["n"], st["last_event_id"]
        )
    return len(recomputed)
