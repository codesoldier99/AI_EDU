"""L1 图谱仓储。所有 SQL 只允许出现在本文件与同层 algo 中。"""
from __future__ import annotations

from functools import lru_cache

from packages.core.db import dumps, get_db
from packages.core.timeutil import now_str

from .models import AbilityModule, Edge, GraphStats, KnowledgePoint, ProjectTask


# ---------------- 课程 ----------------
def upsert_course(code: str, name: str, credit: float = 0, term: str = "") -> int:
    db = get_db()
    row = db.query_one("SELECT id FROM course WHERE code=?", (code,))
    if row:
        db.execute(
            "UPDATE course SET name=?, credit=?, term=? WHERE id=?",
            (name, credit, term, row["id"]),
        )
        return row["id"]
    return db.execute(
        "INSERT INTO course(code, name, credit, term) VALUES(?,?,?,?)",
        (code, name, credit, term),
    )


def get_course(code: str) -> dict | None:
    return get_db().query_one("SELECT * FROM course WHERE code=?", (code,))


def get_course_by_id(course_id: int) -> dict | None:
    return get_db().query_one("SELECT * FROM course WHERE id=?", (course_id,))


def list_courses() -> list[dict]:
    return get_db().query("SELECT * FROM course ORDER BY id")


# ---------------- 知识点 ----------------
def upsert_kp(
    course_id: int,
    code: str,
    name: str,
    description: str = "",
    granularity: str = "atomic",
    kp_type: str = "concept",
    difficulty: float = 0.5,
    unit: str = "",
) -> int:
    db = get_db()
    row = db.query_one("SELECT id FROM knowledge_point WHERE code=?", (code,))
    if row:
        db.execute(
            "UPDATE knowledge_point SET course_id=?, name=?, description=?, granularity=?,"
            " kp_type=?, difficulty=?, unit=? WHERE id=?",
            (course_id, name, description, granularity, kp_type, difficulty, unit, row["id"]),
        )
        return row["id"]
    return db.execute(
        "INSERT INTO knowledge_point(course_id, code, name, description, granularity,"
        " kp_type, difficulty, unit, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (course_id, code, name, description, granularity, kp_type, difficulty, unit, now_str()),
    )


def get_kp(kp_id: int) -> KnowledgePoint | None:
    row = get_db().query_one("SELECT * FROM knowledge_point WHERE id=?", (kp_id,))
    return KnowledgePoint.from_dict(row) if row else None


def get_kp_by_code(code: str) -> KnowledgePoint | None:
    row = get_db().query_one("SELECT * FROM knowledge_point WHERE code=?", (code,))
    return KnowledgePoint.from_dict(row) if row else None


def list_kps(course_id: int | None = None) -> list[KnowledgePoint]:
    if course_id:
        rows = get_db().query(
            "SELECT * FROM knowledge_point WHERE course_id=? ORDER BY id", (course_id,)
        )
    else:
        rows = get_db().query("SELECT * FROM knowledge_point ORDER BY id")
    return [KnowledgePoint.from_dict(r) for r in rows]


def kp_map(course_id: int | None = None) -> dict[int, KnowledgePoint]:
    return {k.id: k for k in list_kps(course_id)}


# ---------------- 依赖边 ----------------
def add_edge(
    from_kp_id: int, to_kp_id: int, edge_type: str = "prereq", strength: float = 1.0
) -> None:
    if from_kp_id == to_kp_id:
        raise ValueError("依赖边禁止自环")
    get_db().execute(
        "INSERT OR REPLACE INTO prerequisite(from_kp_id, to_kp_id, edge_type, strength)"
        " VALUES(?,?,?,?)",
        (from_kp_id, to_kp_id, edge_type, strength),
    )


def list_edges(edge_type: str = "prereq") -> list[Edge]:
    rows = get_db().query("SELECT * FROM prerequisite WHERE edge_type=?", (edge_type,))
    return [Edge.from_dict(r) for r in rows]


