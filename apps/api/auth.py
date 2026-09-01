"""鉴权与数据权限（RBAC）。

数据安全要求（技术方案 §6.1）：
- 学生只能看自己的数据；
- 教师可见本班聚合数据与个体诊断，**不可见其他班级**；
- 系统不生成、不留存任何针对教师的评价性数据（见 tests/test_no_teacher_eval.py）。

令牌（按优先级解析）：
1. `session:<不透明串>` —— 推荐；由 `/api/auth/login` 签发
2. `exam:<随机串>` —— 考试专用，只能进 examinee 接口
3. `teacher:<工号>` / `student:<学号>` / `admin:<工号>` —— 演示兼容明文
   （接校内统一身份时替换 resolve() 即可，路由层不动）

**考试令牌是例外，必须单独看待。** 教学侧明文令牌可猜，绝不能用来考试。
"""
from __future__ import annotations

from packages.auth import accounts, rbac, sessions
from packages.auth.permissions import ROLE_ALIASES
from packages.core.db import get_db, loads
from packages.core.timeutil import now_str

from .microapi import HTTPError, Request


def resolve(token: str) -> dict:
    if not token:
        raise HTTPError(401, "缺少身份令牌")
    if ":" not in token:
        raise HTTPError(401, "令牌格式应为 session:<…> / teacher:<工号> / student:<学号>")
    role, ident = token.split(":", 1)
    if not ident:
        raise HTTPError(401, "令牌缺少身份标识")

    if role == "session":
        try:
            return sessions.resolve_session_token(ident)
        except ValueError as exc:
            raise HTTPError(401, str(exc)) from exc

    if role == "exam":
        # 考试会话令牌。故意不查 student 表以外的任何东西——
        # 这个身份的权限边界就是"我这一场考试的我这张卷子"。
        from packages.exam import session as exam_session

        try:
            se = exam_session.resolve_token(ident)
        except ValueError as exc:
            raise HTTPError(401, str(exc)) from exc
        st = get_db().query_one("SELECT * FROM student WHERE id=?", (se["student_id"],))
        return {
            "role": "examinee",
            "session_id": se["id"],
            "exam_id": se["exam_id"],
            "student_id": se["student_id"],
            "sid": st["sid"] if st else "",
            "name": st["name"] if st else "",
            "exam_token": ident,
            "permissions": [],
            "auth": "exam",
        }

    if role == "admin":
        acc = accounts.find_account("admin", ident)
        if not acc:
            # 兼容：尚未 sync 时允许用 seed 的 A001 直接建
            if ident == "A001":
                accounts.upsert_account("admin", "A001", "教务演示", "admin", [])
                acc = accounts.find_account("admin", ident)
            if not acc:
                raise HTTPError(401, f"未知管理员 {ident}")
        return accounts.principal_from_account(acc)

    if role == "teacher":
        # 优先走 auth_account（可挂 ta / 额外权限）；回退 teacher 表
        acc = accounts.find_account("teacher", ident)
        if acc:
            return accounts.principal_from_account(acc)
        t = get_db().query_one("SELECT * FROM teacher WHERE code=?", (ident,))
        if not t:
            raise HTTPError(401, f"未知教师工号 {ident}")
        klasses = loads(t["klasses"], [])
        return {
            "role": "teacher",
            "code": t["code"],
            "name": t["name"],
            "klasses": klasses,
            "ident": t["code"],
            "permissions": sorted(rbac.permissions_of("teacher")),
            "auth": "legacy",
        }

    if role == "student":
        acc = accounts.find_account("student", ident)
        if acc:
            return accounts.principal_from_account(acc)
        s = get_db().query_one("SELECT * FROM student WHERE sid=?", (ident,))
        if not s:
            raise HTTPError(401, f"未知学号 {ident}")
        return {
            "role": "student",
            "student_id": s["id"],
            "sid": s["sid"],
            "name": s["name"],
            "klass": s["klass"],
            "ident": s["sid"],
            "klasses": [s["klass"]] if s.get("klass") else [],
            "permissions": sorted(rbac.permissions_of("student")),
            "auth": "legacy",
        }

    raise HTTPError(401, "未知角色")


def _role_allowed(principal_role: str, need: str) -> bool:
    """路由 meta.role 与实际角色的兼容（admin 可进 teacher 接口）。"""
    if not need:
        return True
    allowed = ROLE_ALIASES.get(need, frozenset({need}))
    return principal_role in allowed


def middleware(req: Request, meta: dict) -> None:
    if meta.get("public"):
        return
    token = req.headers.get("x-auth-token") or req.q("token") or ""
    req.principal = resolve(token)
    need_role = meta.get("role")
    need_perm = meta.get("perm")

    # 考生身份默认哪儿都进不去：只有显式标了 role="examinee" 的接口才放行。
    if req.principal["role"] == "examinee" and need_role != "examinee":
        raise HTTPError(403, "考试令牌只能访问考试接口")

    if need_role and not _role_allowed(req.principal["role"], need_role):
        raise HTTPError(403, f"该接口仅限 {need_role} 访问")

    if need_perm:
        require_perm(req, need_perm)


def require_perm(req: Request, perm: str) -> None:
    """显式权限检查。principal.permissions 缺失时按角色矩阵回退。"""
    p = req.principal
    if p.get("role") == "examinee":
        raise HTTPError(403, "考试令牌无教学权限")
    perms = set(p.get("permissions") or [])
    if not perms:
        perms = rbac.permissions_of(p.get("role") or "")
    if perm not in perms:
        raise HTTPError(403, f"缺少权限 {perm}")


def actor_id(req: Request) -> str:
    """流程审计用的操作者标识。"""
    p = req.principal
    return p.get("code") or p.get("sid") or p.get("ident") or p.get("role") or "unknown"


def assert_can_view_student(req: Request, student_id: int) -> None:
    p = req.principal
    role = p.get("role")
    if role == "student":
        if p.get("student_id") != student_id:
            raise HTTPError(403, "学生只能查看自己的数据")
        return
    if role in ("teacher", "ta", "admin"):
        s = get_db().query_one("SELECT klass FROM student WHERE id=?", (student_id,))
        if not s:
            raise HTTPError(404, "学生不存在")
        # 空白名单 = 可见全部（主任 / admin）
        if p.get("klasses") and s["klass"] not in p["klasses"]:
            raise HTTPError(403, "教师不可见其他班级的数据")
        return
    raise HTTPError(403, "无权查看学生数据")


def assert_can_view_class(req: Request, klass: str | None) -> None:
    p = req.principal
    if p.get("role") not in ("teacher", "ta", "admin"):
        raise HTTPError(403, "班级数据仅限教师访问")
    if klass and p.get("klasses") and klass not in p["klasses"]:
        raise HTTPError(403, "教师不可见其他班级的数据")


def log_report_open(teacher_code: str, kind: str) -> None:
    """记录教师主动打开诊断报告的次数。

    Phase 1 验收指标：教师周主动打开 ≥ 3 次。
    这是对系统的观测，**不是对教师的评价**，不进入任何画像或考核。
    """
    get_db().execute(
        "INSERT INTO report_open_log(teacher_code, report_kind, opened_at) VALUES(?,?,?)",
        (teacher_code, kind, now_str()),
    )


def report_open_stats(days: int = 7) -> list[dict]:
    return get_db().query(
        "SELECT teacher_code, report_kind, COUNT(*) AS opens FROM report_open_log"
        " WHERE opened_at >= datetime('now', ?) GROUP BY teacher_code, report_kind",
        (f"-{days} days",),
    )
