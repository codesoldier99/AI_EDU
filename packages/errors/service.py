"""错误模式库：系统最有价值的资产。

一位从教二十年的教师最不可替代的能力，是知道学生会在哪里犯错、错的背后
通常是哪个概念没通。这份经验从未被记录，教师退休即消失。
本模块的任务就是把它变成学院资产——从第一届学生就存下来。

流程：学生答错 → ErrorInstance（原始作答）→ 归因聚类到 ErrorPattern
      → 教师确认/修正（teacher_verified）→ 沉淀为该知识点的典型错误
Phase 1 只做采集与人工确认，聚类可粗糙；关键是"存下来"。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from packages.core.db import get_db
from packages.core.models import Message
from packages.core.timeutil import now_str
from packages.graph import algo as graph_algo
from packages.graph import repo as graph_repo


@dataclass
class PatternView(Message):
    id: int = 0
    kp_id: int = 0
    kp_name: str = ""
    description: str = ""
    root_cause_kp_id: int | None = None
    root_cause_name: str = ""
    occurrence_count: int = 0
    teacher_verified: int = 0
    samples: list = field(default_factory=list)


def signature(text: str) -> str:
    """粗聚类签名：去掉数字与空白后取指纹。

    刻意做得简单——Phase 1 的目标是采集，不是聚类精度。
    后续可换成 embedding 聚类，签名字段保留即可平滑迁移。
    """
    norm = re.sub(r"[\s\d，。,\.]+", "", text)[:120]
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:12]


def record_error(
    student_id: int,
    kp_id: int,
    raw_text: str,
    event_id: int | None = None,
    description: str = "",
    root_cause_kp_id: int | None = None,
) -> dict:
    """记录一条错误实例并归入模式。不做掌握度更新（那是 tracker 的事）。"""
    db = get_db()
    sig = signature(raw_text or description)
    now = now_str()
    pat = db.query_one(
        "SELECT * FROM error_pattern WHERE kp_id=? AND signature=?", (kp_id, sig)
    )
    if pat:
        db.execute(
            "UPDATE error_pattern SET occurrence_count=occurrence_count+1, last_seen=?"
            " WHERE id=?",
            (now, pat["id"]),
        )
        pattern_id = pat["id"]
    else:
        pattern_id = db.execute(
            "INSERT INTO error_pattern(kp_id, signature, description, root_cause_kp_id,"
            " occurrence_count, first_seen, last_seen, teacher_verified)"
            " VALUES(?,?,?,?,?,?,?,0)",
            (kp_id, sig, description or (raw_text or "")[:80], root_cause_kp_id, 1, now, now),
        )
    inst_id = db.execute(
        "INSERT INTO error_instance(pattern_id, student_id, event_id, kp_id, raw_text,"
        " created_at) VALUES(?,?,?,?,?,?)",
        (pattern_id, student_id, event_id, kp_id, raw_text, now),
    )
    return {"pattern_id": pattern_id, "instance_id": inst_id, "signature": sig}


def attribute(kp_id: int, raw_text: str, mastery: dict[int, float], threshold: float) -> dict:
    """归因：给出候选根因知识点。

    根因由图谱结构 + 学生状态确定性地算出，大模型只负责把话说清楚，
    且结论必须经教师确认（teacher_verified）后才算数。
    """
    trace = graph_algo.trace_root_cause(kp_id, mastery, threshold)
    root_id = trace["root_kp_id"]
    root = graph_repo.get_kp(root_id)
    kp = graph_repo.get_kp(kp_id)
    from packages.llm import gateway

    prompt = (
        f"- 意图：错误归因\n- 知识点：{kp.name if kp else kp_id}\n"
        f"- 现象：{raw_text[:200]}\n"
        f"- 候选根因：{root.name if root else '（无）'}\n"
    )
    r = gateway.complete(
        "error_attribution",
        "你是助教，只依据给定字段整理归因描述，不得引入新的事实判断。",
        prompt,
        max_tokens=300,
    )
    return {
        "kp_id": kp_id,
        "root_cause_kp_id": root_id if root_id != kp_id else None,
        "root_cause_name": root.name if root and root_id != kp_id else "",
        "path": trace["path"],
        "narrative": r.text,
        "degraded": r.degraded,
        "needs_teacher_confirm": True,
    }


def list_patterns(kp_id: int | None = None, verified_only: bool = False,
                  limit: int = 50) -> list[PatternView]:
    sql = (
        "SELECT p.*, k.name AS kp_name FROM error_pattern p"
        " JOIN knowledge_point k ON k.id=p.kp_id WHERE 1=1"
    )
    params: list = []
    if kp_id:
        sql += " AND p.kp_id=?"
        params.append(kp_id)
    if verified_only:
        sql += " AND p.teacher_verified=1"
    sql += " ORDER BY p.occurrence_count DESC, p.last_seen DESC LIMIT ?"
    params.append(limit)
    out = []
    for r in get_db().query(sql, params):
        root = graph_repo.get_kp(r["root_cause_kp_id"]) if r["root_cause_kp_id"] else None
        samples = get_db().query(
            "SELECT raw_text, created_at FROM error_instance WHERE pattern_id=?"
            " ORDER BY id DESC LIMIT 3",
            (r["id"],),
        )
        out.append(
            PatternView(
                id=r["id"], kp_id=r["kp_id"], kp_name=r["kp_name"],
                description=r["description"], root_cause_kp_id=r["root_cause_kp_id"],
                root_cause_name=root.name if root else "",
                occurrence_count=r["occurrence_count"],
                teacher_verified=r["teacher_verified"], samples=samples,
            )
        )
    return out


def verify_pattern(pattern_id: int, note: str = "", description: str | None = None,
                   root_cause_kp_id: int | None = None) -> None:
    """教师确认。确认过的模式才可用于向学生解释。"""
    db = get_db()
    sets, params = ["teacher_verified=1", "teacher_note=?"], [note]
    if description is not None:
        sets.append("description=?")
        params.append(description)
    if root_cause_kp_id is not None:
        sets.append("root_cause_kp_id=?")
        params.append(root_cause_kp_id)
    params.append(pattern_id)
    db.execute(f"UPDATE error_pattern SET {', '.join(sets)} WHERE id=?", params)


def typical_errors_for(kp_id: int, limit: int = 3) -> list[dict]:
    """回答新教师问不出、老教师答得出的问题：这个知识点，学生通常错在哪？"""
    rows = get_db().query(
        "SELECT description, occurrence_count, teacher_verified FROM error_pattern"
        " WHERE kp_id=? ORDER BY teacher_verified DESC, occurrence_count DESC LIMIT ?",
        (kp_id, limit),
    )
    return rows


def stats() -> dict:
    db = get_db()
    return {
        "patterns": db.scalar("SELECT COUNT(*) FROM error_pattern") or 0,
        "verified": db.scalar("SELECT COUNT(*) FROM error_pattern WHERE teacher_verified=1") or 0,
        "instances": db.scalar("SELECT COUNT(*) FROM error_instance") or 0,
    }
