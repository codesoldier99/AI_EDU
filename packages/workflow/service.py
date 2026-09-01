"""统一待审队列：同步、列表、认领、裁决。

裁决时委托既有业务函数（quiz.bank / graph.repo / errors / review / exam），
禁止在本层重写判分或写掌握度。
"""
from __future__ import annotations

from typing import Any

from packages.core.db import dumps, get_db, loads
from packages.core.timeutil import now_str

from .kinds import ACTIONS, KINDS, TERMINAL, TRANSITIONS


class WorkflowError(Exception):
    """流程状态机错误（非法迁移等）。"""


def _append_event(item_id: int, from_state: str, to_state: str,
                  actor: str, action: str, comment: str = "") -> None:
    get_db().execute(
        "INSERT INTO workflow_event(item_id, from_state, to_state, actor, action,"
        " comment, occurred_at) VALUES(?,?,?,?,?,?,?)",
        (item_id, from_state, to_state, actor, action, comment or "", now_str()),
    )


def upsert_item(
    kind: str,
    ref_id: int,
    *,
    title: str = "",
    payload: dict | None = None,
    state: str = "pending_review",
    created_by: str = "system",
) -> int:
    """幂等登记一条流程项。已存在且仍在进行中则更新标题/载荷；终态不覆盖。"""
    if kind not in KINDS:
        raise WorkflowError(f"未知流程种类 {kind}")
    meta = KINDS[kind]
    db = get_db()
    row = db.query_one(
        "SELECT * FROM workflow_item WHERE kind=? AND ref_table=? AND ref_id=?",
        (kind, meta["ref_table"], ref_id),
    )
    now = now_str()
    pl = dumps(payload or {})
    if row:
        if row["state"] in TERMINAL:
            return row["id"]
        db.execute(
            "UPDATE workflow_item SET title=?, payload=?, updated_at=? WHERE id=?",
            (title or row["title"], pl, now, row["id"]),
        )
        return row["id"]
    iid = db.execute(
        "INSERT INTO workflow_item(kind, ref_table, ref_id, state, title, payload,"
        " created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (kind, meta["ref_table"], ref_id, state, title, pl, created_by, now, now),
    )
    _append_event(iid, "", state, created_by, "create")
    return iid


def get_item(item_id: int) -> dict | None:
    row = get_db().query_one("SELECT * FROM workflow_item WHERE id=?", (item_id,))
    if not row:
        return None
    row["payload"] = loads(row.get("payload"), {})
    return row


def list_items(
    kind: str | None = None,
    state: str | None = "pending_review",
    limit: int = 100,
) -> list[dict]:
    sql = "SELECT * FROM workflow_item WHERE 1=1"
    args: list[Any] = []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if state:
        if state == "open":
            sql += " AND state IN ('pending_review','claimed','draft')"
        else:
            sql += " AND state=?"
            args.append(state)
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    args.append(limit)
    rows = get_db().query(sql, args)
    for r in rows:
        r["payload"] = loads(r.get("payload"), {})
        r["label"] = KINDS.get(r["kind"], {}).get("label", r["kind"])
    return rows


def list_events(item_id: int, limit: int = 50) -> list[dict]:
    return get_db().query(
        "SELECT * FROM workflow_event WHERE item_id=? ORDER BY id ASC LIMIT ?",
        (item_id, limit),
    )


def stats() -> dict:
    rows = get_db().query(
        "SELECT kind, state, COUNT(*) AS n FROM workflow_item GROUP BY kind, state"
    )
    by_kind: dict[str, dict[str, int]] = {}
    open_n = 0
    for r in rows:
        by_kind.setdefault(r["kind"], {})[r["state"]] = r["n"]
        if r["state"] in ("pending_review", "claimed", "draft"):
            open_n += r["n"]
    return {"by_kind": by_kind, "open": open_n, "kinds": {
        k: v["label"] for k, v in KINDS.items()
    }}


