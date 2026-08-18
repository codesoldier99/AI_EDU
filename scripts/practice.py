"""打印"此刻该练什么"及其理由（调试针对性练习的选点逻辑）。

    make practice STUDENT=3
    python3 scripts/practice.py 3 [task_code]

排序依据只有四类：到期复检 / 待跨时间验证 / 任务缺口 / 根因点。
没有一类是正确率——这一条由 tests/test_layering.py 静态盯着。
输出全部来自确定性计算，不经大模型。
"""
from __future__ import annotations

import sys

import _bootstrap  # noqa: F401
from packages.graph import repo as graph_repo
from packages.quiz import bank, selector
from packages.state import repo as state_repo


def main() -> None:
    if len(sys.argv) < 2:
        print("用法：python3 scripts/practice.py <student_id|学号> [task_code]")
        return
    who = sys.argv[1]
    s = (state_repo.get_student(int(who)) if who.isdigit()
         else state_repo.get_student_by_sid(who))
    if not s:
        raise SystemExit(f"未找到学生 {who}")

    task_id = None
    if len(sys.argv) > 2:
        t = graph_repo.get_task_by_code(sys.argv[2])
        if not t:
            raise SystemExit(f"未找到任务 {sys.argv[2]}")
        task_id = t.id

    plan = selector.practice_plan(s["id"], task_id=task_id)
    print(f"学生：{s['name']}（{s['sid']}）")
    print(f"置信度 {plan.confidence}（证据 {plan.evidence_count} 条）"
          f"{'  ' + plan.caveat if plan.caveat else ''}")
    print("-" * 92)
    print(f"{'知识点':<28}{'来源':<12}{'掌握度':>8}{'挡路度':>8}{'优先级':>8}  理由")
    for t in plan.targets:
        print(f"{t['name'][:26]:<28}{t['kind_label']:<12}{t['p_mastery']:>8.3f}"
              f"{t['blocking_severity']:>8.1f}{t['priority']:>8.2f}  {t['reason']}")
    print("-" * 92)

    qs, reasons = selector.pick_questions(s["id"], plan.targets, per_kp=1)
    print(f"可组卷 {len(qs)} 题：")
    for q, r in zip(qs, reasons):
        mark = "（重复题）" if r["repeat"] else ""
        print(f"  → [{q.qtype}] {q.stem[:44]}{mark}")
    missing = selector.coverage_gaps(s["id"], plan.targets)
    if missing:
        print("题库缺口（该练但没题，教师优先在这些点上出题）：")
        for m in missing:
            print(f"  ✗ {m['name']}（{m['kind_label']}）")
    st = bank.stats()
    print(f"\n题库：可用 {st['usable']} 题 / 待审 {st['pending']} 题 / "
          f"已退役 {st['retired']} 题，覆盖 {st['kp_covered']} 个知识点")


if __name__ == "__main__":
    main()
