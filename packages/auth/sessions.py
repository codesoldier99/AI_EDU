"""会话令牌：不透明随机串，替代可猜的 teacher:/student: 明文（演示仍兼容后者）。"""
from __future__ import annotations

import secrets

from packages.core.db import get_db
from packages.core.timeutil import now_str, parse, shift, now

from . import accounts


def _new_token() -> str:
    return secrets.token_urlsafe(24)


def issue(account_id: int, ttl_hours: int | None = 72, note: str = "") -> dict:
    """签发 session:<token>。ttl_hours=None 表示演示期不过期。"""
    acc = accounts.get_account(account_id)
    if not acc or not acc["active"]:
        raise ValueError("账号不存在或已停用")
    token = _new_token()
    issued = now_str()
    expires = None
    if ttl_hours is not None:
        expires = shift(now(), hours=ttl_hours).strftime("%Y-%m-%dT%H:%M:%S")
    sid = get_db().execute(
        "INSERT INTO auth_session(account_id, token, issued_at, expires_at, revoked, note)"
        " VALUES(?,?,?,?,0,?)",
        (account_id, token, issued, expires, note),
    )
    return {
        "session_id": sid,
        "token": f"session:{token}",
        "issued_at": issued,
        "expires_at": expires,
        "account_id": account_id,
        "role": acc["role_code"],
        "ident": acc["ident"],
    }


def revoke(raw_token: str) -> bool:
    token = raw_token.split(":", 1)[-1] if raw_token.startswith("session:") else raw_token
    row = get_db().query_one(
        "SELECT id FROM auth_session WHERE token=? AND revoked=0", (token,)
    )
    if not row:
        return False
    get_db().execute("UPDATE auth_session SET revoked=1 WHERE id=?", (row["id"],))
    return True


def resolve_session_token(raw: str) -> dict:
    """解析 session 令牌 → principal。失败抛 ValueError。"""
    token = raw
    row = get_db().query_one(
        "SELECT * FROM auth_session WHERE token=? AND revoked=0", (token,)
    )
    if not row:
        raise ValueError("无效或已撤销的会话令牌")
    if row.get("expires_at"):
        exp = parse(row["expires_at"])
        if exp and exp < now():
            raise ValueError("会话令牌已过期")
    acc = accounts.get_account(row["account_id"])
    if not acc or not acc["active"]:
        raise ValueError("账号已停用")
    p = accounts.principal_from_account(acc)
    p["auth"] = "session"
    p["session_id"] = row["id"]
    return p


def login(kind: str, ident: str, ttl_hours: int | None = 72) -> dict:
    """演示登录：用工号/学号换不透明会话令牌。

    不设密码——接校内 IdP 时替换本函数即可，路由与 RBAC 不动。
    """
    # 先确保账号存在（教师/学生可能刚 seed 完还没 sync）
    if kind == "teacher":
        t = get_db().query_one("SELECT * FROM teacher WHERE code=?", (ident,))
        if not t:
            raise ValueError(f"未知教师工号 {ident}")
        from packages.core.db import loads

        aid = accounts.upsert_account(
            "teacher", t["code"], t["name"], "teacher", loads(t.get("klasses"), [])
        )
    elif kind == "admin":
        acc = accounts.find_account("admin", ident)
        if not acc:
            raise ValueError(f"未知管理员 {ident}")
        aid = acc["id"]
    elif kind == "student":
        s = get_db().query_one("SELECT * FROM student WHERE sid=?", (ident,))
        if not s:
            raise ValueError(f"未知学号 {ident}")
        aid = accounts.upsert_account(
            "student", s["sid"], s["name"], "student",
            [s["klass"]] if s.get("klass") else [],
        )
    else:
        raise ValueError("kind 须为 teacher / student / admin")
    out = issue(aid, ttl_hours=ttl_hours, note="login")
    out["principal"] = accounts.principal_from_account(accounts.get_account(aid))
    return out
