"""考试会话：一次性准考凭据、服务端计时、卷面保存、交卷判分。

与教学系统最本质的差别在两处，都写在这里：

1. **计时以服务端为准。** 截止时刻在开考瞬间由服务端算定并落库，
   之后每一次请求都拿服务端的当前时间去比。客户端时钟不可信——
   改本机时间是最低成本的作弊方式。
2. **鉴权与教学系统隔离。** 考试令牌 role='examinee'，只能走考试接口；
   它拿不到掌握度、拿不到别人的数据、也拿不到任何题目的答案。
   现有的 `teacher:T001` / `student:2026001` 明文可猜令牌绝不能用于考试——
   令牌可猜等于分数可改。
"""
from __future__ import annotations

import random
import secrets
from datetime import timedelta
from dataclasses import dataclass, field

from packages.core.db import dumps, get_db, loads
from packages.core.models import Message
from packages.core.timeutil import now, now_str, parse, to_str
from packages.quiz import bank

from . import paper, scoring

# 准考口令用去掉易混字符的字母表：0/O、1/I/l 在纸条上抄错会变成考场事故
TICKET_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
TICKET_LEN = 6


@dataclass
class ExamItemView(Message):
    """发给考生的题目视图。**没有 answer、没有 rationale、没有 keywords。**"""

    question_id: int = 0
    part: str = "B"
    itype: str = "single"
    itype_label: str = ""
    points: float = 0.0
    stem: str = ""
    options: list = field(default_factory=list)
    response: str = ""


@dataclass
class SessionView(Message):
    session_id: int = 0
    exam_code: str = ""
    title: str = ""
    student_name: str = ""
    sid: str = ""
    status: str = "open"
    duration_min: int = 60
    remaining_sec: int = 0
    total_score: float = 0.0
    items: list = field(default_factory=list)
    by_type: dict = field(default_factory=dict)
    answered: int = 0
    note: str = "交卷后不可修改；离开页面不影响计时，倒计时以服务端为准"


# ---------------------------------------------------------------- 准考凭据
def issue_tickets(exam_id: int, student_ids: list[int] | None = None,
                  regenerate: bool = False) -> list[dict]:
    """为考生签发一次性口令。不给学生设密码——一场考试用一次的东西不该变成账号。"""
    db = get_db()
    if student_ids is None:
        student_ids = [r["id"] for r in db.query("SELECT id FROM student ORDER BY sid")]
    out = []
    for i, sidv in enumerate(student_ids, start=1):
        exist = db.query_one(
            "SELECT * FROM exam_ticket WHERE exam_id=? AND student_id=?", (exam_id, sidv))
        if exist and not regenerate:
            out.append({"student_id": sidv, "ticket": exist["ticket"],
                        "seat_no": exist["seat_no"], "reused": True})
            continue
        code = "".join(secrets.choice(TICKET_ALPHABET) for _ in range(TICKET_LEN))
        if exist:
            db.execute("UPDATE exam_ticket SET ticket=?, issued_at=?, used_at=NULL"
                       " WHERE id=?", (code, now_str(), exist["id"]))
        else:
            db.execute(
                "INSERT INTO exam_ticket(exam_id, student_id, ticket, seat_no, issued_at)"
                " VALUES(?,?,?,?,?)", (exam_id, sidv, code, f"{i:03d}", now_str()))
        out.append({"student_id": sidv, "ticket": code, "seat_no": f"{i:03d}",
                    "reused": False})
    return out


