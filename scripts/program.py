"""培养方案视图：项目铺到了哪几门课，学生能认定哪些学分，下一门课该建什么。

    python3 scripts/program.py map [AI-ZSB-2026] [PRJ-DAC]   项目在培养方案上的铺开全景
    python3 scripts/program.py demand [PRJ-DAC]              悬空需求队列（该建哪几个点）
    python3 scripts/program.py coverage <student> [program]  学分认定视图
    python3 scripts/program.py courses [AI-ZSB-2026]         培养方案课程清单

大量课程的知识点数是 0，这是**刻意的**：图谱按项目拉取建设，
没有任何项目拉取过的课先不建。`demand` 回答的就是"那么下一步该建谁"。
"""
from __future__ import annotations

import sys

import _bootstrap  # noqa: F401
from packages.graph import repo as graph_repo
from packages.state import coverage
from packages.state import repo as state_repo

DEFAULT_PROGRAM = "AI-ZSB-2026"


def _bar(n: int, width: int = 12, cap: int = 24) -> str:
    filled = 0 if not n else max(1, round(min(n, cap) / cap * width))
    return "█" * filled + "·" * (width - filled)


def cmd_map(argv: list[str]) -> None:
    prog = next((a for a in argv if a.startswith("AI-") or a.startswith("ZH-")), DEFAULT_PROGRAM)
    proj = next((a for a in argv if a.startswith("PRJ-")), "PRJ-DAC")
    p = graph_repo.get_program(prog)
    if not p:
        raise SystemExit(f"培养方案不存在：{prog}")
    m = coverage.program_map(prog, proj)

    print(f"\n{p['name']}（{p['level']}·{p['klass']}）")
    print(f"项目：{proj}\n")
    print(f"  {'课程':26s} {'模块':6s} {'学期':>3s} {'学分':>4s} {'已建':>4s} "
          f"{'直接':>4s} {'间接':>4s} {'欠建':>4s}  图谱建设")
    print("  " + "─" * 92)
    last_mod = None
    for c in m["courses"]:
        if c["module"] != last_mod:
            last_mod = c["module"]
        touched = "★" if c["touched"] else " "
        name = (c["course_name"][:12] + "…") if len(c["course_name"]) > 13 else c["course_name"]
        print(f"{touched} {c['course_code']:11s}{name:15s} {c['module']:6s} "
              f"{c['semester']:>3d} {c['credit']:>4.1f} {c['kps_built']:>4d} "
              f"{c['pulled_direct']:>4d} {c['pulled_via_prereq']:>4d} {c['demanded']:>4d}  "
              f"{_bar(c['kps_built'])}")
    print("  " + "─" * 92)
    print(f"\n  ★ = 该项目触及到的课程：{m['n_touched']} / {m['n_courses']} 门")
    print(f"  已建图谱的课程：{m['n_built']} 门；"
          f"**被需要但一个知识点都还没建**的课程：{m['n_empty_but_needed']} 门")
    print(f"  项目拉取的知识点合计：{m['pulled_total']} 个"
          f"（其中 {m['project_only_kps']} 个属于项目专有知识，不折算学分）")
    print(f"  悬空需求：{m['demand_open']} 个知识点待建\n")
    print("  这张表的读法：0 不是没做完，是还没有项目拉取过它。")
    print("  下一步该建哪门课的哪几个点 → python3 scripts/program.py demand")


