"""账号：把 teacher / student 挂到 RBAC 角色。"""
from __future__ import annotations

from packages.core.db import dumps, get_db, loads
from packages.core.timeutil import now_str

from . import rbac


def upsert_account(
    kind: str,
    ident: str,
    display_name: str,
    role_code: str,
    klasses: list | None = None,
    active: int = 1,
) -> int:
    """幂等创建/更新账号。kind ∈ {teacher, student, admin}。"""
    if kind not in ("teacher", "student", "admin"):
        raise ValueError(f"未知账号类型 {kind}")
    if role_code not in ("admin", "teacher", "ta", "student"):
        raise ValueError(f"未知角色 {role_code}")
    db = get_db()
    row = db.query_one(
        "SELECT id FROM auth_account WHERE kind=? AND ident=?", (kind, ident)
    )
    kl = dumps(klasses if klasses is not None else [])
    if row:
        db.execute(
            "UPDATE auth_account SET display_name=?, role_code=?, klasses=?, active=?"
            " WHERE id=?",
            (display_name, role_code, kl, active, row["id"]),
        )
        return row["id"]
    return db.execute(
        "INSERT INTO auth_account(kind, ident, display_name, role_code, klasses,"
        " active, created_at) VALUES(?,?,?,?,?,?,?)",
        (kind, ident, display_name, role_code, kl, active, now_str()),
    )


def get_account(account_id: int) -> dict | None:
    return get_db().query_one("SELECT * FROM auth_account WHERE id=?", (account_id,))


def find_account(kind: str, ident: str) -> dict | None:
    return get_db().query_one(
        "SELECT * FROM auth_account WHERE kind=? AND ident=? AND active=1",
        (kind, ident),
    )


def list_accounts(role_code: str | None = None, limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM auth_account WHERE active=1"
    args: list = []
    if role_code:
        sql += " AND role_code=?"
        args.append(role_code)
    sql += " ORDER BY role_code, ident LIMIT ?"
    args.append(limit)
    rows = get_db().query(sql, args)
    for r in rows:
        r["klasses"] = loads(r.get("klasses"), [])
        r["permissions"] = sorted(rbac.permissions_of(r["role_code"]))
    return rows


def sync_from_legacy() -> dict:
    """从既有 teacher / student 表同步账号（幂等）。

    - teacher → role=teacher（王主任等空班级名单保持可见全部）
    - student → role=student
    - 额外确保有一个 admin 演示账号 A001
    """
    db = get_db()
    rbac.ensure_matrix()
    n_t = n_s = 0
    for t in db.query("SELECT code, name, klasses FROM teacher"):
        upsert_account(
            "teacher", t["code"], t["name"], "teacher",
            loads(t.get("klasses"), []),
        )
        n_t += 1
    for s in db.query("SELECT sid, name, klass FROM student"):
        upsert_account(
            "student", s["sid"], s["name"], "student",
            [s["klass"]] if s.get("klass") else [],
        )
        n_s += 1
    # 教务演示账号：空 klasses = 全可见
    upsert_account("admin", "A001", "教务演示", "admin", [])
    return {"teachers": n_t, "students": n_s, "admin": 1}


def principal_from_account(acc: dict) -> dict:
    """把 auth_account 行展开成路由层 principal。"""
    klasses = loads(acc.get("klasses"), [])
    role = acc["role_code"]
    out = {
        "role": role,
        "account_id": acc["id"],
        "kind": acc["kind"],
        "ident": acc["ident"],
        "name": acc["display_name"],
        "klasses": klasses,
        "permissions": sorted(rbac.permissions_of(role)),
        "auth": "account",
    }
    if role == "student" or acc["kind"] == "student":
        st = get_db().query_one("SELECT * FROM student WHERE sid=?", (acc["ident"],))
        if st:
            out["student_id"] = st["id"]
            out["sid"] = st["sid"]
            out["klass"] = st["klass"]
            out["role"] = "student"
    if acc["kind"] == "teacher" or role in ("teacher", "ta", "admin"):
        out["code"] = acc["ident"]
        # admin 走教师数据权限路径时，空 klasses = 全班可见（与 T003 一致）
        if role == "admin":
            out["klasses"] = []
    return out
