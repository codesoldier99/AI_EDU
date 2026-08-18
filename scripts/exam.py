"""考试运维命令行。

考试当天最怕的不是功能不全，是**只有一条路可走**。所以除了网页，
监考与判分的每一步都留一条命令行通道：网页挂了照样能把考试办完。

    python3 scripts/exam.py import data/seed/exam_ml_selection.yaml
    python3 scripts/exam.py publish  ML-SELECT-2026
    python3 scripts/exam.py tickets  ML-SELECT-2026 [--regenerate]
    python3 scripts/exam.py monitor  ML-SELECT-2026
    python3 scripts/exam.py sweep    ML-SELECT-2026      # 收超时未交的卷
    python3 scripts/exam.py pending  ML-SELECT-2026      # 待人工判分队列
    python3 scripts/exam.py rank     ML-SELECT-2026 [53]
    python3 scripts/exam.py export   ML-SELECT-2026 [out.csv]
"""
from __future__ import annotations

import csv
import sys

import _bootstrap  # noqa: F401
from packages.exam import paper, scoring, session
from packages.state import repo as state_repo


def _exam(code: str) -> dict:
    e = paper.get_exam(code)
    if not e:
        raise SystemExit(f"未找到考试 {code}")
    return e


def cmd_import(args: list[str]) -> None:
    path = args[0] if args else "data/seed/exam_ml_selection.yaml"
    spec = paper.import_paper(path)
    print(f"已导入 {spec.code}：{spec.title}")
    print(f"  {len(spec.items)} 题 / {spec.total_score} 分 / {spec.duration_min} 分钟")
    for part, v in sorted(spec.by_part.items()):
        label = "概念锚题（不讲评）" if part == "A" else "选拔专用（可讲评）"
        print(f"  {part} 区 {label}：{v['n']} 题 {v['points']} 分")
    for t, v in spec.by_type.items():
        print(f"    {v['label']:<10} {v['n']:>3} 题 {v['points']:>6} 分")
    print("\n下一步：python3 scripts/exam.py publish " + spec.code)


def cmd_publish(args: list[str]) -> None:
    e = _exam(args[0])
    r = paper.publish(e["id"], args[1] if len(args) > 1 else "",
                      args[2] if len(args) > 2 else "")
    print(f"已发布 {r['code']}：{r['n_items']} 题 / {r['total_score']} 分")
    print("  " + r["note"])


def cmd_tickets(args: list[str]) -> None:
    e = _exam(args[0])
    regen = "--regenerate" in args
    rows = session.issue_tickets(e["id"], None, regen)
    print(f"# {e['title']}")
    print(f"# 共 {len(rows)} 人。裁开分发，一人一条；口令一场考试只用一次。")
    print(f"{'座位':<6}{'学号':<12}{'姓名':<10}{'准考口令':<10}")
    print("-" * 42)
    for r in rows:
        s = state_repo.get_student(r["student_id"])
        print(f"{r['seat_no']:<6}{s['sid']:<12}{s['name']:<10}{r['ticket']:<10}"
              + ("  (沿用)" if r.get("reused") else ""))
    print("-" * 42)
    print(f"考场地址：http://<服务器>/exam.html?exam={e['code']}")


def cmd_monitor(args: list[str]) -> None:
    e = _exam(args[0])
    m = session.monitor(e["id"])
    print(f"{e['title']}")
    print(f"  已发准考证 {m['issued']} 人 / 已开考 {m['started']} / "
          f"进行中 {m['in_progress']} / 已交卷 {m['submitted']}")
    if m["absent"]:
        print(f"  未开考 {len(m['absent'])} 人：" +
              "、".join(f"{a['sid']} {a['name']}" for a in m["absent"][:20]))
    print(f"\n{'学号':<12}{'姓名':<10}{'状态':<12}{'总分':>8}{'待判':>6}")
    print("-" * 50)
    for r in m["rows"]:
        sc = "-" if r["total_score"] is None else f"{r['total_score']:.1f}"
        print(f"{r['sid']:<12}{r['name']:<10}{r['status']:<12}{sc:>8}{r['n_pending']:>6}")


def cmd_sweep(args: list[str]) -> None:
    e = _exam(args[0])
    print(f"已收回 {session.sweep_expired(e['id'])} 份超时未交的卷子")


def cmd_pending(args: list[str]) -> None:
    e = _exam(args[0])
    rows = scoring.pending_queue(e["id"])
    if not rows:
        print("没有待人工判分的题目。可以排名了。")
        return
    print(f"待人工判分 {len(rows)} 条（程序题 + 规则判不出来的题）：\n")
    for r in rows:
        print(f"[{r['sid']} {r['name']}] 第 {r['question_id']} 题 · {r['points']} 分")
        print(f"  题面：{r['stem'][:70]}...")
        print(f"  作答：{(r['response'] or '（空白）')[:160]}")
        print(f"  评分要点：{(r['rationale'] or '')[:120]}")
        print(f"  判分命令：exam.py score {r['session_id']} {r['question_id']} <分数>\n")


def cmd_score(args: list[str]) -> None:
    r = scoring.teacher_score(int(args[0]), int(args[1]), float(args[2]),
                              args[3] if len(args) > 3 else "")
    print(f"已给分：总分 {r['total_score']}，该考生仍有 {r['n_pending']} 题待判")


def cmd_rank(args: list[str]) -> None:
    e = _exam(args[0])
    cutoff = int(args[1]) if len(args) > 1 else 53
    r = scoring.ranking(e["id"], cutoff)
    print(f"{e['title']}    切线取前 {cutoff} 名")
    for w in r.warnings:
        print(f"  ⚠ {w}")
    print(f"\n分数线 {r.cutoff_score} 分；第 {cutoff} 名与第 {cutoff+1} 名分差 {r.margin} 分")
    print(f"并列于分数线的有 {r.tie_at_cutoff} 人")
    print("\n并列裁决顺序（考前公布，考后不得更改）：")
    for i, d in enumerate(r.tiebreak_rules, 1):
        print(f"  {i}. {d}")
    print(f"\n{'名次':<6}{'学号':<12}{'姓名':<10}{'总分':>7}{'A区':>7}{'B区':>7}"
          f"{'程序':>7}  {'录取':<6}{'裁决依据'}")
    print("-" * 80)
    for row in r.rows:
        mark = "★实验班" if row["selected"] else ""
        edge = "  ← 分数线" if row["rank"] == cutoff else ""
        print(f"{row['rank']:<6}{row['sid']:<12}{row['name']:<10}"
              f"{row['total_score']:>7.1f}{row['score_a']:>7.1f}{row['score_b']:>7.1f}"
              f"{row['score_program']:>7.1f}  {mark:<6}{row['decided_by']}{edge}")


def cmd_export(args: list[str]) -> None:
    e = _exam(args[0])
    out = args[1] if len(args) > 1 else f"var/{e['code']}_dataset.csv"
    rows = scoring.export_rows(e["id"])
    if not rows:
        raise SystemExit("没有可导出的数据")
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"已导出 {len(rows)} 行 -> {out}")
    print("  total_score      断点回归的 running variable (R)")
    print("  centered_score   R − 分数线，断点在 0")
    print("  assigned_experimental  处理变量 D = 1[R ≥ c]")
    print("  score_anchor_A   两年后纵向比较用的那部分分数")


CMDS = {"import": cmd_import, "publish": cmd_publish, "tickets": cmd_tickets,
        "monitor": cmd_monitor, "sweep": cmd_sweep, "pending": cmd_pending,
        "score": cmd_score, "rank": cmd_rank, "export": cmd_export}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__)
        return
    CMDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
