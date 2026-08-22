"""把项目仓库里的任务语料导出成快照，供没有项目源码的机器使用。

    python3 scripts/snapshot_corpus.py dac3d

为什么需要它：教学系统部署在服务器上，产业项目的源码仓库通常不在那台机器上
（也不该在——几百兆的 Vivado 工程与图纸没有理由进教学服务器）。
但知识点匹配需要那份验收清单当语料，否则召回率会掉回只用任务名的水平。

快照里只有教师自己写的验收清单与模块说明，不含代码、不含图纸。
适配器永远优先读活仓库；快照是回落，不是真相。
"""
from __future__ import annotations

import json
import sys

import _bootstrap  # noqa: F401
from packages.core.config import ROOT


def main() -> None:
    key = sys.argv[1] if len(sys.argv) > 1 else "dac3d"
    from packages.adapters.registry import get_adapter

    ad = get_adapter(key)
    if not hasattr(ad, "task_corpus"):
        raise SystemExit(f"适配器 {key} 不提供任务语料")
    if not getattr(ad, "available", False):
        raise SystemExit(f"本机没有 {key} 的项目仓库，无法生成快照")
    corpus = ad.task_corpus()
    out = ROOT / "data" / "adapters" / f"{key}_corpus.json"
    out.write_text(json.dumps({
        "_说明": "任务语料快照：适配器优先读活仓库，读不到才回落到这里。",
        "corpus": corpus,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写出 {out}（{len(corpus)} 个任务，{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
