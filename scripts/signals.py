"""采集全部项目的五类标准信号。

    python3 scripts/signals.py            # 全量
    python3 scripts/signals.py 2026-08-01 # 只采这个日期之后的

核心代码对项目类型一无所知——遍历库里的项目，按 adapter_key 找适配器，
拿回来的一律是五类标准信号。接第 5 个和第 10 个适配器的成本应当相同。
"""
from __future__ import annotations

import sys

import _bootstrap  # noqa: F401
from packages.adapters.registry import collect_all, list_adapters


def main() -> None:
    since = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"已注册适配器：{'、'.join(list_adapters())}")
    for code, n in collect_all(since).items():
        if n < 0:
            print(f"  {code}：✗ 没有对应的适配器（检查 project.adapter_key）")
        elif n == 0:
            print(f"  {code}：0 条——数据源为空，或作者映射不到学号（不猜，宁可少记）")
        else:
            print(f"  {code}：{n} 条信号入库")


if __name__ == "__main__":
    main()
