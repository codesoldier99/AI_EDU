"""判分、排名与切线。

三条规矩，都来自 docs/measurement-plan.md：

1. **判分规则与平时完全一致**——直接复用 packages/quiz/grader。
   考试分数和平时掌握度必须在同一把尺子上，否则纵向比较无从谈起。
2. **判不了 ≠ 判错**（铁律 7）。程序题一律人工判分；规则判不出来的题
   记 `score=NULL` 进人工队列，**不当作 0 分参与排名**，否则会把
   "系统判不出来"变成"这个学生不会"。
3. **并列裁决规则考前公布、考后不得更改**。严格按分数切 53 人时，
   第 53/54 名同分是必然会发生的，事先定死才不会有争议。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.db import get_db, loads
from packages.core.models import Message
from packages.core.timeutil import now_str
from packages.quiz import bank, grader
from packages.state import tracker

# 并列裁决顺序（measurement-plan §4.2），考前公布。
# 同时保证 running variable 严格单调，RDD 才是干净的。
TIEBREAK = (
    ("total_score", "总分高者优先"),
    ("score_a", "概念锚题得分高者优先——概念比操作更能预测长期发展"),
    ("score_program", "程序题得分高者优先"),
    ("submit_epoch", "交卷时间早者优先（精确到秒，取负值使早者排前）"),
)


@dataclass
class GradeSummary(Message):
    session_id: int = 0
    student_id: int = 0
    score_a: float = 0.0
    score_b: float = 0.0
    total_score: float = 0.0
    n_auto: int = 0
    n_pending: int = 0
    max_possible: float = 0.0     # 含待判题的分数上限，供教师判断是否影响切线
    note: str = "待人工判分的题目按 0 分计入当前总分，但同时给出分数上限"


def grade_session(session_id: int, write_events: bool = True) -> GradeSummary:
    """判一份卷子。可重复调用（教师改分后重算），但事件只在第一次交卷时写。"""
    db = get_db()
    s = db.query_one("SELECT * FROM exam_session WHERE id=?", (session_id,))
    if not s:
        raise ValueError("考试会话不存在")
    rows = db.query("SELECT * FROM exam_answer WHERE session_id=? ORDER BY id", (session_id,))

    out = GradeSummary(session_id=session_id, student_id=s["student_id"])
    for r in rows:
        q = bank.get(r["question_id"])
        if not q:
            continue
        # 人工判分的题（程序题）保留教师已给的分；未判则挂起
        if q.grader == "manual":
            if r["score"] is None:
                out.n_pending += 1
                out.max_possible += r["points"]
                continue
            gained, correct, by, detail = r["score"], None, r["graded_by"] or "teacher", ""
        elif not (r["response"] or "").strip():
            # 空白卷面：考试规则上就是 0 分，没有"判不了"这回事。
            # 但**不写 BKT 证据**——分不清他是不会，还是时间不够没做到
            # （心理测量学里 omitted 与 not-reached 必须与答错分开统计）。
            gained, correct, by, detail = 0.0, None, "rule:blank", "未作答"
            db.execute(
                "UPDATE exam_answer SET is_correct=0, score=0, graded_by=?, grade_detail=?,"
                " updated_at=? WHERE id=?", (by, detail, now_str(), r["id"]))
            out.n_auto += 1
        else:
            g = grader.grade(q, r["response"] or "")
            if g.is_correct is None:
                out.n_pending += 1
                out.max_possible += r["points"]
                db.execute(
                    "UPDATE exam_answer SET is_correct=NULL, score=NULL, graded_by=?,"
                    " grade_detail=?, updated_at=? WHERE id=?",
                    (g.graded_by, g.detail, now_str(), r["id"]),
                )
                continue
            gained = r["points"] if g.is_correct else 0.0
            correct, by, detail = int(g.is_correct), g.graded_by, g.detail
            db.execute(
                "UPDATE exam_answer SET is_correct=?, score=?, graded_by=?, grade_detail=?,"
                " updated_at=? WHERE id=?",
                (correct, gained, by, detail, now_str(), r["id"]),
            )
            out.n_auto += 1

        out.max_possible += r["points"]
        if r["part"] == "A":
            out.score_a += gained
        else:
            out.score_b += gained

        if write_events and correct is not None and not s["graded_at"]:
            # 唯一通道仍是 tracker。source='exam' 权重 1.0——考试是最强的证据。
            tracker.record(
                student_id=s["student_id"], event_type="exam", kp_id=q.kp_id,
                is_correct=bool(correct), source="exam",
                source_ref=f"exam:{s['exam_id']}#session:{session_id}#question:{q.id}",
                payload={"response": (r["response"] or "")[:500], "part": r["part"],
                         "points": r["points"], "score": gained, "graded_by": by},
            )

    out.score_a = round(out.score_a, 2)
    out.score_b = round(out.score_b, 2)
    out.total_score = round(out.score_a + out.score_b, 2)
    out.max_possible = round(out.max_possible, 2)
    db.execute(
        "UPDATE exam_session SET score_a=?, score_b=?, total_score=?, n_pending=?,"
        " graded_at=COALESCE(graded_at, ?) WHERE id=?",
        (out.score_a, out.score_b, out.total_score, out.n_pending, now_str(), session_id),
    )
    return out


def teacher_score(session_id: int, question_id: int, score: float,
                  note: str = "") -> dict:
    """人工给分（程序题，或规则判不出来的题）。给完自动重算总分。"""
    db = get_db()
    a = db.query_one(
        "SELECT * FROM exam_answer WHERE session_id=? AND question_id=?",
        (session_id, question_id),
    )
    if not a:
        raise ValueError("该考生没有这道题")
    v = max(0.0, min(float(score), a["points"]))
    db.execute(
        "UPDATE exam_answer SET score=?, is_correct=?, graded_by='teacher',"
        " grade_detail=?, updated_at=? WHERE id=?",
        (v, int(v >= a["points"] * 0.6), note[:300], now_str(), a["id"]),
    )
    # 人工判分同样要落进事件流，否则 L2 里会缺掉这部分证据
    s = db.query_one("SELECT * FROM exam_session WHERE id=?", (session_id,))
    q = bank.get(question_id)
    if s and q:
        tracker.record(
            student_id=s["student_id"], event_type="exam", kp_id=q.kp_id,
            is_correct=bool(v >= a["points"] * 0.6), source="teacher",
            source_ref=f"exam:{s['exam_id']}#session:{session_id}#question:{question_id}",
            payload={"score": v, "points": a["points"], "graded_by": "teacher",
                     "note": note[:300]},
        )
    g = grade_session(session_id, write_events=False)
    return {"session_id": session_id, "question_id": question_id, "score": v,
            "total_score": g.total_score, "n_pending": g.n_pending}


def pending_queue(exam_id: int, limit: int = 200) -> list[dict]:
    """待人工判分队列。程序题全在这里。"""
    rows = get_db().query(
        "SELECT a.id, a.session_id, a.question_id, a.part, a.points, a.response,"
        " a.grade_detail, s.student_id, st.sid, st.name, q.stem, q.answer, q.rationale"
        " FROM exam_answer a"
        " JOIN exam_session s ON s.id=a.session_id"
        " JOIN student st ON st.id=s.student_id"
        " JOIN question q ON q.id=a.question_id"
        " WHERE s.exam_id=? AND a.score IS NULL AND s.status<>'open'"
        " ORDER BY a.question_id, st.sid LIMIT ?",
        (exam_id, limit),
    )
    return rows


# ---------------------------------------------------------------- 排名与切线
@dataclass
class RankRow(Message):
    rank: int = 0
    student_id: int = 0
    sid: str = ""
    name: str = ""
    score_a: float = 0.0
    score_b: float = 0.0
    score_program: float = 0.0
    total_score: float = 0.0
    submitted_at: str = ""
    n_pending: int = 0
    selected: bool = False
    tie_group: int = 1            # 与几人总分并列
    decided_by: str = ""          # 实际生效的裁决依据


@dataclass
class Ranking(Message):
    exam_id: int = 0
    cutoff_n: int = 53
    cutoff_score: float = 0.0
    rows: list = field(default_factory=list)
    n_graded: int = 0
    n_pending_sessions: int = 0
    tie_at_cutoff: int = 0
    margin: float = 0.0           # 第 53 名与第 54 名的分差
    warnings: list = field(default_factory=list)
    tiebreak_rules: list = field(default_factory=lambda: [d for _k, d in TIEBREAK])


def ranking(exam_id: int, cutoff_n: int = 53) -> Ranking:
    """按 measurement-plan §4.2 的规则排名并切线。"""
    db = get_db()
    sessions = db.query(
        "SELECT s.*, st.sid, st.name FROM exam_session s"
        " JOIN student st ON st.id=s.student_id"
        " WHERE s.exam_id=? AND s.status<>'voided'",
        (exam_id,),
    )
    rows: list[RankRow] = []
    for s in sessions:
        prog = db.scalar(
            "SELECT COALESCE(SUM(a.score),0) FROM exam_answer a JOIN question q"
            " ON q.id=a.question_id WHERE a.session_id=? AND q.grader='manual'",
            (s["id"],),
        ) or 0.0
        rows.append(RankRow(
            student_id=s["student_id"], sid=s["sid"], name=s["name"],
            score_a=s["score_a"] or 0.0, score_b=s["score_b"] or 0.0,
            score_program=round(float(prog), 2),
            total_score=s["total_score"] or 0.0,
            submitted_at=s["submitted_at"] or "", n_pending=s["n_pending"] or 0,
        ))

    # 排序键：总分 -> A 部分 -> 程序题 -> 交卷时间（早者优先）
    rows.sort(key=lambda r: (-r.total_score, -r.score_a, -r.score_program,
                             r.submitted_at or "9999"))

    res = Ranking(exam_id=exam_id, cutoff_n=cutoff_n)
    totals = [r.total_score for r in rows]
    for i, r in enumerate(rows):
        r.rank = i + 1
        r.selected = i < cutoff_n
        r.tie_group = totals.count(r.total_score)
        if r.tie_group > 1:
            same = [x for x in rows if x.total_score == r.total_score]
            if len({x.score_a for x in same}) > 1:
                r.decided_by = "A 部分得分"
            elif len({x.score_program for x in same}) > 1:
                r.decided_by = "程序题得分"
            else:
                r.decided_by = "交卷时间"
        rows[i] = r

    res.rows = [r.to_dict() for r in rows]
    res.n_graded = sum(1 for r in rows if r.n_pending == 0)
    res.n_pending_sessions = sum(1 for r in rows if r.n_pending > 0)
    if len(rows) >= cutoff_n:
        res.cutoff_score = rows[cutoff_n - 1].total_score
        if len(rows) > cutoff_n:
            res.margin = round(rows[cutoff_n - 1].total_score
                               - rows[cutoff_n].total_score, 2)
        res.tie_at_cutoff = totals.count(res.cutoff_score)

    # 这三条警告直接关系到 RDD 能不能做
    if res.n_pending_sessions:
        res.warnings.append(
            f"仍有 {res.n_pending_sessions} 人存在待人工判分的题目，"
            "此时切线不作数——程序题分数会改变排序")
    if res.tie_at_cutoff > 3:
        res.warnings.append(
            f"分数线 {res.cutoff_score} 分上并列 {res.tie_at_cutoff} 人，"
            "已启用并列裁决规则；并列人数过多会削弱断点回归的识别力")
    if res.margin == 0 and len(rows) > cutoff_n:
        res.warnings.append("第 53 名与第 54 名总分相同，切线完全依赖并列裁决规则")
    return res


def export_rows(exam_id: int) -> list[dict]:
    """导出分析数据集：一人一行，含 RDD 的 running variable 与协变量。"""
    r = ranking(exam_id)
    cutoff = r.cutoff_score
    out = []
    for row in r.rows:
        out.append({
            "sid": row["sid"],
            "student_id": row["student_id"],
            "total_score": row["total_score"],       # RDD 的 running variable
            "score_anchor_A": row["score_a"],        # 纵向可比的那部分
            "score_selection_B": row["score_b"],
            "score_program": row["score_program"],
            "rank": row["rank"],
            "assigned_experimental": int(row["selected"]),   # 处理变量
            "centered_score": round(row["total_score"] - cutoff, 2),  # R - c
            "submitted_at": row["submitted_at"],
            "n_pending": row["n_pending"],
        })
    return out