def claim(item_id: int, actor: str) -> dict:
    item = get_item(item_id)
    if not item:
        raise WorkflowError("流程项不存在")
    if "claim" not in TRANSITIONS.get(item["state"], ()):
        raise WorkflowError(f"状态 {item['state']} 不可认领")
    if item["state"] == "claimed" and item.get("claimed_by") and item["claimed_by"] != actor:
        raise WorkflowError(f"已被 {item['claimed_by']} 认领")
    now = now_str()
    get_db().execute(
        "UPDATE workflow_item SET state='claimed', claimed_by=?, claimed_at=?,"
        " updated_at=? WHERE id=?",
        (actor, now, now, item_id),
    )
    _append_event(item_id, item["state"], "claimed", actor, "claim")
    return get_item(item_id)


def unclaim(item_id: int, actor: str) -> dict:
    item = get_item(item_id)
    if not item:
        raise WorkflowError("流程项不存在")
    if "unclaim" not in TRANSITIONS.get(item["state"], ()):
        raise WorkflowError(f"状态 {item['state']} 不可取消认领")
    if item.get("claimed_by") and item["claimed_by"] != actor:
        raise WorkflowError("只能取消自己的认领")
    now = now_str()
    get_db().execute(
        "UPDATE workflow_item SET state='pending_review', claimed_by=NULL,"
        " claimed_at=NULL, updated_at=? WHERE id=?",
        (now, item_id),
    )
    _append_event(item_id, item["state"], "pending_review", actor, "unclaim")
    return get_item(item_id)


def _mark(item_id: int, actor: str, action: str, comment: str = "") -> dict:
    item = get_item(item_id)
    if not item:
        raise WorkflowError("流程项不存在")
    if action not in TRANSITIONS.get(item["state"], ()):
        raise WorkflowError(f"状态 {item['state']} 不允许 {action}")
    to_state = ACTIONS[action]
    now = now_str()
    get_db().execute(
        "UPDATE workflow_item SET state=?, decided_by=?, decided_at=?, comment=?,"
        " updated_at=? WHERE id=?",
        (to_state, actor, now, comment or "", now, item_id),
    )
    _append_event(item_id, item["state"], to_state, actor, action, comment)
    return get_item(item_id)


def record_external_decision(
    kind: str,
    ref_id: int,
    action: str,
    actor: str,
    comment: str = "",
) -> dict | None:
    """业务 API 已完成裁决时回写流程项（避免双写分叉）。

    action ∈ {approve, reject, retire}。若尚无流程项则先登记再迁移。
    """
    if kind not in KINDS:
        return None
    if action not in ("approve", "reject", "retire"):
        raise WorkflowError(f"非法回写动作 {action}")
    meta = KINDS[kind]
    row = get_db().query_one(
        "SELECT id, state FROM workflow_item WHERE kind=? AND ref_table=? AND ref_id=?",
        (kind, meta["ref_table"], ref_id),
    )
    if not row:
        iid = upsert_item(kind, ref_id, title=f"{meta['label']}#{ref_id}",
                          created_by=actor)
    else:
        iid = row["id"]
        if row["state"] in TERMINAL:
            return get_item(iid)
    # 若还在 pending，允许直接裁决；若在 claimed 同理
    item = get_item(iid)
    if action not in TRANSITIONS.get(item["state"], ()) and item["state"] == "draft":
        # draft 只能 retire；approve/reject 先推到 pending
        if action != "retire":
            get_db().execute(
                "UPDATE workflow_item SET state='pending_review', updated_at=? WHERE id=?",
                (now_str(), iid),
            )
            _append_event(iid, "draft", "pending_review", actor, "sync")
    try:
        return _mark(iid, actor, action, comment)
    except WorkflowError:
        # 已是终态等——幂等吞掉
        return get_item(iid)


# 需要 agents 层才能落库的种类：本包只做状态机，由 apps/api 路由代调业务函数。
AGENT_BACKED_KINDS = frozenset({"review_finding", "quiz_grade"})


