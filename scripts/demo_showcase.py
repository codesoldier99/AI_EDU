"""求职智能体演示样板：张三——一名已完成全部培养计划、正在求职的虚构学生。

用途仅限给校长/评审演示"求职智能体能看到学生多少数据点位"，**不是**教学数据。
与 `scripts/demo.py`（60 人虚构班级、供诊断报告演示）同一条原则：数据是模拟的，
但生成方式不作弊——所有掌握度都经 `tracker.record()` 由事件流产生，
`make replay` 能把张三的状态从事件流完整重算、逐条比对。

刻意的设计：
  - 姓名"张三"，但 `cohort`/`klass` 写成明显的演示标记，任何真实班级视图
    按 klass 过滤时都不会把他和实验班A/B 的真实学生混在一起；
  - 覆盖全图谱知识点（而不是只挑一两门课），因为要展示的正是
    "两年内跨全部课程积累的信息"这件事本身；
  - 每个知识点安排三次分散在两年内的作答，最后一次压在"最近几天"——
    这不是为了好看，是 CLAUDE.md §3.3 的"还在吗"：掌握度按半衰期衰减，
    只有最近有复检的知识点，retained 才不会被判定为"该复检了"；
  - 掌握水平按正态分布抽样（均值约 0.72），不是清一色拉满——
    要演示的是"求职智能体能看到强项与差距"，不是造一个满分假学生。

**不可重复运行**：默认检测到张三已存在就退出。事件流只追加，重跑一次
就是给张三叠加第二份人生，之后没有干净撤销的办法（同 docs/deployment.md
对 `scripts/demo.py` 的警告）。确认要重新生成（比如改了模拟参数想重来），
才加 `--force`——它不会清空旧事件，是在旧的之上再叠一份，请谨慎。
"""
from __future__ import annotations

import random
import sys
from datetime import timedelta

import _bootstrap  # noqa: F401
from packages.adapters.base import ProjectSignal, persist_signals
from packages.core.db import get_db
from packages.core.timeutil import now, to_str
from packages.graph import repo as graph_repo
from packages.state import repo as state_repo
from packages.state import tracker

RNG = random.Random(20260921)

SID = "DEMO-ZS001"
NAME = "张三"
COHORT = "求职演示样板（非真实学生）"
KLASS = "演示专用·请勿计入班级统计"

PROGRAM_DAYS = 700  # 约两年培养周期


def build_student() -> dict:
    db = get_db()
    row = db.query_one("SELECT id FROM student WHERE sid=?", (SID,))
    if row and "--force" not in sys.argv:
        print(f"张三（{SID}）已存在，student_id={row['id']}。"
              f"事件流只追加，重复生成会叠加第二份数据——如确认要重来，加 --force。")
        sys.exit(0)
    student_id = state_repo.upsert_student(SID, NAME, COHORT, KLASS)
    print(f"→ 学生：{NAME}（{SID}），student_id={student_id}，"
          f"cohort={COHORT!r}，klass={KLASS!r}")
    return {"id": student_id, "sid": SID, "name": NAME}


def simulate_mastery(student_id: int) -> dict:
    """全图谱知识点，每个安排「首次接触 → 间隔复习 → 近期复检」三次作答。"""
    kps = graph_repo.list_kps()
    start = now() - timedelta(days=PROGRAM_DAYS)
    n_events = 0
    for i, kp in enumerate(kps):
        base = max(0.5, min(0.92, RNG.gauss(0.72, 0.12)))  # 平均水平，带自然波动
        t0 = start + timedelta(
            days=max(0, int(PROGRAM_DAYS * 0.7 * (i / len(kps))) + RNG.randint(-10, 10)))

        ok1 = RNG.random() < max(0.15, base * (1.1 - kp.difficulty * 0.3))
        tracker.record(
            student_id=student_id, event_type=RNG.choice(["quiz", "homework", "practice"]),
            kp_id=kp.id, is_correct=ok1, source="quiz", source_ref="showcase:张三",
            occurred_at=to_str(t0),
        )
        n_events += 1

        t1 = t0 + timedelta(days=RNG.randint(9, 28))
        if t1 < now() - timedelta(days=5):
            ok2 = RNG.random() < min(0.95, base + 0.10)
            tracker.record(
                student_id=student_id, event_type="quiz", kp_id=kp.id, is_correct=ok2,
                source="quiz", source_ref="showcase:张三", occurred_at=to_str(t1),
            )
            n_events += 1

        t2 = now() - timedelta(days=RNG.randint(1, 6))
        ok3 = RNG.random() < min(0.96, base + 0.15)
        tracker.record(
            student_id=student_id, event_type="quiz", kp_id=kp.id, is_correct=ok3,
            source="quiz", source_ref="showcase:张三", occurred_at=to_str(t2),
        )
        n_events += 1
    return {"kps": len(kps), "events": n_events}