def prereqs_of(kp_id: int) -> list[int]:
    """学 kp_id 之前必须先会的知识点。"""
    return [
        r["from_kp_id"]
        for r in get_db().query(
            "SELECT from_kp_id FROM prerequisite WHERE to_kp_id=? AND edge_type='prereq'",
            (kp_id,),
        )
    ]


def dependents_of(kp_id: int) -> list[int]:
    """依赖 kp_id 的后续知识点。"""
    return [
        r["to_kp_id"]
        for r in get_db().query(
            "SELECT to_kp_id FROM prerequisite WHERE from_kp_id=? AND edge_type='prereq'",
            (kp_id,),
        )
    ]


def adjacency(course_id: int | None = None) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    """返回 (前置表, 后继表)。一次取全图，避免 N+1 查询。"""
    kids = {k.id for k in list_kps(course_id)}
    pre: dict[int, list[int]] = {i: [] for i in kids}
    post: dict[int, list[int]] = {i: [] for i in kids}
    for e in list_edges():
        if e.to_kp_id in pre and e.from_kp_id in post:
            pre[e.to_kp_id].append(e.from_kp_id)
            post[e.from_kp_id].append(e.to_kp_id)
    return pre, post


# ---------------- 能力模块 ----------------
def upsert_module(code: str, name: str, description: str = "") -> int:
    db = get_db()
    row = db.query_one("SELECT id FROM ability_module WHERE code=?", (code,))
    if row:
        db.execute(
            "UPDATE ability_module SET name=?, description=? WHERE id=?",
            (name, description, row["id"]),
        )
        return row["id"]
    return db.execute(
        "INSERT INTO ability_module(code, name, description) VALUES(?,?,?)",
        (code, name, description),
    )


def list_modules() -> list[AbilityModule]:
    return [AbilityModule.from_dict(r) for r in get_db().query(
        "SELECT * FROM ability_module ORDER BY code")]


def link_kp_module(kp_id: int, module_id: int, weight: float = 1.0) -> None:
    get_db().execute(
        "INSERT OR REPLACE INTO kp_ability_link(kp_id, module_id, weight) VALUES(?,?,?)",
        (kp_id, module_id, weight),
    )


def modules_of_kp(kp_id: int) -> list[dict]:
    return get_db().query(
        "SELECT m.code, m.name, l.weight FROM kp_ability_link l"
        " JOIN ability_module m ON m.id=l.module_id WHERE l.kp_id=?",
        (kp_id,),
    )


def kps_of_module(module_code: str) -> list[int]:
    return [
        r["kp_id"]
        for r in get_db().query(
            "SELECT l.kp_id FROM kp_ability_link l JOIN ability_module m ON m.id=l.module_id"
            " WHERE m.code=?",
            (module_code,),
        )
    ]


def module_weight_map() -> dict[int, dict[str, float]]:
    rows = get_db().query(
        "SELECT l.kp_id, m.code, l.weight FROM kp_ability_link l"
        " JOIN ability_module m ON m.id=l.module_id"
    )
    out: dict[int, dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["kp_id"], {})[r["code"]] = r["weight"]
    return out


# ---------------- 项目与任务 ----------------
def upsert_project(code: str, name: str, ptype: str, adapter_key: str, desc: str = "") -> int:
    db = get_db()
    row = db.query_one("SELECT id FROM project WHERE code=?", (code,))
    if row:
        db.execute(
            "UPDATE project SET name=?, ptype=?, adapter_key=?, description=? WHERE id=?",
            (name, ptype, adapter_key, desc, row["id"]),
        )
        return row["id"]
    return db.execute(
        "INSERT INTO project(code, name, ptype, adapter_key, description) VALUES(?,?,?,?,?)",
        (code, name, ptype, adapter_key, desc),
    )


def list_projects() -> list[dict]:
    return get_db().query("SELECT * FROM project ORDER BY id")


def get_project(code: str) -> dict | None:
    return get_db().query_one("SELECT * FROM project WHERE code=?", (code,))


