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


# 除 knowledge_point 外，还引用 course(id) 的表。合并课程时要一并改指向，
# 否则删的时候会撞外键——更糟的是，若强行删掉，教师写的大纲就没了。
_COURSE_REFS = ("syllabus",)


def merge_course(from_code: str, into_code: str) -> dict:
    """把旧课程代码合并进新代码：同一门课，只是换成了培养方案里的正式代码。

    **不是删除，是改指向。** 服务器上曾经出现过：ML 这门课底下挂着教师写的
    教学大纲，直接 DELETE 会撞外键（撞对了——那份大纲不该跟着代码一起消失）。
    所以先把引用它的记录改指向新课程，确认知识点已全部移交，才删掉空壳行。
    """
    db = get_db()
    src, dst = get_course(from_code), get_course(into_code)
    if not src:
        return {"merged": False, "reason": f"源课程不存在：{from_code}"}
    if not dst:
        return {"merged": False, "reason": f"目标课程不存在：{into_code}"}
    left = db.scalar("SELECT COUNT(*) FROM knowledge_point WHERE course_id=?", (src["id"],))
    if left:
        return {"merged": False, "reason": f"仍有 {left} 个知识点未移交", "kps_left": left}

    moved = {}
    for tbl in _COURSE_REFS:
        n = db.scalar(f"SELECT COUNT(*) FROM {tbl} WHERE course_id=?", (src["id"],)) or 0
        if n:
            db.execute(f"UPDATE {tbl} SET course_id=? WHERE course_id=?",
                       (dst["id"], src["id"]))
            moved[tbl] = n
    db.execute("DELETE FROM program_course WHERE course_id=?", (src["id"],))
    db.execute("DELETE FROM course_prefix WHERE course_id=?", (src["id"],))
    db.execute("DELETE FROM course WHERE id=?", (src["id"],))
    return {"merged": True, "from": from_code, "into": into_code, "moved": moved}


# ---------------- 培养方案 ----------------
def upsert_program(code: str, name: str, level: str = "", klass: str = "",
                   version: str = "", note: str = "") -> int:
    db = get_db()
    row = db.query_one("SELECT id FROM program WHERE code=?", (code,))
    if row:
        db.execute("UPDATE program SET name=?, level=?, klass=?, version=?, note=? WHERE id=?",
                   (name, level, klass, version, note, row["id"]))
        return row["id"]
    return db.execute(
        "INSERT INTO program(code, name, level, klass, version, note) VALUES(?,?,?,?,?,?)",
        (code, name, level, klass, version, note))


def get_program(code: str) -> dict | None:
    return get_db().query_one("SELECT * FROM program WHERE code=?", (code,))


def link_program_course(program_id: int, course_id: int, module: str, semester: int,
                        credit: float, hours: int, exam_type: str, seq: int = 0,
                        required: int = 1) -> None:
    get_db().execute(
        "INSERT OR REPLACE INTO program_course(program_id, course_id, module, semester,"
        " credit, hours, exam_type, required, seq) VALUES(?,?,?,?,?,?,?,?,?)",
        (program_id, course_id, module, semester, credit, hours, exam_type, required, seq))


def program_courses(program_code: str) -> list[dict]:
    """培养方案的课程清单，附每门课已建的知识点数。

    kps=0 的课很多，这是拉取式建设的正常状态，不是数据缺失。
    """
    return get_db().query(
        "SELECT c.id AS course_id, c.code, c.name, pc.module, pc.semester, pc.credit,"
        " pc.hours, pc.exam_type, pc.seq,"
        " (SELECT COUNT(*) FROM knowledge_point k WHERE k.course_id=c.id) AS kps"
        " FROM program_course pc JOIN course c ON c.id=pc.course_id"
        " JOIN program p ON p.id=pc.program_id WHERE p.code=?"
        " ORDER BY pc.semester, pc.seq", (program_code,))


def program_credit_total(program_id: int) -> float:
    return round(get_db().scalar(
        "SELECT SUM(credit) FROM program_course WHERE program_id=?", (program_id,)) or 0.0, 1)


def set_course_prefix(prefix: str, course_id: int) -> None:
    get_db().execute("INSERT OR REPLACE INTO course_prefix(prefix, course_id) VALUES(?,?)",
                     (prefix, course_id))


def course_for_code(kp_code: str) -> dict | None:
    """按代码前缀猜这个知识点该归哪门课。用于给悬空需求归位。

    取**最长匹配**：CD-ML 应该归「机器学习课程设计」而不是「机器学习」。
    """
    rows = get_db().query(
        "SELECT p.prefix, c.code, c.name, c.id FROM course_prefix p"
        " JOIN course c ON c.id=p.course_id")
    best = None
    for r in rows:
        pre = r["prefix"]
        if kp_code.upper().startswith(pre.upper() + "-") and (
                best is None or len(pre) > len(best["prefix"])):
            best = r
    return best


