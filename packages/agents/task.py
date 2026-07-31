"""模块三：项目任务智能管理 —— 拉取式调度（本次范式调整的核心）。

旧逻辑（已废弃）：next_task = topological_next(task_graph, completed)   # 按拓扑序推
新逻辑：知识由项目需求拉取，不由课程顺序推送。

    学生接到项目任务 → 识别任务所需知识点 → 计算缺口 = 所需 − 已掌握
    → 只推送此刻最挡路的 1–2 个 → 学完立刻用回项目

拓扑序仍保留，但只用于计算缺口内部的先后，不用于安排全员进度。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.db import get_db
from packages.core.models import Message, confidence_from_evidence
from packages.core.timeutil import now_str
from packages.engagement import service as engagement
from packages.graph import algo as graph_algo
from packages.graph import repo as graph_repo
from packages.state import repo as state_repo
from packages.state import tracker

from .base import Agent, AgentOutput


@dataclass
class GapItem(Message):
    kp_id: int = 0
    code: str = ""
    name: str = ""
    p_mastery: float = 0.0
    confidence: float = 0.0
    evidence_count: int = 0
    necessity: str = "required"
    blocking_severity: float = 0.0
    topo_rank: int = 0
    prereq_gap: list = field(default_factory=list)   # 缺口内部还挡在它前面的点


@dataclass
class GapResult(Message):
    student_id: int = 0
    task_id: int = 0
    task_name: str = ""
    required_total: int = 0
    mastered: int = 0
    gap: list = field(default_factory=list)      # 全部缺口（已排序）
    push: list = field(default_factory=list)     # 本次只推的 1–2 个
    confidence: float = 0.0
    evidence_count: int = 0
    caveat: str = ""


def compute_gap(student_id: int, task_id: int, include_helpful: bool = False) -> GapResult:
    """缺口计算：任务所需 − 学生已掌握，缺口内部拓扑排序后按挡路度排序。"""
    thr = CONFIG.teaching.mastery_threshold
    task = graph_repo.get_task(task_id)
    required = graph_repo.required_kps(task_id, include_helpful)
    mastery_rows = {r["kp_id"]: r for r in state_repo.mastery_rows(student_id)}
    mastery = {k: v["p_mastery"] for k, v in mastery_rows.items()}

    gap_ids, mastered = [], 0
    for r in required:
        p = mastery.get(r["kp_id"], 0.0)
        if p >= thr:
            mastered += 1
        else:
            gap_ids.append(r)

    # 拉取式的挡路度：只统计"还没做完的任务"，做完的任务不再构成阻塞
    pending = _pending_task_ids(student_id)
    order = graph_algo.topological_sort([g["kp_id"] for g in gap_ids])
    rank = {kp: i for i, kp in enumerate(order)}
    gap_set = set(rank)

    items: list[GapItem] = []
    for r in gap_ids:
        kp_id = r["kp_id"]
        m = mastery_rows.get(kp_id, {})
        prereq_gap = [p for p in graph_repo.prereqs_of(kp_id) if p in gap_set]
        items.append(
            GapItem(
                kp_id=kp_id, code=r["code"], name=r["name"],
                p_mastery=round(mastery.get(kp_id, 0.0), 4),
                confidence=m.get("confidence", 0.0),
                evidence_count=m.get("evidence_count", 0),
                necessity=r["necessity"],
                blocking_severity=graph_algo.blocking_severity(kp_id, pending),
                topo_rank=rank.get(kp_id, 0),
                prereq_gap=prereq_gap,
            )
        )
    # 排序规则：缺口内部没有前置卡点的优先（能立刻学），再按挡路度，再按拓扑序
    items.sort(key=lambda i: (len(i.prereq_gap), -i.blocking_severity, i.topo_rank))

    evid = sum(i.evidence_count for i in items) + mastered
    res = GapResult(
        student_id=student_id,
        task_id=task_id,
        task_name=task.name if task else "",
        required_total=len(required),
        mastered=mastered,
        gap=[i.to_dict() for i in items],
        push=[i.to_dict() for i in items[: CONFIG.teaching.gap_push_limit]],
        confidence=confidence_from_evidence(evid // max(1, len(required) or 1)),
        evidence_count=evid,
    )
    res.caveat = "" if evid >= CONFIG.teaching.min_evidence_for_report else "数据不足，仅供参考"
    return res


def _pending_task_ids(student_id: int) -> set[int]:
    rows = get_db().query(
        "SELECT task_id FROM task_assignment WHERE student_id=? AND status<>'done'",
        (student_id,),
    )
    ids = {r["task_id"] for r in rows}
    if not ids:  # 未分派任务时退化为全量任务，保证挡路度仍可算
        ids = {t.id for t in graph_repo.list_tasks()}
    return ids


class TaskAgent(Agent):
    name = "task"

    def next_learning_target(self, student_id: int, task_id: int) -> AgentOutput:
        gap = compute_gap(student_id, task_id)
        task = graph_repo.get_task(task_id)
        narrative, degraded = self.express(
            {
                "意图": "拉取式学习建议",
                "当前任务": gap.task_name,
                "所需知识点数": gap.required_total,
                "已掌握": gap.mastered,
                "此刻最挡路": "、".join(p["name"] for p in gap.push) or "（无缺口，可直接开工）",
                "理由": "；".join(
                    f"{p['name']} 挡住 {int(p['blocking_severity'])} 分权重的后续内容"
                    for p in gap.push
                ),
                "数据充分性": gap.caveat or "证据充分",
            },
            max_tokens=400,
        )
        return AgentOutput(
            agent=self.name, narrative=narrative, plan=gap.to_dict(),
            confidence=gap.confidence, evidence_count=gap.evidence_count,
            degraded=degraded, caveat=gap.caveat,
        )

    # ---------- 任务分派与进度 ----------
    def assign(self, task_id: int, student_id: int, status: str = "todo") -> dict:
        get_db().execute(
            "INSERT INTO task_assignment(task_id, student_id, status, updated_at)"
            " VALUES(?,?,?,?) ON CONFLICT(task_id, student_id) DO UPDATE SET"
            " status=excluded.status, updated_at=excluded.updated_at",
            (task_id, student_id, status, now_str()),
        )
        return {"task_id": task_id, "student_id": student_id, "status": status}

    def set_status(self, task_id: int, student_id: int, status: str) -> dict:
        self.assign(task_id, student_id, status)
        task = graph_repo.get_task(task_id)
        if status == "done" and task and task.milestone:
            # 里程碑是允许的激励对象（只奖励坚持，不奖励正确率）
            tracker.record(
                student_id=student_id, event_type="milestone", kp_id=None,
                source="task", source_ref=f"task:{task_id}",
                payload={"detail": f"完成里程碑：{task.name}"},
            )
            engagement.scan_achievements(student_id)
        return {"task_id": task_id, "status": status}

    def board(self, project_id: int, student_id: int | None = None) -> dict:
        tasks = graph_repo.list_tasks(project_id)
        assign = {}
        if student_id:
            assign = {
                r["task_id"]: r["status"]
                for r in get_db().query(
                    "SELECT task_id, status FROM task_assignment WHERE student_id=?",
                    (student_id,),
                )
            }
        rows = []
        for t in tasks:
            kps = graph_repo.required_kps(t.id, include_helpful=True)
            rows.append(
                {
                    "id": t.id, "code": t.code, "name": t.name, "parent_id": t.parent_id,
                    "milestone": t.milestone, "seq": t.seq,
                    "n_kps": len(kps),
                    "status": assign.get(t.id, "unassigned"),
                }
            )
        return {"project_id": project_id, "tasks": rows}

    def project_progress(self, project_id: int, student_id: int) -> dict:
        """只报"任务进度"与"知识点掌握度"，**不报课程进度百分比**（明令禁止）。"""
        b = self.board(project_id, student_id)
        done = sum(1 for t in b["tasks"] if t["status"] == "done")
        total = len(b["tasks"]) or 1
        return {
            "project_id": project_id,
            "tasks_done": done,
            "tasks_total": total,
            "task_progress": round(done / total, 4),
            "note": "本系统不提供课程进度百分比，只提供任务进度与知识点掌握度",
        }
