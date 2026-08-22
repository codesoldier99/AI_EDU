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


# ---------------- 任务-知识点候选（待审草案队列） ----------------
# 候选不是事实：系统其余部分只读 task_kp_link，对本表一无所知。
# 只有教师采纳后才 link_task_kp(annotated_by='teacher')，见 accept_candidate。

def add_candidate(task_id: int, kp_id: int, necessity: str, score: float,
                  confidence: float, evidence: list, source_ref: str = "",
                  rationale: str = "", matcher: str = "lexical") -> bool:
    """登记一条候选。已存在（无论待审还是已判过）则跳过，返回是否新增。

    跳过已判过的是刻意的：教师否决过的映射，不该在下次重跑匹配时又冒出来烦他。
    """
    db = get_db()
    if db.query_one("SELECT id FROM task_kp_candidate WHERE task_id=? AND kp_id=?",
                    (task_id, kp_id)):
        return False
    if db.query_one("SELECT task_id FROM task_kp_link WHERE task_id=? AND kp_id=?",
                    (task_id, kp_id)):
        return False        # 教师已经标过了，不必再问一遍
    db.execute(
        "INSERT INTO task_kp_candidate(task_id, kp_id, necessity, score, confidence,"
        " evidence, source_ref, rationale, matcher, status, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,'pending',?)",
        (task_id, kp_id, necessity, float(score), float(confidence),
         dumps(evidence), source_ref, rationale, matcher, now_str()),
    )
    return True


def list_candidates(project_code: str | None = None, status: str = "pending",
                    limit: int = 300) -> list[dict]:
    """待审队列。按置信度降序——教师先看最有把握的，省时间。"""
    sql = (
        "SELECT c.*, t.code AS task_code, t.name AS task_name,"
        " k.code AS kp_code, k.name AS kp_name, p.code AS project_code"
        " FROM task_kp_candidate c"
        " JOIN project_task t ON t.id=c.task_id"
        " JOIN knowledge_point k ON k.id=c.kp_id"
        " JOIN project p ON p.id=t.project_id WHERE 1=1"
    )
    args: list = []
    if status:
        sql += " AND c.status=?"
        args.append(status)
    if project_code:
        sql += " AND p.code=?"
        args.append(project_code)
    sql += " ORDER BY c.confidence DESC, c.score DESC LIMIT ?"
    args.append(limit)
    return get_db().query(sql, tuple(args))


def get_candidate(cand_id: int) -> dict | None:
    return get_db().query_one("SELECT * FROM task_kp_candidate WHERE id=?", (cand_id,))


def decide_candidate(cand_id: int, accept: bool, decided_by: str,
                     necessity: str | None = None) -> dict:
    """教师裁决。采纳才写入 task_kp_link，且署名为 teacher——模型不署名。"""
    c = get_candidate(cand_id)
    if not c:
        raise KeyError(f"候选不存在：{cand_id}")
    if c["status"] != "pending":
        return {"ok": False, "reason": f"已被处理过：{c['status']}"}
    nec = necessity or c["necessity"]
    if accept:
        link_task_kp(c["task_id"], c["kp_id"], nec, "teacher")
    get_db().execute(
        "UPDATE task_kp_candidate SET status=?, necessity=?, decided_by=?, decided_at=?"
        " WHERE id=?",
        ("accepted" if accept else "rejected", nec, decided_by, now_str(), cand_id),
    )
    invalidate_stats_cache()
    return {"ok": True, "status": "accepted" if accept else "rejected",
            "task_id": c["task_id"], "kp_id": c["kp_id"], "necessity": nec}


def candidate_stats(project_code: str | None = None) -> dict:
    """待审队列概况。采纳率是这套匹配值不值得用的唯一诚实指标。"""
    db = get_db()
    sql = ("SELECT c.status, COUNT(*) n FROM task_kp_candidate c"
           " JOIN project_task t ON t.id=c.task_id"
           " JOIN project p ON p.id=t.project_id")
    args: tuple = ()
    if project_code:
        sql += " WHERE p.code=?"
        args = (project_code,)
    sql += " GROUP BY c.status"
    by = {r["status"]: r["n"] for r in db.query(sql, args)}
    decided = by.get("accepted", 0) + by.get("rejected", 0)
    return {
        "pending": by.get("pending", 0),
        "accepted": by.get("accepted", 0),
        "rejected": by.get("rejected", 0),
        "decided": decided,
        "accept_rate": round(by.get("accepted", 0) / decided, 3) if decided else None,
    }


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


def assigned_task_ids(student_id: int, exclude_done: bool = True) -> list[int]:
    """该学生手上还没做完的任务。拉取式的入口：不知道他在做什么，就不该推任何东西。"""
    sql = "SELECT task_id FROM task_assignment WHERE student_id=?"
    if exclude_done:
        sql += " AND status<>'done'"
    return [r["task_id"] for r in get_db().query(sql + " ORDER BY updated_at DESC",
                                                 (student_id,))]


def task_successors(task_id: int) -> list[int]:
    return [
        r["to_task_id"]
        for r in get_db().query(
            "SELECT to_task_id FROM task_dependency WHERE from_task_id=?", (task_id,)
        )
    ]


# ---------------- 统计 ----------------
# 图谱统计的进程内缓存。图谱是慢变量（导入种子时才变），而 /api/health 是监控
# 打得最勤的接口——实测未缓存时它只有 25 次/秒、p50 超过 1 秒，
# 一个把系统自己压垮的健康检查是没有意义的。
_STATS_TTL = 60.0
# 按库路径分桶。测试里每个用例一个临时库，不分桶会串库——
# 这类"缓存跨实例泄漏"制造的是随机失败的测试，比慢一百倍难查得多。
_stats_cache: dict[str, tuple[float, "GraphStats"]] = {}


def invalidate_stats_cache() -> None:
    """种子导入等会改变图谱结构的操作之后调用。"""
    _stats_cache.clear()


def stats(max_age: float = _STATS_TTL) -> GraphStats:
    import time as _time

    key = str(get_db().path)
    hit = _stats_cache.get(key)
    if hit and (_time.monotonic() - hit[0]) < max_age:
        return hit[1]
    val = _compute_stats()
    _stats_cache[key] = (_time.monotonic(), val)
    return val


def _compute_stats() -> GraphStats:
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