# ---------------- 集中实践环节绑定 ----------------
def bind_practice(project_code: str, course_code: str, note: str = "") -> dict:
    """把一个项目绑到一门集中实践课程上，作为其项目专有知识的学分载体。

    一个项目只能绑一门（主键约束）——否则同一份工作会在两门课里各认一次学分。
    """
    db = get_db()
    c = get_course(course_code)
    if not c:
        return {"ok": False, "reason": f"课程不存在：{course_code}"}
    db.execute(
        "INSERT OR REPLACE INTO practice_binding(project_code, course_id, note, created_at)"
        " VALUES(?,?,?,?)", (project_code, c["id"], note, now_str()))
    return {"ok": True, "project": project_code, "course": course_code}


def practice_projects_of(course_id: int) -> list[str]:
    """这门实践课承接了哪些项目。"""
    return [r["project_code"] for r in get_db().query(
        "SELECT project_code FROM practice_binding WHERE course_id=?", (course_id,))]


def practice_course_of(project_code: str) -> dict | None:
    row = get_db().query_one(
        "SELECT c.* FROM practice_binding b JOIN course c ON c.id=b.course_id"
        " WHERE b.project_code=?", (project_code,))
    return row


def list_practice_bindings() -> list[dict]:
    return get_db().query(
        "SELECT b.project_code, c.code AS course_code, c.name AS course_name, b.note"
        " FROM practice_binding b JOIN course c ON c.id=b.course_id"
        " ORDER BY b.project_code")


def project_domain_kp_ids(project_code: str) -> list[int]:
    """项目专有知识点：该项目用到、但不属于培养方案任何一门课的知识点。

    判据是"归属课程没出现在任何培养方案里"——即 project_domains 声明的知识域。
    """
    return [r["id"] for r in get_db().query(
        "SELECT DISTINCT k.id FROM task_kp_link l"
        " JOIN project_task t ON t.id=l.task_id"
        " JOIN project p ON p.id=t.project_id"
        " JOIN knowledge_point k ON k.id=l.kp_id"
        " WHERE p.code=? AND k.course_id NOT IN"
        " (SELECT course_id FROM program_course)", (project_code,))]


def project_milestones(project_code: str) -> dict:
    """项目里程碑数量——实践环节验收的事实依据之一。"""
    db = get_db()
    total = db.scalar(
        "SELECT COUNT(*) FROM project_task t JOIN project p ON p.id=t.project_id"
        " WHERE p.code=? AND t.milestone=1", (project_code,)) or 0
    return {"milestones": total}


# ---------------- 悬空知识点需求（拉取式图谱建设） ----------------
def add_demand(kp_code: str, demanded_by: str, project_code: str = "",
               kind: str = "task_required") -> bool:
    """登记一条"要用但还没建"的知识点需求。同一来源重复登记只记一次。"""
    db = get_db()
    if db.query_one("SELECT id FROM kp_demand WHERE code=? AND demanded_by=?",
                    (kp_code, demanded_by)):
        return False
    c = course_for_code(kp_code)
    db.execute(
        "INSERT INTO kp_demand(code, course_code, demanded_by, project_code, kind,"
        " status, first_seen) VALUES(?,?,?,?,?,'open',?)",
        (kp_code, c["code"] if c else "", demanded_by, project_code, kind, now_str()))
    return True


def close_built_demands() -> int:
    """知识点建出来了，对应需求自动闭合。队列因此会自己变短，不需要人去清。"""
    return get_db().execute(
        "UPDATE kp_demand SET status='built' WHERE status='open' AND code IN"
        " (SELECT code FROM knowledge_point)") or 0


def demand_queue(project_code: str | None = None) -> list[dict]:
    """按"被多少处需求"排序的建设队列 —— 下一门课该先建哪几个点。

    这是拉取式图谱建设的输出：整个培养方案不必平均用力，
    被 3 个项目拉过的 20 个知识点，比没人拉的 300 个更该先建。
    """
    sql = ("SELECT code, course_code, COUNT(*) AS demands,"
           " GROUP_CONCAT(DISTINCT project_code) AS projects,"
           " GROUP_CONCAT(demanded_by) AS by_whom, MIN(first_seen) AS first_seen"
           " FROM kp_demand WHERE status='open'")
    args: tuple = ()
    if project_code:
        sql += " AND project_code=?"
        args = (project_code,)
    sql += " GROUP BY code, course_code ORDER BY demands DESC, code"
    return get_db().query(sql, args)


def demand_by_course() -> list[dict]:
    """按课程汇总的需求：哪门课欠得最多，就该先建哪门。"""
    return get_db().query(
        "SELECT COALESCE(NULLIF(course_code,''),'（未归类）') AS course_code,"
        " COUNT(DISTINCT code) AS kps, COUNT(*) AS demands"
        " FROM kp_demand WHERE status='open'"
        " GROUP BY course_code ORDER BY demands DESC")


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


def get_module_by_code(code: str) -> dict | None:
    return get_db().query_one("SELECT * FROM ability_module WHERE code=?", (code,))


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
