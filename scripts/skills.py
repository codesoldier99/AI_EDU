"""列出已装载的教学技能包（skills/ 目录）。

    make skills
    python3 scripts/skills.py

看不见的扩展点等于不存在的扩展点，所以装载结果要能一眼看全，
包括**没装上的那些**和它们为什么没装上。
"""
from __future__ import annotations

import _bootstrap  # noqa: F401
from packages import skills


def main() -> None:
    st = skills.stats()
    print(f"技能包目录：{st['dir']}")
    print(f"已装载 {st['loaded']} / 共 {st['total']}    "
          + "  ".join(f"{k}:{v}" for k, v in st["by_kind"].items()))
    print("-" * 88)
    print(f"{'名称':<26}{'类型':<16}{'追问方式':<12}{'优先级':>6}  文件")
    for p in st["items"]:
        print(f"{p['title'][:24]:<26}{p['kind']:<16}{(p['style'] or '—'):<12}"
              f"{p['priority']:>6}  {p['path']}")
    if st["broken"]:
        print("-" * 88)
        print("未能装载：")
        for b in st["broken"]:
            print(f"  ✗ {b['path']}：{b['error']}")
    print("-" * 88)
    print("技能包只影响'怎么问、怎么出题'；掌握度永远由 BKT 依据事件产生，"
          "往这个目录里放任何东西都改变不了这一点。")


if __name__ == "__main__":
    main()
