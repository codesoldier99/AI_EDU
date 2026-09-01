"""角色-权限矩阵的持久化与查询。"""
from __future__ import annotations

from packages.core.db import get_db

from .permissions import PERMISSIONS, ROLE_META, ROLE_PERMISSIONS


def ensure_matrix() -> dict:
    """幂等写入角色/权限矩阵。迁移后与 seed 都会调用。"""
    db = get_db()
    for code, desc in PERMISSIONS.items():
        if db.query_one("SELECT code FROM auth_permission WHERE code=?", (code,)):
            db.execute("UPDATE auth_permission SET description=? WHERE code=?", (desc, code))
        else:
            db.execute(
                "INSERT INTO auth_permission(code, description) VALUES(?,?)", (code, desc)
            )
    for code, (name, desc) in ROLE_META.items():
        if db.query_one("SELECT code FROM auth_role WHERE code=?", (code,)):
            db.execute("UPDATE auth_role SET name=?, description=? WHERE code=?",
                       (name, desc, code))
        else:
            db.execute(
                "INSERT INTO auth_role(code, name, description) VALUES(?,?,?)",
                (code, name, desc),
            )
    # 重建矩阵（以代码默认为准，保证测试与演示一致）
    db.execute("DELETE FROM auth_role_permission")
    rows = []
    for role, perms in ROLE_PERMISSIONS.items():
        for p in sorted(perms):
            rows.append((role, p))
    if rows:
        db.executemany(
            "INSERT INTO auth_role_permission(role_code, perm_code) VALUES(?,?)", rows
        )
    return {
        "roles": len(ROLE_META),
        "permissions": len(PERMISSIONS),
        "bindings": len(rows),
    }


def permissions_of(role_code: str) -> set[str]:
    rows = get_db().query(
        "SELECT perm_code FROM auth_role_permission WHERE role_code=?", (role_code,)
    )
    if rows:
        return {r["perm_code"] for r in rows}
    # 库未种子时退回代码默认，避免空库把所有人挡在门外
    return set(ROLE_PERMISSIONS.get(role_code, ()))


def has_permission(role_code: str, perm: str) -> bool:
    if not perm:
        return True
    return perm in permissions_of(role_code)


def list_matrix() -> dict:
    roles = get_db().query("SELECT * FROM auth_role ORDER BY code")
    perms = get_db().query("SELECT * FROM auth_permission ORDER BY code")
    binds = get_db().query(
        "SELECT role_code, perm_code FROM auth_role_permission ORDER BY role_code, perm_code"
    )
    by_role: dict[str, list[str]] = {}
    for b in binds:
        by_role.setdefault(b["role_code"], []).append(b["perm_code"])
    return {"roles": roles, "permissions": perms, "by_role": by_role}
