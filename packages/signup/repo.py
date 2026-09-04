"""报名的读写。所有 SQL 收在这里（开发规范：写操作走 repository 层）。"""
from __future__ import annotations

from packages.core.db import dumps, get_db, loads
from packages.core.timeutil import now_str

# 三种参与方式，与汇报里那一页一一对应。
# 用固定枚举而不是自由文本：会后要按方式分组联系人，自由文本会分不出来。
ROLES = {
    "mentor": "项目导师",
    "review": "审知识点与映射",
    "dev": "一起做开发",
}

MAXLEN = {"name": 40, "dept": 60, "contact": 80, "topic": 300, "note": 500, "source": 40}


class SignupError(ValueError):
    """报名内容不合法。故意用一个专门的异常，让路由层能翻成 400 而不是 500。"""


def _clean(value: object, field: str) -> str:
    s = str(value or "").strip()
    limit = MAXLEN[field]
    if len(s) > limit:
        raise SignupError(f"{field} 超长（上限 {limit} 字）")
    return s


def create(name: str, dept: str = "", contact: str = "", roles: list[str] | None = None,
           topic: str = "", note: str = "", source: str = "") -> int:
    name = _clean(name, "name")
    if not name:
        raise SignupError("请填写姓名")
    picked = [r for r in (roles or []) if r in ROLES]
    if not picked:
        raise SignupError("请至少选择一种参与方式")
    return get_db().execute(
        "INSERT INTO signup(name, dept, contact, roles, topic, note, source, created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (name, _clean(dept, "dept"), _clean(contact, "contact"), dumps(picked),
         _clean(topic, "topic"), _clean(note, "note"), _clean(source, "source"), now_str()))


def _row(r: dict) -> dict:
    d = dict(r)
    d["roles"] = loads(r["roles"], [])
    d["role_names"] = [ROLES[x] for x in d["roles"] if x in ROLES]
    return d


def list_all(limit: int = 500) -> list[dict]:
    return [_row(r) for r in get_db().query(
        "SELECT * FROM signup ORDER BY id DESC LIMIT ?", (min(limit, 2000),))]


def stats() -> dict:
    """只给计数，不给名单。

    报名页要显示「已有 N 位老师报名」来推一把，但那一页是公开的——
    把姓名和手机号顺手带出去，就成了一次没必要的信息泄漏。
    """
    db = get_db()
    by_role = {k: 0 for k in ROLES}
    for r in db.query("SELECT roles FROM signup"):
        for x in loads(r["roles"], []):
            if x in by_role:
                by_role[x] += 1
    return {"total": db.scalar("SELECT COUNT(*) FROM signup") or 0,
            "by_role": by_role, "role_names": ROLES}