def act(
    item_id: int,
    action: str,
    actor: str,
    *,
    comment: str = "",
    extra: dict | None = None,
    domain_result: dict | None = None,
) -> dict:
    """认领或裁决。

    叶子业务（quiz/graph/errors/exam）在本包内委托；
    agents 背书的种类须由路由先跑业务再传入 domain_result（保持依赖单向）。
    """
    extra = extra or {}
    if action == "claim":
        return {"item": claim(item_id, actor), "domain": None}
    if action == "unclaim":
        return {"item": unclaim(item_id, actor), "domain": None}

    item = get_item(item_id)
    if not item:
        raise WorkflowError("流程项不存在")
    if action not in TRANSITIONS.get(item["state"], ()):
        raise WorkflowError(f"状态 {item['state']} 不允许 {action}")

    if item["kind"] in AGENT_BACKED_KINDS:
        if domain_result is None and action in ("approve", "reject", "retire"):
            raise WorkflowError(
                f"{item['kind']} 须由 API 层先执行业务裁决再提交 domain_result"
            )
        domain = domain_result
    else:
        domain = _apply_domain(item, action, actor, comment, extra)
    updated = _mark(item_id, actor, action, comment)
    return {"item": updated, "domain": domain}


def _apply_domain(item: dict, action: str, actor: str, comment: str,
                  extra: dict) -> dict | None:
    """把流程裁决落到业务表（仅 packages 叶子：quiz/graph/errors/exam）。"""
    kind = item["kind"]
    ref_id = item["ref_id"]

    if kind == "quiz_draft":
        from packages.quiz import bank

        if action == "approve":
            return bank.review(ref_id, "accept", extra.get("patch"))
        if action in ("reject", "retire"):
            return bank.review(ref_id, "reject")
        return None

    if kind == "kp_mapping":
        from packages.graph import repo as graph_repo

        if action == "approve":
            return graph_repo.decide_candidate(
                ref_id, True, decided_by=actor,
                necessity=extra.get("necessity"),
            )
        if action in ("reject", "retire"):
            return graph_repo.decide_candidate(ref_id, False, decided_by=actor)
        return None

    if kind == "error_pattern":
        from packages.errors import service as errors

        if action == "approve":
            errors.verify_pattern(
                ref_id, note=comment,
                description=extra.get("description"),
                root_cause_kp_id=extra.get("root_cause_kp_id"),
            )
            return {"id": ref_id, "teacher_verified": 1}
        if action in ("reject", "retire"):
            # 错误模式无"否决"表字段：标记流程终态即可，不删除资产
            return {"id": ref_id, "action": action, "note": "未确认，保留待后续"}
        return None

    if kind == "exam_grade":
        if action != "approve":
            return {"skipped": True, "reason": "考试判分否决无业务含义，仅关闭流程项"}
        from packages.exam import scoring

        session_id = extra.get("session_id") or item["payload"].get("session_id")
        question_id = extra.get("question_id") or item["payload"].get("question_id")
        score = extra.get("score")
        if session_id is None or question_id is None or score is None:
            raise WorkflowError("exam_grade 裁决需要 session_id / question_id / score")
        return scoring.teacher_score(
            int(session_id), int(question_id), float(score), comment
        )

    raise WorkflowError(f"未实现的流程种类 {kind}")

# ---------------------------------------------------------------- 从业务表同步
def sync_pending(limit_per_kind: int = 200) -> dict:
    """把各业务待审表投影进 workflow_item（幂等）。"""
    counts = {
        "quiz_draft": _sync_quiz_drafts(limit_per_kind),
        "kp_mapping": _sync_kp_mappings(limit_per_kind),
        "review_finding": _sync_review_findings(limit_per_kind),
        "error_pattern": _sync_error_patterns(limit_per_kind),
        "exam_grade": _sync_exam_grades(limit_per_kind),
        "quiz_grade": _sync_quiz_grades(limit_per_kind),
    }
    return {"synced": counts, "stats": stats()}


def _sync_quiz_drafts(limit: int) -> int:
    from packages.quiz import bank

    n = 0
    for q in bank.list_pending(limit):
        upsert_item(
            "quiz_draft", q.id,
            title=f"题库草案 · {getattr(q, 'kp_name', '') or q.kp_id} · #{q.id}",
            payload={"kp_id": q.kp_id, "qtype": q.qtype, "origin": q.origin,
                     "stem": (q.stem or "")[:120]},
            created_by="llm" if q.origin == "llm" else "system",
        )
        n += 1
    return n