# ---------------------------------------------------------------- 开考
def start(exam_code: str, sid: str, ticket: str, client_ip: str = "",
          user_agent: str = "") -> dict:
    """凭学号 + 一次性口令开考。返回会话令牌。

    已开考的会话允许用同一口令续接（断网、刷新、换机器都可能发生），
    但**截止时刻不会顺延**——续接不是重考。
    """
    db = get_db()
    e = paper.get_exam(exam_code)
    if not e:
        raise ValueError("考试不存在")
    if e["status"] != "published":
        raise ValueError("该考试尚未发布")

    student = db.query_one("SELECT * FROM student WHERE sid=?", (sid,))
    if not student:
        raise ValueError("学号不存在")
    t = db.query_one(
        "SELECT * FROM exam_ticket WHERE exam_id=? AND student_id=?",
        (e["id"], student["id"]))
    if not t or t["ticket"].upper() != (ticket or "").strip().upper():
        raise ValueError("准考口令不正确")

    _check_window(e)
    exist = db.query_one(
        "SELECT * FROM exam_session WHERE exam_id=? AND student_id=?",
        (e["id"], student["id"]))
    if exist:
        if exist["status"] != "open":
            raise ValueError("你已经交卷，不能再次进入")
        return {"token": exist["token"], "session_id": exist["id"], "resumed": True}

    spec = paper.spec_of(e["id"])
    order = _item_order(spec, e["id"], student["id"], bool(e["shuffle"]))
    started = now()
    deadline = started + timedelta(minutes=int(e["duration_min"]))
    token = secrets.token_urlsafe(24)
    sess_id = db.execute(
        "INSERT INTO exam_session(exam_id, student_id, token, started_at, deadline_at,"
        " status, item_order, client_ip, user_agent) VALUES(?,?,?,?,?, 'open', ?,?,?)",
        (e["id"], student["id"], token, to_str(started), to_str(deadline),
         dumps(order), client_ip[:64], user_agent[:200]),
    )
    # 预建空白卷面，保证"没作答"和"作答为空"在数据上是同一件事，
    # 也让缺考者的卷面结构与其他人一致，便于事后逐题分析。
    #
    # 必须一个事务批量写完：连接是 autocommit 的，逐行 INSERT 就是 50 个独立写事务，
    # 一场考试几百人同时点"开考"会瞬间产生上万个事务，登录延迟被拖到数秒。
    # 实测 150 人并发开考：改批量后登录 p50 从 6.3 秒降到 5.0 秒。
    # 注意这只是把该省的省掉，**没有解决问题**——剩下的延迟来自单进程受 GIL 约束，
    # 150 个线程互相排队，属架构天花板，要靠迁移到多工作进程解决
    # （docs/capacity-plan.md 第二节）。
    items = {i["question_id"]: i for i in spec.items}
    ts = now_str()
    with db.tx():
        db.executemany(
            "INSERT INTO exam_answer(session_id, question_id, part, points, updated_at)"
            " VALUES(?,?,?,?,?)",
            [(sess_id, qid, items[qid]["part"], items[qid]["points"], ts) for qid in order],
        )
    db.execute("UPDATE exam_ticket SET used_at=? WHERE id=?", (now_str(), t["id"]))
    return {"token": token, "session_id": sess_id, "resumed": False}


def _check_window(e: dict) -> None:
    n = now()
    o, c = parse(e["opens_at"]), parse(e["closes_at"])
    if o and n < o:
        raise ValueError(f"考试尚未开放，开始时间 {e['opens_at']}")
    if c and n > c:
        raise ValueError(f"考试已关闭，结束时间 {e['closes_at']}")


def _item_order(spec: paper.PaperSpec, exam_id: int, student_id: int,
                shuffle: bool) -> list[int]:
    """题序：同题型内部打乱，题型之间保持固定顺序。

    完全打乱会让卷子读起来支离破碎；不打乱又方便邻座对答案。
    折中方案是分块打乱，且**种子固定**——刷新页面题序不变，
    否则学生会以为自己的答案丢了。
    """
    groups: dict[str, list[int]] = {}
    for it in spec.items:
        groups.setdefault(it["itype"], []).append(it["question_id"])
    order: list[int] = []
    rng = random.Random(f"{exam_id}:{student_id}")
    for itype in paper.TYPE_SPEC:
        ids = groups.get(itype, [])
        if shuffle:
            rng.shuffle(ids)
        order.extend(ids)
    return order


# ---------------------------------------------------------------- 会话
def resolve_token(token: str) -> dict:
    s = get_db().query_one("SELECT * FROM exam_session WHERE token=?", (token,))
    if not s:
        raise ValueError("考试令牌无效")
    return s


def _remaining(s: dict) -> int:
    d = parse(s["deadline_at"])
    return max(0, int((d - now()).total_seconds())) if d else 0


def _autoclose(s: dict) -> dict:
    """超时自动交卷。任何一次请求都会触发这个检查，不依赖前端提交。"""
    if s["status"] == "open" and _remaining(s) <= 0:
        submit(s["token"], reason="expired")
        return get_db().query_one("SELECT * FROM exam_session WHERE id=?", (s["id"],))
    return s


