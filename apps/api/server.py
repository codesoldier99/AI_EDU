"""API 进程入口。apps/api 仅做路由与鉴权，业务逻辑一律在 packages 下。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.core.config import CONFIG  # noqa: E402
from packages.core.db import get_db  # noqa: E402

from . import auth  # noqa: E402
from .microapi import App  # noqa: E402
from .routes import admin, diagnosis, graph, learning  # noqa: E402


def create_app() -> App:
    app = App(static_dir=ROOT / "apps" / "web")
    app.use(auth.middleware)
    graph.register(app)
    diagnosis.register(app)
    learning.register(app)
    admin.register(app)
    return app


def main() -> None:
    db = get_db()
    db.migrate(verbose=False)
    app = create_app()
    print("院长实验班 AI 教学系统 · 本地演示")
    print(f"  数据库：{db.path}")
    print(f"  大模型：{'离线降级表达器（未配置 API Key）' if _degraded() else CONFIG.llm_model}")
    app.serve(CONFIG.host, CONFIG.port)


def _degraded() -> bool:
    from packages.llm import gateway

    return gateway.is_degraded()


if __name__ == "__main__":
    main()