def cmd_demand(argv: list[str]) -> None:
    proj = next((a for a in argv if a.startswith("PRJ-")), None)
    items = coverage.build_queue(proj)
    if not items:
        print("需求队列为空：当前所有被引用的知识点都已建出来。")
        return
    print(f"\n悬空需求队列（{len(items)} 个知识点，按被需求次数排序）\n")
    print(f"  {'知识点代码':14s} {'归属课程':22s} {'需求数':>5s}  被谁需要")
    print("  " + "─" * 88)
    for it in items:
        by = "、".join(it.demanded_by[:3]) + ("…" if len(it.demanded_by) > 3 else "")
        print(f"  {it.code:14s} {it.course_name[:20]:22s} {it.demands:>5d}  {by}")
    print("  " + "─" * 88)

    print("\n按课程汇总（哪门课欠得最多，就该先建哪门）：")
    for r in graph_repo.demand_by_course():
        c = graph_repo.get_course(r["course_code"])
        print(f"  {r['course_code']:12s} {(c['name'] if c else ''):22s} "
              f"欠 {r['kps']} 个知识点 / 被需求 {r['demands']} 次")
    print("\n  建出来之后需求自动闭合，队列会自己变短——不需要人去清。")


def cmd_coverage(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("用法：python3 scripts/program.py coverage <student_id|学号> [program]")
    who = argv[0]
    prog = argv[1] if len(argv) > 1 else DEFAULT_PROGRAM
    s = (state_repo.get_student(int(who)) if who.isdigit()
         else state_repo.get_student_by_sid(who))
    if not s:
        raise SystemExit(f"未找到学生 {who}")
    rows = coverage.program_coverage(prog, s["id"])

    print(f"\n学生：{s['name']}（{s['sid']}）　培养方案：{prog}")
    from packages.core.config import CONFIG

    _t = CONFIG.teaching
    print(f"学分认定两道闸：图谱已建 ≥{_t.credit_min_kps} 个知识点，"
          f"且覆盖度 ≥{_t.credit_min_coverage:.0%}（培养方案 §4）\n")
    print(f"  {'课程':26s} {'已建':>4s} {'已验证':>5s} {'覆盖度':>6s} {'学分':>4s}  认定  说明")
    print("  " + "─" * 92)
    eligible = 0.0
    for c in rows:
        if not c.kps_built:
            continue          # 图谱没建的课不占篇幅，见末尾汇总
        mark = "✓" if c.credit_eligible else "—"
        if c.credit_eligible:
            eligible += c.credit
        name = (c.course_name[:12] + "…") if len(c.course_name) > 13 else c.course_name
        print(f"  {c.course_code:11s}{name:15s} {c.kps_built:>4d} {c.validated:>5d} "
              f"{c.coverage:>6.0%} {c.credit:>4.1f}   {mark}   {c.caveat}")
    print("  " + "─" * 92)
    n_empty = sum(1 for c in rows if not c.kps_built)
    print(f"\n  可认定学分合计：{eligible:.1f}")
    print(f"  另有 {n_empty} 门课图谱尚未建设，无法认定——"
          f"这不是学生没学，是系统还没建那门课的坐标系。")
    print("\n  提醒：覆盖度的分子只算「已验证掌握」（跨时间再次做对），不算当堂做对。")
    print("  学分要进档案，一次做对的证据强度不够。")


def cmd_courses(argv: list[str]) -> None:
    prog = argv[0] if argv else DEFAULT_PROGRAM
    rows = graph_repo.program_courses(prog)
    if not rows:
        raise SystemExit(f"培养方案不存在或没有课程：{prog}")
    last = None
    total = 0.0
    for c in rows:
        if c["module"] != last:
            print(f"\n【{c['module']}】")
            last = c["module"]
        total += c["credit"]
        print(f"  {c['code']:12s} {c['name']:26s} 第{c['semester']}学期 "
              f"{c['credit']:>4.1f}学分 {c['hours']:>3d}学时 {c['exam_type']:4s} "
              f"知识点 {c['kps']:>3d}")
    print(f"\n合计 {len(rows)} 门课 / {total:.1f} 学分")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, argv = sys.argv[1], sys.argv[2:]
    {
        "map": lambda: cmd_map(argv),
        "demand": lambda: cmd_demand(argv),
        "coverage": lambda: cmd_coverage(argv),
        "courses": lambda: cmd_courses(argv),
    }.get(cmd, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