SIGNAL_SPECS = {
    "code_commit": (45, lambda: RNG.randint(20, 600)),
    "build_test": (30, lambda: RNG.choice([1, 1, 1, 1, 0])),
    "runtime": (20, lambda: round(RNG.uniform(0.55, 0.95), 3)),
    "doc_delivery": (10, lambda: 1),
    "collaboration": (25, lambda: RNG.randint(1, 8)),
}


def simulate_projects(student_id: int, sid: str) -> dict:
    """两个项目全部任务标记完成 + 五类项目信号铺满两年周期。"""
    db = get_db()
    projects = graph_repo.list_projects()
    start = now() - timedelta(days=PROGRAM_DAYS - 30)
    n_tasks = n_milestones = 0

    for p in projects:
        db.execute(
            "INSERT OR REPLACE INTO project_member(project_id, student_id, role)"
            " VALUES(?,?,?)", (p["id"], student_id, "member"))
        tasks = graph_repo.list_tasks(p["id"])
        for j, t in enumerate(tasks):
            done_at = start + timedelta(
                days=int((PROGRAM_DAYS - 60) * (j / max(1, len(tasks)))) + RNG.randint(-5, 5))
            db.execute(
                "INSERT OR REPLACE INTO task_assignment(task_id, student_id, status, updated_at)"
                " VALUES(?,?,?,?)", (t.id, student_id, "done", to_str(done_at)))
            n_tasks += 1
            if t.milestone:
                tracker.record(
                    student_id=student_id, event_type="milestone", source="task",
                    source_ref=f"task:{t.id}", occurred_at=to_str(done_at),
                    payload={"detail": f"完成里程碑：{t.name}"},
                )
                n_milestones += 1

    signals = []
    for p in projects:
        for cls, (count, valgen) in SIGNAL_SPECS.items():
            for k in range(count):
                at = start + timedelta(days=RNG.randint(0, PROGRAM_DAYS - 60),
                                        hours=RNG.randint(8, 22))
                signals.append(ProjectSignal(
                    project_code=p["code"], student_sid=sid, signal_class=cls, metric=cls,
                    value=valgen(), occurred_at=to_str(at), raw_ref=f"showcase:{cls}:{k}",
                ))
    n_sig = persist_signals(signals)
    return {"projects": len(projects), "tasks": n_tasks, "milestones": n_milestones,
            "signals": n_sig}


def main() -> None:
    student = build_student()
    m = simulate_mastery(student["id"])
    print(f"→ 掌握度事件：{m['kps']} 个知识点 × 约 3 次作答 = {m['events']} 条 LearningEvent")
    p = simulate_projects(student["id"], student["sid"])
    print(f"→ 项目证据：{p['projects']} 个项目全部任务标记完成"
          f"（{p['tasks']} 个任务，{p['milestones']} 个里程碑）、"
          f"{p['signals']} 条五类项目信号")
    tracker.recompute_ability(student["id"])
    total = m["events"] + p["tasks"] + p["milestones"] + p["signals"]
    print(f"→ 合计写入 {total} 条结构化事实记录（这是求职智能体真实读取的数据点位；"
          f"'百万级'指的是这背后每次提交的每行 diff、每次追问的每个字这一级颗粒度，"
          f"系统按 CLAUDE.md 的判不了原则不会在报告里假装能数到这么细）。")
    print("完成。可用 GET /api/career/{job_code}/fit?student_id=<张三的 student_id> 验证。")


if __name__ == "__main__":
    main()
