"""确定性选题：先决定"该练哪个知识点"，再决定"发哪道题"。

DeepTutor 的 Mastery Path 把"练什么"交给同一个 agent loop 去想；
我们把它拆成两段——**练什么是算出来的，题面才是写出来的**。
好处很直接：教师可以质疑排序，学生可以看见理由，出了问题能复算。

排序的四个来源（PRIORITY_KINDS），刻意都不是"正确率"：
    retention   到期复检——学过的东西掉下去了，掉得最多且最挡路的先叫回来
    verify      暂定掌握但从未跨时间再做对——正是"证明给我看"的时刻
    gap         当前任务缺口——拉取式的本分
    root_cause  根因点——错在表层，病在上游

铁律 5：禁止以当场正确率、做题数、停留时长作为优化目标。
本模块的排序依据里没有任何一个是它们，这条由 tests/test_layering.py 静态盯着。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.models import Message, confidence_from_evidence
from packages.core.timeutil import days_between, now_str
from packages.graph import algo as graph_algo
from packages.graph import repo as graph_repo
from packages.state import repo as state_repo
from packages.state import verification

from . import bank

# 四类可作为练习理由的来源。新增一类必须同时说明它为什么不是"刷正确率"。
PRIORITY_KINDS = ("retention", "verify", "gap", "root_cause")

KIND_LABEL = {
    "retention": "到期复检",
    "verify": "待跨时间验证",
    "gap": "任务缺口",
    "root_cause": "根因点",
}
# 同一知识点被多个来源命中时取最高档；档位差体现"先救哪个"
KIND_BASE = {"retention": 3.0, "verify": 2.4, "gap": 2.0, "root_cause": 2.8}


@dataclass
class PracticeTarget(Message):
    kp_id: int = 0
    code: str = ""
    name: str = ""
    kind: str = "gap"
    kind_label: str = ""
    priority: float = 0.0
    p_mastery: float = 0.0
    blocking_severity: float = 0.0
    reason: str = ""


@dataclass
class PracticePlan(Message):
    student_id: int = 0
    task_id: int | None = None
    targets: list = field(default_factory=list)
    confidence: float = 0.0
    evidence_count: int = 0
    caveat: str = ""
    note: str = ("练习目标由复检队列 / 待验证 / 任务缺口 / 根因四类算出，"
                 "与正确率、做题数无关")


# ---------------------------------------------------------------- 目标选择
def practice_plan(student_id: int, task_id: int | None = None,
                  limit: int | None = None) -> PracticePlan:
    """算出此刻最该练的几个知识点，每个都带可被质疑的理由。"""
    t = CONFIG.teaching
    limit = limit or max(1, t.gap_push_limit * 2)
    mastery = state_repo.mastery_vector(student_id)
    picked: dict[int, PracticeTarget] = {}

    def offer(kp_id: int, kind: str, bonus: float, reason: str) -> None:
        sev = graph_algo.blocking_severity(kp_id)
        pri = round(KIND_BASE[kind] + bonus + sev / 10.0, 4)
        cur = picked.get(kp_id)
        if cur and cur.priority >= pri:
            return
        kp = graph_repo.get_kp(kp_id)
        picked[kp_id] = PracticeTarget(
            kp_id=kp_id, code=kp.code if kp else "", name=kp.name if kp else "",
            kind=kind, kind_label=KIND_LABEL[kind], priority=pri,
            p_mastery=round(mastery.get(kp_id, 0.0), 4), blocking_severity=sev,
            reason=reason,
        )

    # 1) 到期复检：掌握度按遗忘曲线掉到复检线以下
    for d in verification.due_reviews(student_id, limit=limit * 2):
        offer(d["kp_id"], "retention", d["drop"],
              f"{d['days_since']:.0f} 天没碰，估计已从 {d['p_mastery']:.2f} 掉到 "
              f"{d['retained']:.2f}，该叫回来确认一次")

    # 2) 待跨时间验证：达标了但从没在隔了几天之后再做对过
    for q in _pending_verification(student_id):
        offer(q["kp_id"], "verify", 0.0,
              f"掌握度 {q['p_mastery']:.2f} 已达标，但只在当堂做对过；"
              f"距上次 {q['days_since']:.0f} 天，正好隔期再验一次")

    # 3) 任务缺口：拉取式的本分。没指定任务时看他手上还没做完的那些。
    task_ids = [task_id] if task_id else graph_repo.assigned_task_ids(student_id)[:3]
    for tid in task_ids:
        task = graph_repo.get_task(tid)
        label = f"任务「{task.name}」" if task else "当前任务"
        for g in _task_gap(tid, mastery, t.mastery_threshold):
            offer(g["kp_id"], "gap", 0.0,
                  f"{label}需要它，掌握度 {g['p_mastery']:.2f} < {t.mastery_threshold}")

    # 4) 根因点：把表层的错往上游追一层
    for kp_id in list(picked):
        rc = graph_algo.trace_root_cause(kp_id, mastery, t.mastery_threshold)
        if not rc["is_self"] and rc["root_kp_id"] != kp_id:
            offer(rc["root_kp_id"], "root_cause", 0.0,
                  f"「{picked[kp_id].name}」的上游薄弱点，先补这里更省力")

    targets = sorted(picked.values(), key=lambda x: -x.priority)[:limit]
    evid = sum(
        (state_repo.get_mastery(student_id, x.kp_id) or {}).get("evidence_count", 0)
        for x in targets
    )
    plan = PracticePlan(
        student_id=student_id, task_id=task_id,
        targets=[x.to_dict() for x in targets],
        confidence=confidence_from_evidence(evid // max(1, len(targets) or 1)),
        evidence_count=evid,
    )
    plan.caveat = "" if evid >= t.min_evidence_for_report else "数据不足，仅供参考"
    return plan


def _pending_verification(student_id: int) -> list[dict]:
    """达标但未跨时间验证，且已经过了验证间隔——此刻叫回来最有信息量。"""
    t = CONFIG.teaching
    v = verification.build(student_id)
    out = []
    for i in v.items:
        if i["p_mastery"] < t.mastery_threshold or i["validated"]:
            continue
        if i["days_since"] < t.verify_gap_days:
            continue   # 还没到间隔，现在再做对也只证明短时记忆
        out.append(i)
    out.sort(key=lambda x: -x["days_since"])
    return out


def _task_gap(task_id: int, mastery: dict[int, float], thr: float) -> list[dict]:
    out = []
    for r in graph_repo.required_kps(task_id):
        p = mastery.get(r["kp_id"], 0.0)
        if p < thr:
            out.append({"kp_id": r["kp_id"], "p_mastery": p})
    out.sort(key=lambda x: x["p_mastery"])
    return out


# ---------------------------------------------------------------- 选题
def pick_questions(student_id: int, targets: list[dict], per_kp: int = 1,
                   verified_only: bool = True) -> tuple[list[bank.Question], list[dict]]:
    """给定练习目标，从题库里取题。

    两条规则：
    - **没做过的优先**——重复发同一道题，练出来的是背题，不是掌握；
    - **难度贴着当前水平**——取 difficulty 最接近 p_mastery+0.1 的题，
      既不是"降低难度讨好学生"（铁律 5 禁止），也不是无差别上难度。
    """
    seen = bank.attempted_question_ids(student_id)
    questions: list[bank.Question] = []
    reasons: list[dict] = []
    for tgt in targets:
        pool = bank.list_for_kp(tgt["kp_id"], verified_only=verified_only, limit=50)
        if not pool:
            continue
        aim = min(0.95, max(0.05, tgt.get("p_mastery", 0.0) + 0.1))
        pool.sort(key=lambda q: (q.id in seen, abs(q.difficulty - aim), q.id))
        for q in pool[:per_kp]:
            questions.append(q)
            reasons.append({
                "question_id": q.id, "kp_id": tgt["kp_id"], "kp_name": tgt.get("name", ""),
                "kind": tgt.get("kind", ""), "kind_label": tgt.get("kind_label", ""),
                "reason": tgt.get("reason", ""),
                "repeat": q.id in seen,
            })
    return questions, reasons


def coverage_gaps(student_id: int, targets: list[dict],
                  verified_only: bool = True) -> list[dict]:
    """哪些该练的知识点**题库里根本没题**。

    这个清单比任何"命中率"都有用：它直接告诉教师下一批题该出在哪。
    """
    out = []
    for tgt in targets:
        if not bank.list_for_kp(tgt["kp_id"], verified_only=verified_only, limit=1):
            out.append({"kp_id": tgt["kp_id"], "name": tgt.get("name", ""),
                        "kind_label": tgt.get("kind_label", "")})
    return out


def last_practiced(student_id: int, kp_id: int) -> float | None:
    """距上次在该知识点上作答过去了多少天。用于解释"为什么现在叫你复习"。"""
    row = state_repo.list_events(student_id=student_id, kp_id=kp_id, order="id")
    if not row:
        return None
    return round(days_between(row[-1]["occurred_at"], now_str()), 2)
