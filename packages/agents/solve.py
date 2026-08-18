"""分步解题：把推理链条掰开，但**一次只交出一步**。

DeepTutor 的 Deep Solve 是"把完整推理过程讲清楚"。讲得越清楚，学生看得越舒服，
学到的越少——这不是猜测，是已经量化过的：无限制的 AI 辅助会让学生的独立考试成绩
下降约 17%，而引导式提示显著优于传统方式（DEVELOPMENT_PLAN §〇）。

所以这里做的是同一件事的反面：
    · 模型只产出**步骤骨架**（每步"要做什么"），不产出结论；
    · 每一步先由学生作答，**判定走确定性工具**（数值走求值器，文字走关键词覆盖）；
    · 判不过就走已有的三级降级，降级留痕（写 escalation 事件）；
    · 只有降到第 3 级，系统才交出那一步的结论，并标记需教师介入。

证据权重随判定的确定性走：数值校验通过记 practice(0.7)，
文字规则判定记 ask(0.5)。判不了就不写证据——这一条比什么都重要。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.db import dumps, get_db, loads
from packages.core.models import Message
from packages.core.timeutil import now_str
from packages.graph import repo as graph_repo
from packages.quiz import grader
from packages.rag import retriever
from packages.state import repo as state_repo
from packages.state import tracker
from packages.tools import calc

from .asking import ESCALATION_LABELS
from .base import Agent, AgentOutput

MAX_STEPS = 8
_KEY_RE = re.compile(r"^\s*\[(步骤|知识点|校验|结论)\]\s*(.*)$")


@dataclass
class StepView(Message):
    idx: int = 0
    kp_id: int | None = None
    kp_name: str = ""
    ask: str = ""
    check_kind: str = "text"
    student_text: str = ""
    passed: bool | None = None
    revealed: bool = False
    expected: str = ""      # 未 revealed 时恒为空字符串


@dataclass
class SolveView(Message):
    session_id: int = 0
    student_id: int = 0
    problem: str = ""
    status: str = "open"
    cursor: int = 0
    n_steps: int = 0
    attempts: int = 0
    escalation_level: int = 0
    escalation_label: str = "无"
    steps: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    narrative: str = ""
    degraded: bool = False
    confidence: float = 0.0
    evidence_count: int = 0
    caveat: str = ""
    gives_answer: bool = False


# ---------------------------------------------------------------- 骨架解析
def parse_steps(text: str) -> tuple[list[dict], list[dict]]:
    """解析步骤骨架。缺 [步骤] 或 [结论] 的块整块丢弃（宁缺毋滥）。"""
    blocks, cur = [], {}
    for raw in (text or "").splitlines():
        m = _KEY_RE.match(raw)
        if not m:
            if cur and cur.get("_last"):
                cur[cur["_last"]] = (cur[cur["_last"]] + " " + raw.strip()).strip()
            continue
        key, val = m.group(1), m.group(2).strip()
        if key == "步骤":
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
        ask, expected = (b.get("步骤") or "").strip(), (b.get("结论") or "").strip()
        if len(ask) < 4:
            bad.append({"block": b, "reason": "步骤描述缺失"})
            continue
        if not expected:
            bad.append({"block": b, "reason": "缺少该步结论（系统留存，未通过前不下发）"})
            continue
        kind = (b.get("校验") or "").strip().lower()
        if kind not in ("numeric", "text"):
            kind = "numeric" if _looks_numeric(expected) else "text"
        ok.append({"ask": ask, "expected": expected, "check_kind": kind,
                   "kp_hint": (b.get("知识点") or "").strip()})
    return ok[:MAX_STEPS], bad


def _looks_numeric(text: str) -> bool:
    try:
        calc.safe_eval(text)
        return True
    except calc.CalcError:
        return False


def expected_keywords(expected: str, k: int = 4) -> list[str]:
    """从该步结论里抽取确定性判分关键词。

    抽词规则固定且可复算：按标点切段，取长度 >= 2 的片段，去重后取前 k 个。
    刻意不用模型抽词——抽词一旦不确定，"这一步算不算过"就不可复算了。
    """
    parts = [p.strip() for p in re.split(r"[，,。；;：:、\s（）()]+", expected or "") if p.strip()]
    out: list[str] = []
    for p in parts:
        if len(p) >= 2 and p not in out:
            out.append(p)
    return out[:k]


# ---------------------------------------------------------------- 智能体
class SolveAgent(Agent):
    name = "solve"
    system_prompt = (
        "你是解题教练。硬约束：\n"
        "1) 只输出**步骤骨架**，每一步写清'要做什么'，不要写成完整解答；\n"
        "2) 每步的 [结论] 只写这一步的结果本身，一句话，不要展开推导；\n"
        "3) 只使用给定的教材依据，不得引入教材之外的方法；\n"
        "4) 严格按行格式输出，不写任何额外说明。"
    )
    # 每一轮说给学生听的话不是骨架：同样必须换提示词（见 base.Agent.express 的说明）
    turn_prompt = (
        "你是解题教练，正在陪学生一步一步做。硬约束：\n"
        "1) 只依据给定字段组织语言，不得引入新的事实判断；\n"
        "2) 严格遵守给定的「硬约束」字段——它规定了这一轮能说到什么程度；\n"
        "3) 两三句话，语气平实，不催促、不评价；\n"
        "4) 不要输出任何行格式标记。"
    )

    # ---------- 开一局 ----------
    def start(self, student_id: int, problem: str, kp_id: int | None = None) -> SolveView:
        ctx = retriever.grounded_context(problem, intent="ask", kp_id=kp_id, top_k=3)
        kp_ids = self._infer_kps(ctx, kp_id)
        kp_names = [k.name for k in (graph_repo.get_kp(i) for i in kp_ids) if k]
        fields = {
            "意图": "分步解题骨架",
            "题目": problem[:800],
            "相关知识点": "、".join(kp_names) or "（未识别）",
            "教材依据": ctx.context_text(3)[:1000],
            "步数上限": MAX_STEPS,
            "输出格式": ("每步四行：[步骤] 要做什么／[知识点] 名称（可省）／"
                        "[校验] numeric 或 text／[结论] 该步的结果，一句话"),
        }
        text, degraded = self.express(fields, max_tokens=900)
        steps, bad = parse_steps(text)

        db = get_db()
        now = now_str()
        sid = db.execute(
            "INSERT INTO solve_session(student_id, problem, kp_ids, n_steps, cursor,"
            " attempts, escalation_level, status, citations, created_at, updated_at)"
            " VALUES(?,?,?,?,0,0,0,?,?,?,?)",
            (student_id, problem[:2000], dumps(kp_ids), len(steps),
             "open" if steps else "closed",
             dumps([c.to_dict() for c in ctx.citations]), now, now),
        )
        for i, s in enumerate(steps):
            db.execute(
                "INSERT INTO solve_step(session_id, idx, kp_id, ask, check_kind, expected,"
                " created_at) VALUES(?,?,?,?,?,?,?)",
                (sid, i, self._step_kp(s["kp_hint"], kp_ids), s["ask"],
                 s["check_kind"], s["expected"], now),
            )
        tracker.record(
            student_id=student_id, event_type="solve_start",
            kp_id=kp_ids[0] if kp_ids else None, is_correct=None, source="ask",
            source_ref=f"solve:{sid}",
            payload={"problem": problem[:300], "n_steps": len(steps),
                     "rejected_blocks": len(bad)},
        )
        v = self.view(sid)
        v.degraded = degraded
        if not steps:
            v.caveat = "未能从教材依据中拆出可校验的步骤，建议改用引导追问"
        return v

    # ---------- 学生答一步 ----------
    def answer(self, session_id: int, text: str, stuck: bool = False) -> SolveView:
        db = get_db()
        s = db.query_one("SELECT * FROM solve_session WHERE id=?", (session_id,))
        if not s:
            raise ValueError("会话不存在")
        if s["status"] != "open":
            return self.view(session_id)
        step = db.query_one(
            "SELECT * FROM solve_step WHERE session_id=? AND idx=?", (session_id, s["cursor"])
        )
        if not step:
            return self.view(session_id)

        res = self._check(step, text)
        attempts = s["attempts"] + 1
        level = s["escalation_level"]

        # 判定确定 -> 写证据；判不了 -> 只留行为事件，不动掌握度
        if res.is_correct is not None and step["kp_id"]:
            tracker.record(
                student_id=s["student_id"], event_type="practice", kp_id=step["kp_id"],
                is_correct=res.is_correct,
                source="practice" if res.graded_by == "rule:numeric" else "ask",
                source_ref=f"solve:{session_id}#step:{step['idx']}",
                payload={"response": text[:500], "graded_by": res.graded_by,
                         "detail": res.detail},
            )

        db.execute(
            "UPDATE solve_step SET student_text=?, passed=? WHERE id=?",
            (text[:2000], None if res.is_correct is None else int(res.is_correct), step["id"]),
        )

        if res.is_correct:
            cursor = s["cursor"] + 1
            done = cursor >= s["n_steps"]
            db.execute(
                "UPDATE solve_session SET cursor=?, attempts=0, status=?, updated_at=?"
                " WHERE id=?",
                (cursor, "done" if done else "open", now_str(), session_id),
            )
            return self.view(session_id, feedback=res.detail)

        # 未通过：按已有的降级梯度往下走一格，并留痕
        need = attempts >= CONFIG.teaching.escalation_attempts or stuck
        if need and level < 3:
            level += 1
            tracker.record(
                student_id=s["student_id"], event_type="escalation", kp_id=step["kp_id"],
                is_correct=None, source="ask",
                source_ref=f"solve:{session_id}#step:{step['idx']}",
                payload={"level": level, "label": ESCALATION_LABELS[level],
                         "reason": "自述卡住" if stuck else "多次尝试未通过",
                         "needs_teacher": level >= 3},
            )
            if level >= 3:
                db.execute("UPDATE solve_step SET revealed=1 WHERE id=?", (step["id"],))
        db.execute(
            "UPDATE solve_session SET attempts=?, escalation_level=?, updated_at=? WHERE id=?",
            (attempts, level, now_str(), session_id),
        )
        return self.view(session_id, feedback=res.detail)

    # ---------- 视图 ----------
    def view(self, session_id: int, feedback: str = "") -> SolveView:
        db = get_db()
        s = db.query_one("SELECT * FROM solve_session WHERE id=?", (session_id,))
        if not s:
            raise ValueError("会话不存在")
        rows = db.query(
            "SELECT * FROM solve_step WHERE session_id=? ORDER BY idx", (session_id,)
        )
        steps = []
        for r in rows:
            revealed = bool(r["revealed"])
            past = r["idx"] < s["cursor"]
            kp = graph_repo.get_kp(r["kp_id"]) if r["kp_id"] else None
            steps.append(StepView(
                idx=r["idx"], kp_id=r["kp_id"], kp_name=kp.name if kp else "",
                ask=r["ask"] if r["idx"] <= s["cursor"] else "",   # 未到的步骤不预告
                check_kind=r["check_kind"], student_text=r["student_text"],
                passed=None if r["passed"] is None else bool(r["passed"]),
                revealed=revealed,
                # 结论只在两种情况下下发：这一步已经过了，或者已降级到第 3 级
                expected=r["expected"] if (past or revealed) else "",
            ).to_dict())

        level = s["escalation_level"]
        cur = rows[s["cursor"]] if s["cursor"] < len(rows) else None
        cites = loads(s["citations"], [])
        fields = {
            "意图": "分步解题",
            "当前步骤": cur["ask"] if cur else "全部步骤已完成",
            "第几步": f"{min(s['cursor'] + 1, s['n_steps'])}/{s['n_steps']}",
            "判定反馈": feedback,
            "降级层级": ESCALATION_LABELS[level],
            "硬约束": self._constraint(level),
        }
        narrative, degraded = self.express(fields, max_tokens=400,
                                           system=self.turn_prompt)
        n_ev = state_repo.count_events(student_id=s["student_id"])
        v = SolveView(
            session_id=session_id, student_id=s["student_id"], problem=s["problem"],
            status=s["status"], cursor=s["cursor"], n_steps=s["n_steps"],
            attempts=s["attempts"], escalation_level=level,
            escalation_label=ESCALATION_LABELS[level], steps=steps, citations=cites,
            narrative=narrative, degraded=degraded,
            confidence=min(0.9, cites[0]["score"]) if cites else 0.0,
            evidence_count=len(cites),
            gives_answer=level >= 3,
        )
        v.caveat = "" if cites else "未检索到教材依据，解题骨架可靠性受限"
        _ = n_ev
        return v

    # ---------- 内部 ----------
    @staticmethod
    def _constraint(level: int) -> str:
        return {
            0: "只复述当前这一步要做什么，绝不给出这一步的结果",
            1: "可给一个关键提示，仍不得给出这一步的结果",
            2: "可提示这一步用到的方法名称与代入顺序，不得代替学生完成计算",
            3: "可给出这一步的结论，并说明该知识点已标记需教师介入",
        }[max(0, min(3, level))]

    @staticmethod
    def _check(step: dict, text: str) -> grader.GradeResult:
        """这一步过没过——确定性判定，判不了就说判不了。"""
        if step["check_kind"] == "numeric":
            return grader.grade_numeric(text, step["expected"], tolerance=0.01)
        kws = expected_keywords(step["expected"])
        if not kws:
            return grader.GradeResult(graded_by="pending_teacher", detail="该步无法自动判定")
        return grader.grade_keyword(text, kws)

    @staticmethod
    def _infer_kps(ctx, kp_id: int | None) -> list[int]:
        """从检索命中的材料反推涉及哪些知识点。确定性：只认材料上标注的 kp_codes。"""
        codes: list[str] = []
        for hit in ctx.hits:
            for c in hit.get("kp_codes") or []:
                if c not in codes:
                    codes.append(c)
        ids = []
        if kp_id:
            ids.append(kp_id)
        for c in codes:
            kp = graph_repo.get_kp_by_code(c)
            if kp and kp.id not in ids:
                ids.append(kp.id)
        return ids[:5]

    @staticmethod
    def _step_kp(hint: str, kp_ids: list[int]) -> int | None:
        """把模型给的知识点提示对到图谱上。对不上就留空——不硬凑。"""
        h = (hint or "").strip()
        if h:
            kp = graph_repo.get_kp_by_code(h)
            if kp:
                return kp.id
            for i in kp_ids:
                k = graph_repo.get_kp(i)
                if k and (k.name in h or h in k.name):
                    return i
        return kp_ids[0] if kp_ids else None

    def sessions(self, student_id: int, limit: int = 10) -> list[dict]:
        return get_db().query(
            "SELECT id, problem, status, cursor, n_steps, escalation_level, created_at"
            " FROM solve_session WHERE student_id=? ORDER BY id DESC LIMIT ?",
            (student_id, limit),
        )


__all__ = ["SolveAgent", "SolveView", "StepView", "parse_steps", "expected_keywords",
           "AgentOutput"]
