"""自动出题 · 自动批改 · 针对性练习。

吸收自 DeepTutor 的 Quiz + Mastery Path，但把它劈成了三段，各归其位：

    练什么   packages/quiz/selector.py   确定性——复检 / 待验证 / 缺口 / 根因
    题面     本模块（LLM）               只写题面，且必须被 RAG 约束在教材内
    算不算对 packages/quiz/grader.py     确定性——判不了就交给人，绝不猜

DeepTutor 把这三件事放在同一个 agent loop 里"一起想"，好处是灵活，
代价是没有一处可以被教师质疑、被系统复算。我们不能付这个代价：
错一次不要紧，错了不知道错在哪、下次还错，才是不能接受的。

两条硬约束：
1. 模型出的题一律进待审队列（`teacher_verified=0`），组卷默认只用已审题。
   题库是资产，掺进去的每一道烂题都会长期污染诊断结论。
2. 发给学生的题**永远不带答案与解析**（`Question.for_student()`），
   批改之后才给解析。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from packages.core.db import dumps, get_db, loads
from packages.core.models import Message, confidence_from_evidence
from packages.core.timeutil import now_str
from packages.errors import service as errors
from packages.graph import repo as graph_repo
from packages.quiz import bank, grader, selector
from packages.rag import retriever
from packages.skills import quiz_guidance
from packages.state import repo as state_repo
from packages.state import tracker

from .base import Agent, AgentOutput

# 判分方式 -> 证据来源。判定越确定，写进 L2 的权重越高（见 tracker.SOURCE_WEIGHT）
GRADER_SOURCE = {
    "rule:choice": "quiz",
    "rule:numeric": "quiz",
    "rule:keyword": "practice",
    "teacher": "teacher",
}


@dataclass
class DraftResult(Message):
    kp_id: int = 0
    kp_name: str = ""
    accepted: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    degraded: bool = False
    note: str = "模型出题一律进待审队列，教师确认后方可用于组卷"


@dataclass
class PaperView(Message):
    paper_id: int = 0
    student_id: int = 0
    purpose: str = ""
    questions: list = field(default_factory=list)   # 学生视图，不含答案
    reasons: list = field(default_factory=list)
    missing_bank: list = field(default_factory=list)
    confidence: float = 0.0
    evidence_count: int = 0
    caveat: str = ""
    narrative: str = ""


@dataclass
class SubmitResult(Message):
    paper_id: int = 0
    student_id: int = 0
    items: list = field(default_factory=list)
    n_graded: int = 0
    n_pending: int = 0
    note: str = ("判分走确定性规则；判不了的题不计入掌握度，"
                 "进教师人工队列。系统不以正确率为优化目标。")


# ---------------------------------------------------------------- 草案解析
BLOCK_KEYS = ("题目", "选项", "答案", "解析", "容差", "关键词", "难度")
_KEY_RE = re.compile(rf"^\s*\[({'|'.join(BLOCK_KEYS)})\]\s*(.*)$")


def parse_drafts(text: str, qtype: str) -> tuple[list[dict], list[dict]]:
    """把模型输出解析成结构化题目。**宁缺毋滥**：任何一项缺失即整块丢弃。

    与代码审查那边同一条规矩——解析不出来的就当没有，
    绝不"尽量还原"。还原出来的东西没人能为它负责。
    """
    blocks: list[dict] = []
    cur: dict = {}
    for raw in (text or "").splitlines():
        m = _KEY_RE.match(raw)
        if not m:
            if cur and cur.get("_last"):
                cur[cur["_last"]] = (cur[cur["_last"]] + " " + raw.strip()).strip()
            continue
        key, val = m.group(1), m.group(2).strip()
        if key == "题目":
            if cur:
                blocks.append(cur)
            cur = {}
        cur[key] = val
        cur["_last"] = key
    if cur:
        blocks.append(cur)

    ok, bad = [], []
    for b in blocks:
        b.pop("_last", None)
        parsed, why = _validate(b, qtype)
        (ok.append(parsed) if parsed else bad.append({"block": b, "reason": why}))
    return ok, bad


def _validate(b: dict, qtype: str) -> tuple[dict | None, str]:
    stem = (b.get("题目") or "").strip()
    ans = (b.get("答案") or "").strip()
    if len(stem) < 8:
        return None, "题面缺失或过短"
    if not ans:
        return None, "缺少答案"
    out = {"stem": stem, "answer": ans, "rationale": (b.get("解析") or "").strip(),
           "qtype": qtype, "options": [], "keywords": [], "tolerance": 0.0,
           "difficulty": _num(b.get("难度"), 0.5)}
    if qtype == "choice":
        opts = [o.strip() for o in re.split(r"\s*\|\s*", b.get("选项", "")) if o.strip()]
        if len(opts) < 3:
            return None, "选项少于 3 个"
        letters = [re.match(r"^([A-H])[\.、\)]", o) for o in opts]
        if not all(letters):
            return None, "选项未按 'A. xxx' 编号"
        if not re.fullmatch(r"[A-H]+", ans.upper()):
            return None, "答案不是选项字母"
        if any(c not in {m.group(1) for m in letters} for c in ans.upper()):
            return None, "答案字母不在选项范围内"
        out["options"] = opts
        out["answer"] = ans.upper()
    elif qtype == "numeric":
        from packages.tools import calc

        try:
            calc.safe_eval(ans)
        except calc.CalcError:
            return None, "答案不是可计算的数值表达式"
        out["tolerance"] = _num(b.get("容差"), 0.01)
    else:
        kws = [k.strip() for k in re.split(r"[、,，;；]\s*", b.get("关键词", "")) if k.strip()]
        if len(kws) < 2:
            return None, "主观题必须给出至少 2 个判分关键词"
        out["keywords"] = kws
    if not out["rationale"]:
        return None, "缺少解析（解析将来要进错误模式库，不能省）"
    return out, ""


def _num(v, default: float) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 智能体
class QuizAgent(Agent):
    name = "quiz"
    system_prompt = (
        "你是命题助教。硬约束：\n"
        "1) 只依据给定的教材依据出题，不得引入教材之外的事实；\n"
        "2) 严格按给定的行格式输出，每题一组，不要写任何额外说明；\n"
        "3) 一道题只考一个知识点；干扰项必须对应一种真实的想错方式；\n"
        "4) 题面不得出现暗示答案的措辞。"
    )
    # 组卷说明不是题目：必须换一套提示词，否则模型会照着出题格式回一道题
    narrate_prompt = (
        "你是学习助教。用两三句话说明这次练习安排的目标与理由。硬约束：\n"
        "1) 只依据给定字段组织语言，不得引入新的事实判断；\n"
        "2) 不要出题、不要给答案、不要写任何题目格式；\n"
        "3) 不评价学生聪明与否，不提正确率。"
    )

    # ---------- 出题（进待审队列） ----------
    def draft(self, kp_id: int, n: int = 3, qtype: str = "choice") -> DraftResult:
        kp = graph_repo.get_kp(kp_id)
        if not kp:
            return DraftResult(kp_id=kp_id, rejected=[{"reason": "知识点不存在"}])
        ctx = retriever.grounded_context(
            f"{kp.name} {kp.description}", intent="quiz", kp_id=kp_id, top_k=3
        )
        res = DraftResult(kp_id=kp_id, kp_name=kp.name,
                          citations=[c.to_dict() for c in ctx.citations])
        if not ctx.hits:
            # RAG 拿不到材料就不出题。无依据的题比没有题更糟。
            res.rejected.append({"reason": "未检索到教材依据，拒绝出题"})
            return res

        fields = {
            "意图": "命题出卷",
            "知识点": kp.name,
            "知识点说明": kp.description,
            "题型": {"choice": "单选题", "numeric": "数值计算题",
                     "short": "简答题", "code": "代码题"}.get(qtype, "单选题"),
            "题量": n,
            "教材依据": ctx.context_text(3)[:1200],
            "出题要求": quiz_guidance(qtype, kp.kp_type),
            "输出格式": self._format_hint(qtype),
        }
        text, degraded = self.express(fields, max_tokens=1200)
        res.degraded = degraded
        ok, bad = parse_drafts(text, qtype)
        res.rejected = bad
        for item in ok[:n]:
            try:
                qid = bank.add(
                    kp_id=kp_id, stem=item["stem"], answer=item["answer"],
                    qtype=qtype, options=item["options"], rationale=item["rationale"],
                    difficulty=item["difficulty"], origin="llm",
                    keywords=item["keywords"], tolerance=item["tolerance"],
                    citations=[c.to_dict() for c in ctx.citations],
                )
            except ValueError as exc:
                res.rejected.append({"block": item, "reason": str(exc)})
                continue
            res.accepted.append({"question_id": qid, **item})
        return res

    @staticmethod
    def _format_hint(qtype: str) -> str:
        if qtype == "choice":
            return ("每题四行：[题目] …／[选项] A. …| B. …| C. …| D. …／"
                    "[答案] 单个字母／[解析] …")
        if qtype == "numeric":
            return "每题四行：[题目] …／[答案] 可计算的表达式或数值／[容差] 相对误差／[解析] …"
        return "每题四行：[题目] …／[答案] 参考答案／[关键词] 判分关键词、顿号分隔／[解析] …"

    # ---------- 组卷 ----------
    def assemble(self, student_id: int, task_id: int | None = None, per_kp: int = 1,
                 limit: int | None = None, verified_only: bool = True) -> PaperView:
        plan = selector.practice_plan(student_id, task_id=task_id, limit=limit)
        questions, reasons = selector.pick_questions(
            student_id, plan.targets, per_kp=per_kp, verified_only=verified_only
        )
        missing = selector.coverage_gaps(student_id, plan.targets, verified_only)
        pid = get_db().execute(
            "INSERT INTO quiz_paper(student_id, purpose, scope_ref, question_ids, reasons,"
            " created_at) VALUES(?,?,?,?,?,?)",
            (student_id,
             plan.targets[0]["kind"] if plan.targets else "mixed",
             f"task:{task_id}" if task_id else "",
             dumps([q.id for q in questions]), dumps(reasons), now_str()),
        )
        view = PaperView(
            paper_id=pid, student_id=student_id,
            purpose=plan.targets[0]["kind_label"] if plan.targets else "无",
            questions=[q.for_student() for q in questions],
            reasons=reasons, missing_bank=missing,
            confidence=plan.confidence, evidence_count=plan.evidence_count,
            caveat=plan.caveat,
        )
        fields = {
            "意图": "练习安排",
            "题量": len(questions),
            "练习目标": "；".join(
                f"{t['name']}（{t['kind_label']}）" for t in plan.targets[:3]
            ) or "（暂无）",
            "选中理由": plan.targets[0]["reason"] if plan.targets else "",
            "题库缺口": "、".join(m["name"] for m in missing[:3]),
        }
        view.narrative, _ = self.express(fields, max_tokens=300,
                                         system=self.narrate_prompt)
        return view

    # ---------- 批改 ----------
    def submit(self, paper_id: int, answers: dict, student_id: int | None = None) -> SubmitResult:
        db = get_db()
        paper = db.query_one("SELECT * FROM quiz_paper WHERE id=?", (paper_id,))
        if not paper:
            raise ValueError("试卷不存在")
        sid = student_id or paper["student_id"]
        if sid != paper["student_id"]:
            raise ValueError("试卷与学生不匹配")
        qids = loads(paper["question_ids"], [])
        res = SubmitResult(paper_id=paper_id, student_id=sid)
        for qid in qids:
            raw = answers.get(str(qid), answers.get(qid, ""))
            q = bank.get(int(qid))
            if not q:
                continue
            g = grader.grade(q, str(raw))
            source = GRADER_SOURCE.get(g.graded_by, "practice")
            # 唯一通道：tracker。判不了（is_correct=None）时事件照写、掌握度不动。
            tr = tracker.record(
                student_id=sid, event_type="quiz", kp_id=q.kp_id,
                is_correct=g.is_correct, source=source,
                source_ref=f"paper:{paper_id}#question:{q.id}",
                payload={"response": str(raw)[:800], "graded_by": g.graded_by,
                         "grade_confidence": g.confidence, "detail": g.detail,
                         "pending_teacher": g.is_correct is None},
            )
            if g.is_correct is False and str(raw).strip():
                errors.record_error(
                    student_id=sid, kp_id=q.kp_id, raw_text=str(raw)[:500],
                    event_id=tr.event_id, description=f"{q.stem[:60]}｜{g.detail}",
                )
            res.items.append({
                "question_id": q.id, "kp_id": q.kp_id, "kp_name": q.kp_name,
                "response": str(raw), "is_correct": g.is_correct,
                "graded_by": g.graded_by, "grade_confidence": g.confidence,
                "detail": g.detail,
                # 解析只在批改之后给出，且判不了的题不给（否则等于送答案）
                "rationale": q.rationale if g.is_correct is not None else "",
                "answer": q.answer if g.is_correct is not None else "",
                "p_after": tr.p_after if tr.updated else None,
            })
            if g.is_correct is None:
                res.n_pending += 1
            else:
                res.n_graded += 1
        db.execute("UPDATE quiz_paper SET submitted_at=? WHERE id=?", (now_str(), paper_id))
        return res

    # ---------- 教师侧 ----------
    def pending_grading(self, klass: str | None = None, limit: int = 50) -> list[dict]:
        """确定性判分放弃的那些作答，排队等教师。"""
        rows = get_db().query(
            "SELECT e.id AS event_id, e.student_id, e.kp_id, e.source_ref, e.payload,"
            " e.occurred_at, s.sid, s.name, s.klass, k.name AS kp_name"
            " FROM learning_event e JOIN student s ON s.id=e.student_id"
            " JOIN knowledge_point k ON k.id=e.kp_id"
            " WHERE e.event_type='quiz' AND e.is_correct IS NULL"
            " ORDER BY e.id DESC LIMIT ?",
            (limit * 4,),
        )
        out = []
        for r in rows:
            if klass and r["klass"] != klass:
                continue
            p = loads(r["payload"], {})
            if not p.get("pending_teacher"):
                continue
            m = re.search(r"question:(\d+)", r["source_ref"] or "")
            q = bank.get(int(m.group(1))) if m else None
            out.append({
                "event_id": r["event_id"], "student_id": r["student_id"],
                "sid": r["sid"], "name": r["name"], "kp_name": r["kp_name"],
                "question_id": q.id if q else None,
                "stem": q.stem if q else "", "answer": q.answer if q else "",
                "response": p.get("response", ""), "detail": p.get("detail", ""),
                "occurred_at": r["occurred_at"],
            })
            if len(out) >= limit:
                break
        return out

    def teacher_grade(self, event_id: int, is_correct: bool, note: str = "") -> dict:
        """教师人工判分：**补一条新事件**，不改旧事件。

        事件流只追加。旧的"判不了"记录留在那里，是这套判分规则需要改进的证据。
        """
        ev = state_repo.get_event(event_id)
        if not ev:
            raise ValueError("事件不存在")
        tr = tracker.record(
            student_id=ev["student_id"], event_type="quiz", kp_id=ev["kp_id"],
            is_correct=bool(is_correct), source="teacher",
            source_ref=ev["source_ref"],
            payload={"graded_by": "teacher", "note": note[:300],
                     "supersedes_event": event_id},
        )
        return tr.to_dict()

    def review_queue(self, limit: int = 50) -> dict:
        pend = bank.list_pending(limit)
        return {
            "items": [{**q.to_dict(), "kp_name": q.kp_name} for q in pend],
            "stats": bank.stats(),
            "note": "模型出的题必须逐题过教师这一关；退回的题标 retired，不删除",
        }

    # ---------- 学生侧汇总 ----------
    def history(self, student_id: int, limit: int = 20) -> dict:
        rows = state_repo.list_events(
            student_id=student_id, event_type="quiz", order="occurred_at"
        )[-limit:]
        items = []
        for r in rows:
            m = re.search(r"question:(\d+)", r["source_ref"] or "")
            items.append({
                "occurred_at": r["occurred_at"], "kp_id": r["kp_id"],
                "is_correct": r["is_correct"],
                "graded_by": (r["payload"] or {}).get("graded_by", ""),
                "question_id": int(m.group(1)) if m else None,
            })
        n = len(items)
        return {
            "items": items,
            "evidence_count": n,
            "confidence": confidence_from_evidence(n),
            "note": "此处只呈现作答记录本身；是否掌握看知识点掌握度，不看这里的对错比例",
        }
