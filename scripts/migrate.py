"""数据库迁移。"""
from __future__ import annotations

import _bootstrap  # noqa: F401
from packages.core.db import get_db


def main() -> None:
    db = get_db()
    print(f"数据库：{db.path}")
    applied = db.migrate()
    print("已是最新" if not applied else f"应用了 {len(applied)} 个迁移")


if __name__ == "__main__":
    main()
