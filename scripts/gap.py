"""打印任务知识缺口与挡路度排序（调试拉取逻辑）。

    make gap STUDENT=3 TASK=T-AGV-3
    python3 scripts/gap.py 3 T-AGV-3

输出的是确定性计算结果，不经大模型——这正是"状态与表达分离"的意思。
"""
from __future__ import annotations

import sys

import _bootstrap  # noqa: F401
from packages.agents.task import compute_gap
from packages.core.config import CONFIG
from packages.graph import repo as graph_repo
from packages.state import repo as state_repo


def main() -> None:
    if len(sys.argv) < 3:
        print("用法：python3 scripts/gap.py <student_id|学号> <task_code|task_id>")
        tasks = graph_repo.list_tasks()[:8]
        print("可用任务示例：" + "、".join(t.code for t in tasks))
        return
    who, what = sys.argv[1], sys.argv[2]
    s = (state_repo.get_student(int(who)) if who.isdigit()
         else state_repo.get_student_by_sid(who))
    if not s:
        raise SystemExit(f"未找到学生 {who}")
    t = (graph_repo.get_task(int(what)) if what.isdigit()
         else graph_repo.get_task_by_code(what))
    if not t:
        raise SystemExit(f"未找到任务 {what}")

    g = compute_gap(s["id"], t.id)
    thr = CONFIG.teaching.mastery_threshold
    print(f"学生：{s['name']}（{s['sid']}）")
    print(f"任务：{t.code} {t.name}")
    print(f"掌握度阈值：{thr}    所需知识点 {g.required_total} 个，已掌握 {g.mastered} 个")
    print(f"置信度 {g.confidence}（证据 {g.evidence_count} 条）{'  ' + g.caveat if g.caveat else ''}")
    print("-" * 78)
    print(f"{'知识点':<34}{'掌握度':>8}{'挡路度':>8}{'拓扑':>6}  前置卡点")
    for item in g.gap:
        pre = ",".join(str(p) for p in item["prereq_gap"]) or "-"
        print(f"{item['name'][:32]:<34}{item['p_mastery']:>8.3f}"
              f"{item['blocking_severity']:>8.1f}{item['topo_rank']:>6}  {pre}")
    print("-" * 78)
    print(f"本次只推 {len(g.push)} 个（拉取式：一次只给最挡路的 1–2 个）：")
    for p in g.push:
        print(f"  → {p['name']}   掌握度 {p['p_mastery']:.3f}  挡路度 {p['blocking_severity']}")


if __name__ == "__main__":
    main()
