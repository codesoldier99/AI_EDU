"""题库仓储。题库与错误模式库同级，是**可积累资产**，不是缓存。

三条规矩：
1. 模型出的题一律 `teacher_verified=0`，属草案；组卷默认只取已审题（宁缺毋滥，同代码审查）。
2. 题只能标 `retired`，不能删——删题会带走历史作答的解释力（DB 触发器已固化）。
3. 每道题必须挂 `citations`，指向出题所依据的教材片段；无依据的题不许入库。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from packages.core.db import dumps, get_db, loads
from packages.core.models import Message
from packages.core.timeutil import now_str

QTYPES = ("choice", "numeric", "short", "code")
GRADERS = ("choice", "numeric", "keyword", "manual")
ORIGINS = ("llm", "teacher", "import", "anchor")
# anchor = 概念锚题：两年内要反复重测，**绝不进日常组卷池**，
# 也绝不在考后讲评。泄漏一次，纵向比较就报废（docs/measurement-plan.md §3.2）。


@dataclass
class Question(Message):
    id: int = 0
    kp_id: int = 0
    kp_name: str = ""
    qtype: str = "choice"
    stem: str = ""
    options: list = field(default_factory=list)
    answer: str = ""
    tolerance: float = 0.0
    keywords: list = field(default_factory=list)
    rationale: str = ""
    difficulty: float = 0.5
    origin: str = "llm"
    grader: str = "choice"
    citations: list = field(default_factory=list)
    teacher_verified: int = 0
    retired: int = 0
    created_at: str = ""

    def for_student(self) -> dict:
        """发给学生的视图：**不含答案、不含解析**。

        答案泄漏不是小事——一次泄漏会让这道题在整个题库里作废。
        """
        return {
            "id": self.id, "kp_id": self.kp_id, "kp_name": self.kp_name,
            "qtype": self.qtype, "stem": self.stem, "options": self.options,
            "difficulty": self.difficulty,
            "teacher_verified": self.teacher_verified,
        }


def signature(stem: str, answer: str = "") -> str:
    """去重指纹：去掉空白与标点后取哈希。同一知识点下重复题面直接被 UNIQUE 挡住。"""
    norm = re.sub(r"[\s，。；：、,\.;:?？!！（）()]+", "", f"{stem}|{answer}")[:200]
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def _row_to_q(r: dict) -> Question:
    return Question(
        id=r["id"], kp_id=r["kp_id"], kp_name=r.get("kp_name") or "",
        qtype=r["qtype"], stem=r["stem"], options=loads(r["options"], []),
        answer=r["answer"], tolerance=r["tolerance"], keywords=loads(r["keywords"], []),
        rationale=r["rationale"], difficulty=r["difficulty"], origin=r["origin"],
        grader=r["grader"], citations=loads(r["citations"], []),
        teacher_verified=r["teacher_verified"], retired=r["retired"],
        created_at=r["created_at"],
    )


def add(
    kp_id: int,
    stem: str,
    answer: str,
    qtype: str = "choice",
    options: list | None = None,
    rationale: str = "",
    difficulty: float = 0.5,
    origin: str = "llm",
    grader: str | None = None,
    keywords: list | None = None,
    tolerance: float = 0.0,
    citations: list | None = None,
    teacher_verified: int | None = None,
) -> int:
    """入库一道题。返回题目 id；重复题面返回已存在的 id（不新建）。

    `teacher_verified` 默认由 origin 决定：教师录入即已审，模型生成一律待审。
    """
    if qtype not in QTYPES:
        raise ValueError(f"未知题型：{qtype}")
    if origin not in ORIGINS:
        raise ValueError(f"未知来源：{origin}")
    if not (stem or "").strip():
        raise ValueError("题面为空")
    grader = grader or _default_grader(qtype)
    if grader not in GRADERS:
        raise ValueError(f"未知判分器：{grader}")
    if not citations and origin == "llm":
        # 无教材依据的模型出题一律拒收——RAG 约束是硬约束，不是建议
        raise ValueError("模型生成的题必须携带教材依据（citations）")
    verified = (teacher_verified if teacher_verified is not None
                else int(origin in ("teacher", "anchor")))

    db = get_db()
    sig = signature(stem, answer)
    exist = db.query_one("SELECT id FROM question WHERE kp_id=? AND signature=?", (kp_id, sig))
    if exist:
        return exist["id"]
    return db.execute(
        "INSERT INTO question(kp_id, qtype, stem, options, answer, tolerance, keywords,"
        " rationale, difficulty, origin, grader, citations, signature, teacher_verified,"
        " retired, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
        (kp_id, qtype, stem.strip(), dumps(options or []), str(answer), float(tolerance),
         dumps(keywords or []), rationale, float(difficulty), origin, grader,
         dumps(citations or []), sig, verified, now_str()),
    )


def _default_grader(qtype: str) -> str:
    return {"choice": "choice", "numeric": "numeric",
            "short": "keyword", "code": "keyword"}.get(qtype, "manual")


def get(question_id: int) -> Question | None:
    r = get_db().query_one(
        "SELECT q.*, k.name AS kp_name FROM question q"
        " JOIN knowledge_point k ON k.id=q.kp_id WHERE q.id=?",
        (question_id,),
    )
    return _row_to_q(r) if r else None


def list_for_kp(kp_id: int, verified_only: bool = True, limit: int = 50) -> list[Question]:
    sql = (
        "SELECT q.*, k.name AS kp_name FROM question q"
        " JOIN knowledge_point k ON k.id=q.kp_id"
        " WHERE q.kp_id=? AND q.retired=0 AND q.origin<>'anchor'"
    )
    if verified_only:
        sql += " AND q.teacher_verified=1"
    sql += " ORDER BY q.difficulty, q.id LIMIT ?"
    return [_row_to_q(r) for r in get_db().query(sql, (kp_id, limit))]


def list_pending(limit: int = 100) -> list[Question]:
    """待教师审核的模型草案。"""
    rows = get_db().query(
        "SELECT q.*, k.name AS kp_name FROM question q"
        " JOIN knowledge_point k ON k.id=q.kp_id"
        " WHERE q.teacher_verified=0 AND q.retired=0 AND q.origin<>'anchor'"
        " ORDER BY q.id LIMIT ?",
        (limit,),
    )
    return [_row_to_q(r) for r in rows]


def review(question_id: int, action: str, patch: dict | None = None) -> dict:
    """教师审核：accept 转正 / reject 退役 / edit 改后转正。

    这一步是把教师经验沉淀成资产的入口，与错误模式库的 teacher_verified 同构。
    """
    q = get(question_id)
    if not q:
        raise ValueError("题目不存在")
    db = get_db()
    if action == "reject":
        db.execute("UPDATE question SET retired=1 WHERE id=?", (question_id,))
        return {"id": question_id, "action": "reject", "retired": 1}
    if action == "edit" and patch:
        allowed = {"stem", "answer", "rationale", "difficulty", "tolerance"}
        sets, vals = [], []
        for k, v in patch.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if "options" in patch:
            sets.append("options=?")
            vals.append(dumps(patch["options"]))
        if "keywords" in patch:
            sets.append("keywords=?")
            vals.append(dumps(patch["keywords"]))
        if sets:
            db.execute(f"UPDATE question SET {', '.join(sets)} WHERE id=?", (*vals, question_id))
    db.execute("UPDATE question SET teacher_verified=1 WHERE id=?", (question_id,))
    return {"id": question_id, "action": action, "teacher_verified": 1}


def attempted_question_ids(student_id: int) -> set[int]:
    """该学生做过的题。选题时优先给没做过的，避免"背题"式的假掌握。"""
    rows = get_db().query(
        "SELECT source_ref FROM learning_event WHERE student_id=? AND event_type='quiz'",
        (student_id,),
    )
    out: set[int] = set()
    for r in rows:
        m = re.search(r"question:(\d+)", r["source_ref"] or "")
        if m:
            out.add(int(m.group(1)))
    return out


def stats() -> dict:
    db = get_db()
    row = db.query_one(
        "SELECT COUNT(*) AS total,"
        " SUM(CASE WHEN teacher_verified=1 AND retired=0 AND origin<>'anchor'"
        "          THEN 1 ELSE 0 END) AS usable,"
        " SUM(CASE WHEN teacher_verified=0 AND retired=0 THEN 1 ELSE 0 END) AS pending,"
        " SUM(retired) AS retired FROM question"
    ) or {}
    by_type = db.query(
        "SELECT qtype, COUNT(*) AS n FROM question WHERE retired=0 GROUP BY qtype"
    )
    return {
        "total": row.get("total") or 0,
        "usable": row.get("usable") or 0,
        "pending": row.get("pending") or 0,
        "retired": row.get("retired") or 0,
        "by_type": {r["qtype"]: r["n"] for r in by_type},
        "kp_covered": db.scalar(
            "SELECT COUNT(DISTINCT kp_id) FROM question WHERE retired=0 AND teacher_verified=1"
            " AND origin<>'anchor'"
        ) or 0,
        "anchor": db.scalar(
            "SELECT COUNT(*) FROM question WHERE retired=0 AND origin='anchor'") or 0,
    }
