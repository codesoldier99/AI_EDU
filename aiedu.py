#!/usr/bin/env python3
"""统一命令行入口。与 Makefile 等价，供没有 make 的环境使用。

    python3 aiedu.py setup                    一键就绪（迁移 + 种子 + 演示数据）
    python3 aiedu.py dev [--port 8900]        启动本地环境
    python3 aiedu.py migrate                  数据库迁移
    python3 aiedu.py seed [course|projects|kb|teachers]
    python3 aiedu.py demo [N]                 生成虚拟班级演示数据
    python3 aiedu.py mock-llm [--port 8910]   本机假底座（无 Key 也能演示"接上大模型"）
    python3 aiedu.py test                     全量测试
    python3 aiedu.py test-state               仅测状态层（改 BKT 后必跑）
    python3 aiedu.py check                    架构铁律自检
    python3 aiedu.py lint                     静态检查
    python3 aiedu.py replay [student_id]      从事件流重算状态，校验一致性
    python3 aiedu.py gap <student> <task>     打印任务知识缺口
    python3 aiedu.py practice <student> [task] 打印此刻该练什么及其理由
    python3 aiedu.py skills                   列出已装载的教学技能包
    python3 aiedu.py reset                    清空数据库
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


def run(args: list[str], cwd: Path | None = None, env: dict | None = None) -> int:
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.call(args, cwd=str(cwd or ROOT), env=e)


def main() -> int:
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "help"
    rest = argv[1:]

    if cmd in ("help", "-h", "--help"):
        print(__doc__)
        return 0

    if cmd == "setup":
        for step in (["migrate"], ["seed"], ["demo"] + rest):
            rc = main_with(step)
            if rc:
                return rc
        port = os.environ.get("AIEDU_PORT", "8900")
        print(f"\n就绪。执行 `python3 aiedu.py dev` 后打开 http://127.0.0.1:{port}/")
        return 0

    if cmd == "dev":
        env = {}
        if "--port" in rest:
            env["AIEDU_PORT"] = rest[rest.index("--port") + 1]
        return run([PY, "-m", "apps.api.server"], env=env)

    if cmd == "migrate":
        return run([PY, "scripts/migrate.py"])
    if cmd == "seed":
        return run([PY, "scripts/seed.py", *rest])
    if cmd == "demo":
        return run([PY, "scripts/demo.py", *(rest or ["60"])])
    if cmd == "mock-llm":
        return run([PY, "scripts/mock_llm.py", *rest])
    if cmd == "lint":
        return run([PY, "scripts/lint.py"])
    if cmd == "replay":
        return run([PY, "scripts/replay.py", *rest])
    if cmd == "gap":
        return run([PY, "scripts/gap.py", *rest])
    if cmd == "practice":
        return run([PY, "scripts/practice.py", *rest])
    if cmd == "skills":
        return run([PY, "scripts/skills.py"])

    if cmd == "test":
        return run([PY, "-m", "unittest", "discover", "-s", "tests", "-t", "tests", "-v"])
    if cmd == "test-state":
        return run([PY, "-m", "unittest", "test_bkt", "test_replay", "-v"], cwd=ROOT / "tests")
    if cmd == "test-study":
        return run([PY, "-m", "unittest", "test_tools", "test_quiz", "test_study",
                    "test_skills", "-v"], cwd=ROOT / "tests")
    if cmd == "check":
        return run([PY, "-m", "unittest", "test_layering", "-v"], cwd=ROOT / "tests")

    if cmd == "reset":
        n = 0
        for f in (ROOT / "var").glob("aiedu.db*"):
            f.unlink()
            n += 1
        print(f"已删除 {n} 个数据库文件。执行 `python3 aiedu.py setup` 重建。")
        return 0

    print(f"未知命令：{cmd}\n")
    print(__doc__)
    return 2


def main_with(argv: list[str]) -> int:
    saved = sys.argv
    try:
        sys.argv = [saved[0], *argv]
        return main()
    finally:
        sys.argv = saved


if __name__ == "__main__":
    sys.exit(main())
