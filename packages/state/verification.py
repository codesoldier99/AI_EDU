"""掌握的**质量**：它是真的吗，它还在吗，它是自己挣来的吗。

这一层回答的不是"会不会"（那是 bkt.py 的事），而是三个更难的问题：

1. **验证了吗** —— 一次做对不算掌握。只有在**间隔若干天之后再次做对**，
   才算"已验证掌握"。对应 LearnVector 的 "stays with you until you've
   mastered new skills **and can prove it**"。
2. **还在吗** —— 掌握度会随时间衰减。到期未复检的知识点要主动叫回来复习。
3. **是自己挣来的吗** —— 在提示/降级之后做对，与无提示做对，不是一回事。
   若不区分，系统会把"认知外包"误记为"学会了"。

三者全部是**派生视图**：只读事件流，不修改 p_mastery。
掌握度必须始终等于事件流折叠的结果，否则 `make replay` 失去意义（铁律）。
衰减若真的写回库里，重算就对不上——所以这里只算不写。

关于第 3 点的一条硬约束：提示依赖度是**诊断指标，禁止作为优化目标或激励对象**
（铁律 5）。它用来提醒教师"这个学生的通过率是借来的"，不用来排名或扣分。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.db import get_db
from packages.core.models import Message
from packages.core.timeutil import days_between, now_str, parse

from . import repo
from .tracker import SCORING_EVENTS


# ---------------------------------------------------------------- 衰减
def decay_factor(days: float, halflife: float | None = None) -> float:
    """遗忘曲线：半衰期形式。days 天之后还剩多少。

    刻意用最简单、可解释的一条曲线，而不是拟合出来的复杂函数——
    教师要能一眼看懂"为什么系统说他该复习了"。
    """
    hl = halflife or CONFIG.teaching.retention_halflife_days
    return 0.5 ** (max(0.0, days) / max(hl, 1e-6))


@dataclass
class KPQuality(Message):
    kp_id: int = 0
    code: str = ""
    name: str = ""
    unit: str = ""
    p_mastery: float = 0.0
    retained: float = 0.0          # 计入遗忘后的当前估计
    days_since: float = 0.0
    validated: bool = False        # 是否已跨时间验证
    validated_gap_days: float = 0.0
    due: bool = False              # 是否到期需要复检
    evidence_count: int = 0
    unaided: bool = True           # 最近一次做对是否无提示


@dataclass
class VerificationView(Message):
    student_id: int = 0
    items: list = field(default_factory=list)
    n_mastered: int = 0
    n_validated: int = 0
    n_due: int = 0
    validated_rate: float = 0.0
    confidence: float = 0.0
    evidence_count: int = 0
    caveat: str = ""


# ---------------------------------------------------------------- 验证掌握
def _correct_timeline(student_id: int) -> dict[int, list[dict]]:
    """每个知识点上的作答时间线（只取参与掌握度判定的事件）。"""
    out: dict[int, list[dict]] = {}
    for ev in repo.list_events(student_id=student_id, order="id"):
        if ev["kp_id"] is None or ev["is_correct"] is None:
            continue
        if ev["event_type"] not in SCORING_EVENTS:
            continue
        out.setdefault(ev["kp_id"], []).append(ev)
    return out


def _escalated_kps(student_id: int) -> dict[int, list[str]]:
    """该学生在哪些知识点上触发过降级，以及降级发生的时间。"""
    out: dict[int, list[str]] = {}
    for ev in repo.list_events(student_id=student_id, event_type="escalation", order="id"):
        if ev["kp_id"] is not None:
            out.setdefault(ev["kp_id"], []).append(ev["occurred_at"])
    return out


def is_validated(events: list[dict], gap_days: float | None = None) -> tuple[bool, float]:
    """跨时间验证：两次做对之间相隔 >= gap_days 才算数。

    返回 (是否已验证, 实际间隔天数)。
    这条规则挡住的是"当堂反复练直到做对"——那证明的是短时记忆，不是掌握。
    """
    gap = gap_days if gap_days is not None else CONFIG.teaching.verify_gap_days
    corrects = [e for e in events if e["is_correct"]]
    if len(corrects) < 2:
        return False, 0.0
    best = 0.0
    first = corrects[0]["occurred_at"]
    for e in corrects[1:]:
        d = days_between(first, e["occurred_at"])
        best = max(best, d)
        if d >= gap:
            return True, round(d, 2)
    return False, round(best, 2)


def _last_unaided(events: list[dict], escalations: list[str]) -> bool:
    """最近一次做对，是否发生在该知识点最近一次降级之前（即无提示）。"""
    corrects = [e for e in events if e["is_correct"]]
    if not corrects:
        return True
    last_ok = corrects[-1]["occurred_at"]
    return not any(
        (p := parse(e)) and (q := parse(last_ok)) and p <= q for e in escalations
    )


# ---------------------------------------------------------------- 视图
def build(student_id: int) -> VerificationView:
    t = CONFIG.teaching
    rows = repo.mastery_rows(student_id)
    timeline = _correct_timeline(student_id)
    escalations = _escalated_kps(student_id)
    now = now_str()

    items: list[KPQuality] = []
    for r in rows:
        evs = timeline.get(r["kp_id"], [])
        last_at = evs[-1]["occurred_at"] if evs else None
        days = days_between(last_at, now) if last_at else 0.0
        retained = round(r["p_mastery"] * decay_factor(days), 4)
        validated, gap = is_validated(evs)
        mastered = r["p_mastery"] >= t.mastery_threshold
        # 到期复检：曾经达标，但按遗忘曲线已经掉到复检线以下
        due = mastered and retained < t.retention_threshold
        items.append(KPQuality(
            kp_id=r["kp_id"], code=r["code"], name=r["name"], unit=r["unit"],
            p_mastery=round(r["p_mastery"], 4), retained=retained,
            days_since=round(days, 1), validated=validated, validated_gap_days=gap,
            due=due, evidence_count=r["evidence_count"],
            unaided=_last_unaided(evs, escalations.get(r["kp_id"], [])),
        ))

    mastered = [i for i in items if i.p_mastery >= t.mastery_threshold]
    validated = [i for i in mastered if i.validated]
    due = [i for i in items if i.due]
    evid = sum(i.evidence_count for i in items)
    from packages.core.models import confidence_from_evidence

    v = VerificationView(
        student_id=student_id,
        items=[i.to_dict() for i in items],
        n_mastered=len(mastered),
        n_validated=len(validated),
        n_due=len(due),
        validated_rate=round(len(validated) / len(mastered), 4) if mastered else 0.0,
        confidence=confidence_from_evidence(evid // max(1, len(items) or 1)),
        evidence_count=evid,
    )
    v.caveat = "" if evid >= t.min_evidence_for_report else "数据不足，仅供参考"
    return v


def due_reviews(student_id: int, limit: int = 5) -> list[dict]:
    """该复习了：按"掉得最多 × 挡路度最高"排序。

    这是"陪你到底"的具体形态——学过不等于学会，达标不等于还记得。
    """
    from packages.graph import algo as graph_algo

    v = build(student_id)
    out = []
    for i in v.items:
        if not i["due"]:
            continue
        drop = i["p_mastery"] - i["retained"]
        sev = graph_algo.blocking_severity(i["kp_id"])
        out.append({**i, "drop": round(drop, 4), "blocking_severity": sev,
                    "priority": round(drop * (1 + sev / 10.0), 4)})
    out.sort(key=lambda x: -x["priority"])
    return out[:limit]


# ---------------------------------------------------------------- 提示依赖度
@dataclass
class Independence(Message):
    student_id: int = 0
    n_correct: int = 0
    n_unaided_correct: int = 0
    unaided_rate: float = 0.0
    n_escalated_kps: int = 0
    n_escalations: int = 0
    aided_kps: list = field(default_factory=list)
    note: str = ("诊断指标，用于提醒教师某些通过是借来的；"
                 "禁止作为优化目标、激励对象或排名依据")


def independence(student_id: int) -> Independence:
    """认知外包的观测：有多少"做对"是在提示之后发生的。

    依据：无护栏的 AI 辅助会让学生把思考外包出去，作业更好看、人更不会。
    我们无法禁止学生求助（也不该禁止），但必须**看得见**这件事，
    否则系统会把外包出来的正确率当成学习成效，从而奖励错误的行为。
    """
    timeline = _correct_timeline(student_id)
    escalations = _escalated_kps(student_id)
    n_correct = n_unaided = 0
    aided: list[dict] = []
    from packages.graph import repo as graph_repo

    for kp_id, evs in timeline.items():
        esc = escalations.get(kp_id, [])
        esc_times = [p for e in esc if (p := parse(e))]
        for e in evs:
            if not e["is_correct"]:
                continue
            n_correct += 1
            at = parse(e["occurred_at"])
            if at and any(p <= at for p in esc_times):
                continue  # 该知识点在此之前已经给过提示
            n_unaided += 1
        if esc_times:
            kp = graph_repo.get_kp(kp_id)
            aided.append({"kp_id": kp_id, "name": kp.name if kp else "",
                          "escalations": len(esc_times)})
    aided.sort(key=lambda x: -x["escalations"])
    return Independence(
        student_id=student_id,
        n_correct=n_correct,
        n_unaided_correct=n_unaided,
        unaided_rate=round(n_unaided / n_correct, 4) if n_correct else 0.0,
        n_escalated_kps=len(aided),
        n_escalations=sum(a["escalations"] for a in aided),
        aided_kps=aided[:8],
    )


# ---------------------------------------------------------------- 学法适配
def style_effectiveness(student_id: int) -> dict:
    """哪种追问方式对**这个**学生真的有用。

    "Adapts to how you learn" 若只停留在难度自适应，就还是千人一面的另一种形态。
    真正的适配要求系统记住：对他而言，反例质疑管用，类比迁移不管用。

    统计口径：一次会话采用某种方式后，紧随其后的作答是否被判定为有进展。
    判定本身走确定性规则（AskingAgent._detect_progress），不是模型说了算。
    """
    rows = get_db().query(
        "SELECT s.style, e.is_correct FROM asking_session s"
        " JOIN learning_event e ON e.source_ref = 'session:' || s.id"
        " WHERE s.student_id=? AND e.event_type='ask_turn' AND e.is_correct IS NOT NULL",
        (student_id,),
    )
    agg: dict[str, dict] = {}
    for r in rows:
        st = r["style"] or "未记录"
        a = agg.setdefault(st, {"tried": 0, "progressed": 0})
        a["tried"] += 1
        a["progressed"] += int(bool(r["is_correct"]))
    for st, a in agg.items():
        a["rate"] = round(a["progressed"] / a["tried"], 4) if a["tried"] else 0.0
    return agg


def preferred_styles(student_id: int, min_tries: int = 3) -> list[str]:
    """按实测有效率给出该学生的追问方式偏好；证据不足时返回空表（不猜）。"""
    agg = style_effectiveness(student_id)
    ranked = [(s, a) for s, a in agg.items() if a["tried"] >= min_tries]
    ranked.sort(key=lambda x: -x[1]["rate"])
    return [s for s, _ in ranked]


# ---------------------------------------------------------------- 班级视图
def class_verification(course_id: int, klass: str | None = None) -> dict:
    """班级层面：看起来会了、但没验证过的比例有多高。"""
    students = repo.list_students(klass)
    rows, tot_m, tot_v, tot_due = [], 0, 0, 0
    for s in students:
        v = build(s["id"])
        ind = independence(s["id"])
        tot_m += v.n_mastered
        tot_v += v.n_validated
        tot_due += v.n_due
        rows.append({
            "student_id": s["id"], "sid": s["sid"], "name": s["name"],
            "n_mastered": v.n_mastered, "n_validated": v.n_validated,
            "n_due": v.n_due, "validated_rate": v.validated_rate,
            "unaided_rate": ind.unaided_rate, "n_escalations": ind.n_escalations,
        })
    rows.sort(key=lambda r: (r["validated_rate"], r["unaided_rate"]))
    return {
        "n_students": len(students),
        "n_mastered": tot_m,
        "n_validated": tot_v,
        "n_due": tot_due,
        "validated_rate": round(tot_v / tot_m, 4) if tot_m else 0.0,
        "rows": rows,
        "note": "「已验证掌握」= 跨时间再次做对；提示依赖度仅作诊断，不用于评价学生",
    }
