"""数据库访问层。

设计取向：SQL 写成 PostgreSQL 与 SQLite 双兼容的子集，本地演示走 SQLite（零依赖），
生产切 PostgreSQL 16 + pgvector 只需换 db_url 并安装驱动，业务代码不改。
向量以 JSON 文本落库（SQLite），迁到 pgvector 时由 migrations 改列类型。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import CONFIG, ROOT

_local = threading.local()


class Database:
    """极薄的连接与查询封装。仓储(repository)层之上禁止直接写 SQL。"""

    def __init__(self, url: str | None = None) -> None:
        self.url = url or CONFIG.db_url
        if not self.url.startswith("sqlite://"):
            raise NotImplementedError(
                "当前构建仅内置 SQLite 驱动；接 PostgreSQL 请安装 psycopg 并实现 PgDatabase"
            )
        self.path = Path(self.url.replace("sqlite:///", ""))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---- 连接 ----
    def conn(self) -> sqlite3.Connection:
        key = f"conn::{self.path}"
        c = getattr(_local, key, None)
        if c is None:
            c = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA journal_mode=WAL")
            setattr(_local, key, c)
        return c

    def close(self) -> None:
        key = f"conn::{self.path}"
        c = getattr(_local, key, None)
        if c is not None:
            c.close()
            setattr(_local, key, None)

    # ---- 查询 ----
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        cur = self.conn().execute(sql, tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.query_one(sql, params)
        return None if row is None else list(row.values())[0]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        cur = self.conn().execute(sql, tuple(params))
        rowid = cur.lastrowid
        cur.close()
        return rowid

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        cur = self.conn().executemany(sql, [tuple(s) for s in seq])
        cur.close()

    @contextmanager
    def tx(self):
        c = self.conn()
        c.execute("BEGIN")
        try:
            yield self
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

    # ---- 迁移 ----
    def migrate(self, verbose: bool = True) -> list[str]:
        mig_dir = ROOT / "migrations"
        self.conn().executescript(
            "CREATE TABLE IF NOT EXISTS schema_migration("
            " name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        done = {r["name"] for r in self.query("SELECT name FROM schema_migration")}
        applied = []
        for f in sorted(mig_dir.glob("*.sql")):
            if f.name in done:
                continue
            sql = f.read_text(encoding="utf-8")
            self.conn().executescript(sql)
            self.execute(
                "INSERT INTO schema_migration(name, applied_at) VALUES(?, datetime('now'))",
                (f.name,),
            )
            applied.append(f.name)
            if verbose:
                print(f"  applied {f.name}")
        return applied

    def reset(self) -> None:
        """仅供演示与测试：删库重来。"""
        self.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                p.unlink()


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def set_db(db: Database) -> None:
    """测试用：替换全局库。"""
    global _db
    _db = db


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(txt: str | None, default: Any = None) -> Any:
    if not txt:
        return default
    try:
        return json.loads(txt)
    except Exception:
        return default
