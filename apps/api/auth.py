"""鉴权与数据权限。

数据安全要求（技术方案 §6.1）：
- 学生只能看自己的数据；
- 教师可见本班聚合数据与个体诊断，**不可见其他班级**；
- 系统不生成、不留存任何针对教师的评价性数据（见 tests/test_no_teacher_eval.py）。

演示令牌格式：`teacher:<工号>` / `student:<学号>`。
接校内统一身份认证时替换 resolve() 即可，路由层不动。

**考试令牌是例外，必须单独看待。** `exam:<随机串>` 由 packages/exam/session 在开考时
签发，role='examinee'，只能走标记了 role="examinee" 的考试接口。
理由：教学侧的 `student:2026001` 是可猜的明文格式，用它来考试等于分数可改；
而考生令牌一旦泄漏也只能看到自己那张卷子，看不到掌握度、看不到别人、更看不到答案。
"""
from __future__ import annotations

from packages.core.db import get_db, loads
from packages.core.timeutil import now_str

from .microapi import HTTPError, Request


def resolve(token: str) -> dict:
    if not token:
        raise HTTPError(401, "缺少身份令牌")
    if ":" not in token:
        raise HTTPError(401, "令牌格式应为 teacher:<工号> 或 student:<学号>")
    role, ident = token.split(":", 1)
    db = get_db()
    if role == "teacher":
        t = db.query_one("SELECT * FROM teacher WHERE code=?", (ident,))
        if not t:
            raise HTTPError(401, f"未知教师工号 {ident}")
        return {"role": "teacher", "code": t["code"], "name": t["name"],
                "klasses": loads(t["klasses"], [])}
    if role == "exam":
        # 考试会话令牌。故意不查 student 表以外的任何东西——
        # 这个身份的权限边界就是"我这一场考试的我这张卷子"。
        from packages.exam import session as exam_session

        try:
            se = exam_session.resolve_token(ident)
        except ValueError as exc:
            raise HTTPError(401, str(exc)) from exc
        st = db.query_one("SELECT * FROM student WHERE id=?", (se["student_id"],))
        return {"role": "examinee", "session_id": se["id"], "exam_id": se["exam_id"],
                "student_id": se["student_id"], "sid": st["sid"] if st else "",
                "name": st["name"] if st else "", "exam_token": ident}
    if role == "student":
        s = db.query_one("SELECT * FROM student WHERE sid=?", (ident,))
        if not s:
            raise HTTPError(401, f"未知学号 {ident}")
        return {"role": "student", "student_id": s["id"], "sid": s["sid"],
                "name": s["name"], "klass": s["klass"]}
    raise HTTPError(401, "未知角色")


def middleware(req: Request, meta: dict) -> None:
    if meta.get("public"):
        return
    token = req.headers.get("x-auth-token") or req.q("token") or ""
    req.principal = resolve(token)
    need = meta.get("role")
    # 考生身份默认哪儿都进不去：只有显式标了 role="examinee" 的接口才放行。
    # 不这么写的话，所有"没标 role"的教学接口都会对考生敞开——
    # 而考生的 principal 里是有 student_id 的，那就等于考试中能查自己的掌握度。
    if req.principal["role"] == "examinee" and need != "examinee":
        raise HTTPError(403, "考试令牌只能访问考试接口")
    if need and req.principal["role"] != need:
        raise HTTPError(403, f"该接口仅限 {need} 访问")


def assert_can_view_student(req: Request, student_id: int) -> None:
    p = req.principal
    if p.get("role") == "student":
        if p.get("student_id") != student_id:
            raise HTTPError(403, "学生只能查看自己的数据")
        return
    if p.get("role") == "teacher":
        s = get_db().query_one("SELECT klass FROM student WHERE id=?", (student_id,))
        if not s:
            raise HTTPError(404, "学生不存在")
        if p.get("klasses") and s["klass"] not in p["klasses"]:
            raise HTTPError(403, "教师不可见其他班级的数据")


def assert_can_view_class(req: Request, klass: str | None) -> None:
    p = req.principal
    if p.get("role") != "teacher":
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