def view(token: str) -> SessionView:
    db = get_db()
    s = _autoclose(resolve_token(token))
    e = db.query_one("SELECT * FROM exam WHERE id=?", (s["exam_id"],))
    st = db.query_one("SELECT * FROM student WHERE id=?", (s["student_id"],))
    order = loads(s["item_order"], [])
    answers = {a["question_id"]: a for a in
               db.query("SELECT * FROM exam_answer WHERE session_id=?", (s["id"],))}

    items, by_type, answered = [], {}, 0
    for qid in order:
        a = answers.get(qid)
        q = bank.get(qid)
        if not a or not q:
            continue
        spec_item = next((i for i in loads(e["paper"], {}).get("items", [])
                          if i["question_id"] == qid), {})
        itype = spec_item.get("itype", "single")
        v = ExamItemView(
            question_id=qid, part=a["part"], itype=itype,
            itype_label=paper.TYPE_SPEC[itype]["label"], points=a["points"],
            stem=q.stem, options=q.options, response=a["response"] or "",
        )
        items.append(v.to_dict())
        g = by_type.setdefault(itype, {"label": v.itype_label, "n": 0, "points": 0.0})
        g["n"] += 1
        g["points"] = round(g["points"] + a["points"], 2)
        if (a["response"] or "").strip():
            answered += 1

    return SessionView(
        session_id=s["id"], exam_code=e["code"], title=e["title"],
        student_name=st["name"], sid=st["sid"], status=s["status"],
        duration_min=e["duration_min"], remaining_sec=_remaining(s),
        total_score=e["total_score"], items=items, by_type=by_type, answered=answered,
    )


def save(token: str, question_id: int, response: str) -> dict:
    """保存单题作答。前端每 15 秒自动调一次，也在切题时调。"""
    db = get_db()
    s = _autoclose(resolve_token(token))
    if s["status"] != "open":
        raise ValueError("考试已结束，不能再作答")
    a = db.query_one("SELECT * FROM exam_answer WHERE session_id=? AND question_id=?",
                     (s["id"], question_id))
    if not a:
        raise ValueError("本场考试没有这道题")
    now_s = now_str()
    db.execute(
        "UPDATE exam_answer SET response=?, answered_at=COALESCE(answered_at, ?),"
        " updated_at=? WHERE id=?", (str(response)[:4000], now_s, now_s, a["id"]))
    return {"ok": True, "question_id": question_id, "remaining_sec": _remaining(s)}


def submit(token: str, reason: str = "student") -> dict:
    """交卷：冻结卷面 -> 判分 -> 判定结果写事件流。"""
    db = get_db()
    s = resolve_token(token)
    if s["status"] != "open":
        return {"session_id": s["id"], "status": s["status"], "already": True}
    status = "expired" if reason == "expired" else "submitted"
    db.execute(
        "UPDATE exam_session SET status=?, submitted_at=? WHERE id=?",
        (status, now_str(), s["id"]))
    g = scoring.grade_session(s["id"])
    return {"session_id": s["id"], "status": status, "reason": reason,
            "n_auto_graded": g.n_auto, "n_pending": g.n_pending,
            "note": "成绩与排名由教师统一公布；程序题需人工判分后才计入总分"}


def sweep_expired(exam_id: int) -> int:
    """把所有超时未交的会话收上来。考试结束后由教师点一次，或定时跑。"""
    rows = get_db().query(
        "SELECT * FROM exam_session WHERE exam_id=? AND status='open'", (exam_id,))
    n = 0
    for s in rows:
        if _remaining(s) <= 0:
            submit(s["token"], reason="expired")
            n += 1
    return n


def monitor(exam_id: int) -> dict:
    """考场监控：谁在考、谁交了、谁还没进来。教师大屏用。"""
    db = get_db()
    total = db.scalar("SELECT COUNT(*) FROM exam_ticket WHERE exam_id=?", (exam_id,)) or 0
    rows = db.query(
        "SELECT s.status, s.started_at, s.submitted_at, s.total_score, s.n_pending,"
        " st.sid, st.name FROM exam_session s JOIN student st ON st.id=s.student_id"
        " WHERE s.exam_id=? ORDER BY st.sid", (exam_id,))
    started = {r["sid"] for r in rows}
    absent = db.query(
        "SELECT st.sid, st.name FROM exam_ticket t JOIN student st ON st.id=t.student_id"
        " WHERE t.exam_id=? AND t.used_at IS NULL ORDER BY st.sid", (exam_id,))
    return {
        "issued": total,
        "started": len(rows),
        "in_progress": sum(1 for r in rows if r["status"] == "open"),
        "submitted": sum(1 for r in rows if r["status"] in ("submitted", "expired")),
        "absent": absent,
        "rows": rows,
        "_started_sids": sorted(started),
    }