def _sync_kp_mappings(limit: int) -> int:
    from packages.graph import repo as graph_repo

    n = 0
    for c in graph_repo.list_candidates(None, "pending", limit):
        upsert_item(
            "kp_mapping", c["id"],
            title=f"映射 · {c.get('task_code','')} ↔ {c.get('kp_code','')}",
            payload={"task_id": c["task_id"], "kp_id": c["kp_id"],
                     "confidence": c.get("confidence"), "score": c.get("score"),
                     "project_code": c.get("project_code")},
            created_by=c.get("matcher") or "matcher",
        )
        n += 1
    return n


def _sync_review_findings(limit: int) -> int:
    rows = get_db().query(
        "SELECT id, student_id, rule, message, severity, category"
        " FROM review_finding WHERE teacher_action='pending'"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    n = 0
    for r in rows:
        upsert_item(
            "review_finding", r["id"],
            title=f"审查 · {r['rule']} · 学生#{r['student_id']}",
            payload={"student_id": r["student_id"], "rule": r["rule"],
                     "severity": r["severity"], "category": r["category"],
                     "message": (r.get("message") or "")[:160]},
            created_by="review",
        )
        n += 1
    return n


def _sync_error_patterns(limit: int) -> int:
    rows = get_db().query(
        "SELECT p.id, p.kp_id, p.description, p.occurrence_count, k.name AS kp_name"
        " FROM error_pattern p JOIN knowledge_point k ON k.id=p.kp_id"
        " WHERE p.teacher_verified=0"
        " ORDER BY p.occurrence_count DESC LIMIT ?",
        (limit,),
    )
    n = 0
    for r in rows:
        upsert_item(
            "error_pattern", r["id"],
            title=f"错误模式 · {r['kp_name']} · ×{r['occurrence_count']}",
            payload={"kp_id": r["kp_id"], "description": (r.get("description") or "")[:160],
                     "occurrence_count": r["occurrence_count"]},
            created_by="errors",
        )
        n += 1
    return n


def _sync_exam_grades(limit: int) -> int:
    rows = get_db().query(
        "SELECT a.id, a.session_id, a.question_id, a.points, s.exam_id, s.student_id,"
        " st.sid, st.name"
        " FROM exam_answer a"
        " JOIN exam_session s ON s.id=a.session_id"
        " JOIN student st ON st.id=s.student_id"
        " WHERE a.score IS NULL AND s.status<>'open'"
        " ORDER BY a.id DESC LIMIT ?",
        (limit,),
    )
    n = 0
    for r in rows:
        upsert_item(
            "exam_grade", r["id"],
            title=f"考试判分 · {r['sid']} · Q{r['question_id']}",
            payload={"session_id": r["session_id"], "question_id": r["question_id"],
                     "exam_id": r["exam_id"], "student_id": r["student_id"],
                     "points": r["points"], "sid": r["sid"], "name": r["name"]},
            created_by="exam",
        )
        n += 1
    return n


def _sync_quiz_grades(limit: int) -> int:
    """练习侧：payload.grade.graded_by='pending_teacher' 且尚未被教师补判。"""
    rows = get_db().query(
        "SELECT id, student_id, kp_id, source_ref, payload, occurred_at"
        " FROM learning_event WHERE event_type='quiz'"
        " ORDER BY id DESC LIMIT ?",
        (limit * 5,),  # 粗筛后再过滤
    )
    n = 0
    for r in rows:
        if n >= limit:
            break
        pl = loads(r.get("payload"), {}) or {}
        grade = pl.get("grade") or {}
        if grade.get("graded_by") != "pending_teacher":
            continue
        # 若已有教师补判事件则跳过
        ref = r.get("source_ref") or ""
        if ref and get_db().query_one(
            "SELECT id FROM learning_event WHERE source='teacher'"
            " AND source_ref=? AND id>?",
            (ref, r["id"]),
        ):
            continue
        upsert_item(
            "quiz_grade", r["id"],
            title=f"练习判分 · 事件#{r['id']} · 学生#{r['student_id']}",
            payload={"student_id": r["student_id"], "kp_id": r["kp_id"],
                     "source_ref": ref, "occurred_at": r["occurred_at"]},
            created_by="quiz",
        )
        n += 1
    return n


def required_perm(kind: str) -> str:
    return KINDS.get(kind, {}).get("perm", "workflow.act")
