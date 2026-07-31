"""L2 状态层仓储。"""
from __future__ import annotations

from packages.core.db import dumps, get_db, loads
from packages.core.timeutil import now_str

from .bkt import BKTParams


# ---------------- 学生 ----------------
def upsert_student(sid: str, name: str, cohort: str = "", klass: str = "") -> int:
    db = get_db()
    row = db.query_one("SELECT id FROM student WHERE sid=?", (sid,))
    if row:
        db.execute(
            "UPDATE student SET name=?, cohort=?, klass=? WHERE id=?",
            (name, cohort, klass, row["id"]),
        )
        return row["id"]
    return db.execute(
        "INSERT INTO student(sid, name, cohort, klass, enrolled_at) VALUES(?,?,?,?,?)",
        (sid, name, cohort, klass, now_str()),
    )


def get_student(student_id: int) -> dict | None:
    return get_db().query_one("SELECT * FROM student WHERE id=?", (student_id,))


def get_student_by_sid(sid: str) -> dict | None:
    return get_db().query_one("SELECT * FROM student WHERE sid=?", (sid,))


def list_students(klass: str | None = None) -> list[dict]:
    if klass:
        return get_db().query("SELECT * FROM student WHERE klass=? ORDER BY sid", (klass,))
    return get_db().query("SELECT * FROM student ORDER BY sid")


# ---------------- 事件流（只追加） ----------------
def append_event(
    student_id: int,
    event_type: str,
    kp_id: int | None = None,
    is_correct: bool | None = None,
    source: str = "",
    source_ref: str = "",
    occurred_at: str | None = None,
    payload: dict | None = None,
) -> int:
    return get_db().execute(
        "INSERT INTO learning_event(student_id, kp_id, event_type, is_correct, source,"
        " source_ref, occurred_at, payload) VALUES(?,?,?,?,?,?,?,?)",
        (
            student_id,
            kp_id,
            event_type,
            None if is_correct is None else int(is_correct),
            source,
            source_ref,
            occurred_at or now_str(),
            dumps(payload or {}),
        ),
    )


def get_event(event_id: int) -> dict | None:
    r = get_db().query_one("SELECT * FROM learning_event WHERE id=?", (event_id,))
    if r:
        r["payload"] = loads(r["payload"], {})
    return r


def list_events(
    student_id: int | None = None,
    kp_id: int | None = None,
    event_type: str | None = None,
    since: str | None = None,
    limit: int | None = None,
    order: str = "occurred_at",
) -> list[dict]:
    """order='id' 取**记录顺序**（事件流的规范顺序，重算以此为准）；
    order='occurred_at' 取时间顺序，仅用于展示与曲线聚合。"""
    sql = "SELECT * FROM learning_event WHERE 1=1"
    params: list = []
    if student_id:
        sql += " AND student_id=?"
        params.append(student_id)
    if kp_id:
        sql += " AND kp_id=?"
        params.append(kp_id)
    if event_type:
        sql += " AND event_type=?"
        params.append(event_type)
    if since:
        sql += " AND occurred_at>=?"
        params.append(since)
    sql += " ORDER BY id" if order == "id" else " ORDER BY occurred_at, id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = get_db().query(sql, params)
    for r in rows:
        r["payload"] = loads(r["payload"], {})
    return rows


def count_events(**kw) -> int:
    return len(list_events(**kw))


# ---------------- 掌握度 ----------------
def get_mastery(student_id: int, kp_id: int) -> dict | None:
    return get_db().query_one(
        "SELECT * FROM mastery_state WHERE student_id=? AND kp_id=?", (student_id, kp_id)
    )


def mastery_vector(student_id: int) -> dict[int, float]:
    return {
        r["kp_id"]: r["p_mastery"]
        for r in get_db().query(
            "SELECT kp_id, p_mastery FROM mastery_state WHERE student_id=?", (student_id,)
        )
    }


def mastery_rows(student_id: int) -> list[dict]:
    return get_db().query(
        "SELECT m.*, k.code, k.name, k.unit FROM mastery_state m"
        " JOIN knowledge_point k ON k.id=m.kp_id WHERE m.student_id=? ORDER BY k.id",
        (student_id,),
    )


