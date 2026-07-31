"""从事件流重算状态并校验一致性（Phase 0 验收标准）。

    make replay STUDENT=<id>     校验单个学生
    make replay                  校验全班（抽样报告）

这是"系统可被第三方核查"的技术保证：状态不是模型说的，是事件算出来的，
任何人都能拿走事件流自己重算一遍。
"""
from __future__ import annotations

import sys

import _bootstrap  # noqa: F401
from packages.core.db import get_db
from packages.engagement import service as engagement
from packages.state import replay
from packages.state import repo as state_repo


def check(student_id: int, verbose: bool = True) -> bool:
    d = replay.verify(student_id)
    e = engagement.verify_streak(student_id)
    ok = d.ok and e["match"]
    if verbose or not ok:
        s = state_repo.get_student(student_id)
        tag = "✓" if ok else "✗"
        print(f"{tag} [{student_id}] {s['name'] if s else '?'}  "
              f"事件 {d.n_events} / 知识点 {d.n_kps} / 不一致 {len(d.mismatches)} / "
              f"engagement {'一致' if e['match'] else '不一致'}")
        for m in d.mismatches[:5]:
            print(f"    - {m}")
        if not e["match"]:
            print(f"    - engagement stored={e['stored']} replay={e['replay']}")
    return ok


def main() -> None:
    get_db().migrate(verbose=False)
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        sys.exit(0 if check(int(sys.argv[1])) else 1)
    students = state_repo.list_students()
    if not students:
        print("暂无学生数据，先执行 make demo")
        return
    bad = [s for s in students if not check(s["id"], verbose=False)]
    print(f"重算校验：{len(students) - len(bad)}/{len(students)} 一致")
    for s in bad:
        check(s["id"])
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