def upsert_task(
    project_id: int,
    code: str,
    name: str,
    parent_code: str | None = None,
    milestone: int = 0,
    seq: int = 0,
    description: str = "",
) -> int:
    db = get_db()
    parent_id = None
    if parent_code:
        p = db.query_one("SELECT id FROM project_task WHERE code=?", (parent_code,))
        parent_id = p["id"] if p else None
    row = db.query_one("SELECT id FROM project_task WHERE code=?", (code,))
    if row:
        db.execute(
            "UPDATE project_task SET project_id=?, name=?, parent_id=?, milestone=?, seq=?,"
            " description=? WHERE id=?",
            (project_id, name, parent_id, milestone, seq, description, row["id"]),
        )
        return row["id"]
    return db.execute(
        "INSERT INTO project_task(project_id, code, name, parent_id, milestone, seq, description)"
        " VALUES(?,?,?,?,?,?,?)",
        (project_id, code, name, parent_id, milestone, seq, description),
    )


def get_task(task_id: int) -> ProjectTask | None:
    row = get_db().query_one("SELECT * FROM project_task WHERE id=?", (task_id,))
    return ProjectTask.from_dict(row) if row else None


def get_task_by_code(code: str) -> ProjectTask | None:
    row = get_db().query_one("SELECT * FROM project_task WHERE code=?", (code,))
    return ProjectTask.from_dict(row) if row else None


def list_tasks(project_id: int | None = None) -> list[ProjectTask]:
    if project_id:
        rows = get_db().query(
            "SELECT * FROM project_task WHERE project_id=? ORDER BY seq, id", (project_id,)
        )
    else:
        rows = get_db().query("SELECT * FROM project_task ORDER BY project_id, seq, id")
    return [ProjectTask.from_dict(r) for r in rows]


def link_task_kp(task_id: int, kp_id: int, necessity: str = "required",
                 annotated_by: str = "teacher") -> None:
    get_db().execute(
        "INSERT OR REPLACE INTO task_kp_link(task_id, kp_id, necessity, annotated_by)"
        " VALUES(?,?,?,?)",
        (task_id, kp_id, necessity, annotated_by),
    )


def required_kps(task_id: int, include_helpful: bool = False) -> list[dict]:
    sql = (
        "SELECT l.kp_id, l.necessity, k.code, k.name FROM task_kp_link l"
        " JOIN knowledge_point k ON k.id=l.kp_id WHERE l.task_id=?"
    )
    if not include_helpful:
        sql += " AND l.necessity='required'"
    return get_db().query(sql, (task_id,))


def tasks_requiring(kp_id: int) -> list[int]:
    return [
        r["task_id"]
        for r in get_db().query("SELECT task_id FROM task_kp_link WHERE kp_id=?", (kp_id,))
    ]


def add_task_dependency(from_code: str, to_code: str) -> None:
    db = get_db()
    a = db.query_one("SELECT id FROM project_task WHERE code=?", (from_code,))
    b = db.query_one("SELECT id FROM project_task WHERE code=?", (to_code,))
    if a and b and a["id"] != b["id"]:
        db.execute(
            "INSERT OR REPLACE INTO task_dependency(from_task_id, to_task_id) VALUES(?,?)",
            (a["id"], b["id"]),
        )


def task_successors(task_id: int) -> list[int]:
    return [
        r["to_task_id"]
        for r in get_db().query(
            "SELECT to_task_id FROM task_dependency WHERE from_task_id=?", (task_id,)
        )
    ]


# ---------------- 统计 ----------------
def stats() -> GraphStats:
    db = get_db()
    from .algo import detect_cycles

    return GraphStats(
        courses=db.scalar("SELECT COUNT(*) FROM course") or 0,
        kps=db.scalar("SELECT COUNT(*) FROM knowledge_point") or 0,
        edges=db.scalar("SELECT COUNT(*) FROM prerequisite") or 0,
        modules=db.scalar("SELECT COUNT(*) FROM ability_module") or 0,
        tasks=db.scalar("SELECT COUNT(*) FROM project_task") or 0,
        task_kp_links=db.scalar("SELECT COUNT(*) FROM task_kp_link") or 0,
        cycles=detect_cycles(),
    )