def class_mastery(course_id: int, klass: str | None = None) -> list[dict]:
    sql = (
        "SELECT m.kp_id, k.code, k.name, k.unit, AVG(m.p_mastery) AS avg_mastery,"
        " AVG(m.confidence) AS avg_conf, COUNT(*) AS n_students,"
        " SUM(m.evidence_count) AS evidence_count"
        " FROM mastery_state m JOIN knowledge_point k ON k.id=m.kp_id"
        " JOIN student s ON s.id=m.student_id WHERE k.course_id=?"
    )
    params: list = [course_id]
    if klass:
        sql += " AND s.klass=?"
        params.append(klass)
    sql += " GROUP BY m.kp_id, k.code, k.name, k.unit ORDER BY avg_mastery ASC"
    return get_db().query(sql, params)


def write_mastery(
    student_id: int,
    kp_id: int,
    p_mastery: float,
    confidence: float,
    evidence_count: int,
    last_event_id: int,
) -> None:
    """唯一的掌握度写入口。last_event_id 必填，由 DB 触发器强制。"""
    if last_event_id is None:
        raise ValueError("掌握度必须挂 LearningEvent")
    get_db().execute(
        "INSERT INTO mastery_state(student_id, kp_id, p_mastery, confidence, evidence_count,"
        " last_event_id, updated_at) VALUES(?,?,?,?,?,?,?)"
        " ON CONFLICT(student_id, kp_id) DO UPDATE SET p_mastery=excluded.p_mastery,"
        " confidence=excluded.confidence, evidence_count=excluded.evidence_count,"
        " last_event_id=excluded.last_event_id, updated_at=excluded.updated_at",
        (student_id, kp_id, p_mastery, confidence, evidence_count, last_event_id, now_str()),
    )


def clear_derived_state(student_id: int | None = None) -> None:
    """仅供 replay 校验：清掉派生态后由事件流重算。事件流本身不动。"""
    db = get_db()
    if student_id:
        db.execute("DELETE FROM mastery_state WHERE student_id=?", (student_id,))
        db.execute("DELETE FROM ability_profile WHERE student_id=?", (student_id,))
    else:
        db.execute("DELETE FROM mastery_state")
        db.execute("DELETE FROM ability_profile")


# ---------------- BKT 参数 ----------------
def get_params(kp_id: int) -> BKTParams:
    r = get_db().query_one("SELECT * FROM bkt_param WHERE kp_id=?", (kp_id,))
    if not r:
        return BKTParams.default()
    return BKTParams(r["p_init"], r["p_transit"], r["p_slip"], r["p_guess"])


def all_params() -> dict[int, BKTParams]:
    rows = get_db().query("SELECT * FROM bkt_param")
    return {
        r["kp_id"]: BKTParams(r["p_init"], r["p_transit"], r["p_slip"], r["p_guess"])
        for r in rows
    }


def set_params(kp_id: int, prm: BKTParams, fitted_n: int = 0) -> None:
    get_db().execute(
        "INSERT INTO bkt_param(kp_id, p_init, p_transit, p_slip, p_guess, fitted_n, updated_at)"
        " VALUES(?,?,?,?,?,?,?) ON CONFLICT(kp_id) DO UPDATE SET p_init=excluded.p_init,"
        " p_transit=excluded.p_transit, p_slip=excluded.p_slip, p_guess=excluded.p_guess,"
        " fitted_n=excluded.fitted_n, updated_at=excluded.updated_at",
        (kp_id, prm.p_init, prm.p_transit, prm.p_slip, prm.p_guess, fitted_n, now_str()),
    )


# ---------------- 能力画像 ----------------
def write_ability(student_id: int, module_id: int, level: float, confidence: float) -> None:
    get_db().execute(
        "INSERT INTO ability_profile(student_id, module_id, level, confidence, updated_at)"
        " VALUES(?,?,?,?,?) ON CONFLICT(student_id, module_id) DO UPDATE SET"
        " level=excluded.level, confidence=excluded.confidence, updated_at=excluded.updated_at",
        (student_id, module_id, level, confidence, now_str()),
    )


def ability_rows(student_id: int) -> list[dict]:
    return get_db().query(
        "SELECT a.*, m.code, m.name FROM ability_profile a"
        " JOIN ability_module m ON m.id=a.module_id WHERE a.student_id=? ORDER BY m.code",
        (student_id,),
    )
