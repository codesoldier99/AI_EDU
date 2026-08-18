"""模块二：苏格拉底式引导问答。

**不交给大模型自由发挥。** 策略引擎依据 L2 状态确定三件事：
    1. 起点——从已掌握的最近前置点切入
    2. 深度——掌握度越低，拆解越细
    3. 类型——概念澄清 / 反例质疑 / 类比迁移 / 条件变更
生成只负责把这三件事说成人话，且必须被 RAG 约束在教材内。

降级机制必须实现且必须留痕；降级是为了防止挫败感摧毁动机，
**不是为了让学生轻松**（CLAUDE.md §3.2）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.db import get_db
from packages.core.models import Message
from packages.core.timeutil import now_str
from packages.graph import algo as graph_algo
from packages.graph import repo as graph_repo
from packages.rag import retriever
from packages import skills
from packages.state import repo as state_repo
from packages.state import tracker
from packages.state import verification

from .base import Agent, AgentOutput

STYLES = ("概念澄清", "反例质疑", "类比迁移", "条件变更")
# 降级梯度：0 未降级；1 关键提示；2 解题框架；3 完整解析 + 标记教师介入
ESCALATION_LABELS = {0: "无", 1: "关键提示", 2: "解题框架", 3: "完整解析（已标记教师介入）"}


@dataclass
class AskingPlan(Message):
    target_kp_id: int = 0
    target_kp_name: str = ""
    entry_kp_id: int | None = None
    entry_kp_name: str = ""
    depth: int = 1
    style: str = "概念澄清"
    escalation_level: int = 0
    attempts: int = 0
    reason: str = ""


class AskingStrategy:
    """确定性的追问策略。可单测、可复现，不依赖任何模型。"""

    def decide(self, student_id: int, target_kp: int, attempts: int = 0,
               escalation_level: int = 0, history_styles: list[str] | None = None) -> AskingPlan:
        thr = CONFIG.teaching.mastery_threshold
        mastery = state_repo.mastery_vector(student_id)
        kp = graph_repo.get_kp(target_kp)
        p = mastery.get(target_kp, 0.0)

        entry = graph_algo.nearest_mastered_prerequisite(target_kp, mastery, thr)
        entry_kp = graph_repo.get_kp(entry) if entry else None

        # 深度：掌握度越低拆得越细（1~4 级）
        depth = 1 if p >= 0.6 else 2 if p >= 0.4 else 3 if p >= 0.2 else 4
        # 类型：先按知识点性质给一个默认次序，
        # 再用**该学生的实测有效率**重排——"顺着你的学法"不能只停留在难度自适应，
        # 必须记住"对他而言反例质疑管用、类比迁移不管用"。证据不足时不猜，用默认序。
        used = set(history_styles or [])
        if kp and kp.kp_type in ("method", "skill"):
            order = ["条件变更", "反例质疑", "类比迁移", "概念澄清"]
        else:
            order = ["概念澄清", "反例质疑", "类比迁移", "条件变更"]
        prefer = [s for s in verification.preferred_styles(student_id) if s in STYLES]
        if prefer:
            order = prefer + [s for s in order if s not in prefer]
        # 仍然轮换：同一套问法连着无效时要换，不能锁死在历史最优上
        style = next((s for s in order if s not in used), order[0])

        return AskingPlan(
            target_kp_id=target_kp,
            target_kp_name=kp.name if kp else "",
            entry_kp_id=entry,
            entry_kp_name=entry_kp.name if entry_kp else "",
            depth=depth,
            style=style,
            escalation_level=escalation_level,
            attempts=attempts,
            reason=(
                f"掌握度 {p:.2f}<{thr}，从已掌握的「{entry_kp.name}」切入"
                if entry_kp
                else f"掌握度 {p:.2f}，未找到已掌握的前置点，从最基础处问起"
            ) + (f"；按你的历史反馈优先用「{prefer[0]}」" if prefer else ""),
        )


class AskingAgent(Agent):
    name = "asking"
    system_prompt = (
        "你是苏格拉底式助教。硬约束：\n"
        "1) 未触发降级时**绝不给出最终答案**，只提出一个能推进思考的问题；\n"
        "2) 只使用给定的教材依据，不得引入教材之外的表述；\n"
        "3) 语气平实，不评价学生聪明与否，不催促。"
    )
    strategy = AskingStrategy()

    # ---------- 会话 ----------
    def start(self, student_id: int, kp_id: int) -> AgentOutput:
        db = get_db()
        now = now_str()
        open_s = db.query_one(
            "SELECT * FROM asking_session WHERE student_id=? AND kp_id=? AND status='open'"
            " ORDER BY id DESC",
            (student_id, kp_id),
        )
        if open_s:
            session_id = open_s["id"]
            attempts, level = open_s["attempts"], open_s["escalation_level"]
        else:
            session_id = db.execute(
                "INSERT INTO asking_session(student_id, kp_id, style, depth, attempts,"
                " escalation_level, status, created_at, updated_at)"
                " VALUES(?,?,?,?,0,0,'open',?,?)",
                (student_id, kp_id, "", 1, now, now),
            )
            attempts, level = 0, 0
        return self._turn(session_id, student_id, kp_id, attempts, level, student_text=None)

    def reply(self, session_id: int, student_text: str,
              self_reported_stuck: bool = False) -> AgentOutput:
        db = get_db()
        s = db.query_one("SELECT * FROM asking_session WHERE id=?", (session_id,))
        if not s:
            return AgentOutput(agent=self.name, narrative="会话不存在")
        db.execute(
            "INSERT INTO asking_turn(session_id, role, text, created_at) VALUES(?,?,?,?)",
            (session_id, "student", student_text, now_str()),
        )
        attempts = s["attempts"] + 1
        level = s["escalation_level"]

        progressed = self._detect_progress(student_text)
        frustrated = self._detect_frustration(session_id, student_text, self_reported_stuck)

        # 学生的作答本身是证据，按 ask 权重写入 L2（唯一通道：tracker）
        if progressed is not None:
            tracker.record(
                student_id=s["student_id"], event_type="ask_turn", kp_id=s["kp_id"],
                is_correct=progressed, source="ask", source_ref=f"session:{session_id}",
                payload={"text": student_text[:500]},
            )

        need_escalate = (
            attempts >= CONFIG.teaching.escalation_attempts * (level + 1) or frustrated
        ) and progressed is not True
        if need_escalate and level < 3:
            level += 1
            self._log_escalation(s["student_id"], s["kp_id"], session_id, level,
                                 "挫败信号" if frustrated else "多次尝试无进展")
        return self._turn(session_id, s["student_id"], s["kp_id"], attempts, level,
                          student_text=student_text)

    # ---------- 内部 ----------
    def _turn(self, session_id: int, student_id: int, kp_id: int, attempts: int,
              level: int, student_text: str | None) -> AgentOutput:
        db = get_db()
        styles = [
            r["text"].split("|")[0]
            for r in db.query(
                "SELECT text FROM asking_turn WHERE session_id=? AND role='system'",
                (session_id,),
            )
        ]
        plan = self.strategy.decide(student_id, kp_id, attempts, level, styles)
        kp = graph_repo.get_kp(kp_id)
        # RAG 约束：拿不到教材依据就不许自由发挥
        ctx = retriever.grounded_context(
            f"{kp.name if kp else ''} {kp.description if kp else ''} {student_text or ''}",
            intent="ask", kp_id=kp_id, top_k=2,
        )
        # 教学技能包：教师写一个 Markdown 就能改变"这一类问法怎么说"。
        # 注意方向——style 是 AskingStrategy 确定性选出来的，技能包只影响表达。
        pack = skills.pick_asking_style(plan.style, kp.kp_type if kp else "")
        fields = {
            "意图": "苏格拉底追问",
            "目标知识点": plan.target_kp_name,
            "切入点": plan.entry_kp_name,
            "追问方式": plan.style,
            "拆解层级": plan.depth,
            "教材依据": ctx.context_text(2)[:700],
            "学生上一句": (student_text or "")[:300],
            "降级层级": ESCALATION_LABELS[level],
        }
        if pack:
            fields["教学法要点"] = pack.guidance(400)
        if level == 0:
            fields["硬约束"] = "只能提问，不能给出最终答案"
        elif level == 1:
            fields["硬约束"] = "可给一个关键提示，仍不得给出完整答案"
        elif level == 2:
            fields["硬约束"] = "可给解题框架（步骤骨架），不得代替学生完成计算或推导"
        else:
            fields["硬约束"] = "可给完整解析，并说明该知识点已标记需教师介入"

        narrative, degraded = self.express(fields, max_tokens=500)
        db.execute(
            "INSERT INTO asking_turn(session_id, role, text, citations, created_at)"
            " VALUES(?,?,?,?,?)",
            (session_id, "system", f"{plan.style}|{narrative}",
             __import__("json").dumps([c.to_dict() for c in ctx.citations],
                                      ensure_ascii=False), now_str()),
        )
        db.execute(
            "UPDATE asking_session SET entry_kp_id=?, style=?, depth=?, attempts=?,"
            " escalation_level=?, status=?, updated_at=? WHERE id=?",
            (plan.entry_kp_id, plan.style, plan.depth, attempts, level,
             "closed" if level >= 3 else "open", now_str(), session_id),
        )
        return AgentOutput(
            agent=self.name,
            narrative=narrative,
            plan={**plan.to_dict(), "session_id": session_id,
                  "escalation_label": ESCALATION_LABELS[level],
                  "gives_answer": level >= 3,
                  # 溯源：这一轮用了哪个教学技能包，教师能查到具体文件
                  "skill_pack": pack.name if pack else "",
                  "skill_pack_sha": pack.sha256 if pack else ""},
            citations=[c.to_dict() for c in ctx.citations],
            confidence=0.0 if not ctx.hits else min(0.9, ctx.hits[0]["score"]),
            evidence_count=len(ctx.hits),
            degraded=degraded,
            caveat="" if ctx.hits else "未检索到教材依据，回答已受限",
        )

    def _log_escalation(self, student_id: int, kp_id: int, session_id: int, level: int,
                        reason: str) -> None:
        """每次降级写 LearningEvent(event_type='escalation')，诊断报告显示降级热点。"""
        tracker.record(
            student_id=student_id, event_type="escalation", kp_id=kp_id,
            is_correct=None, source="ask", source_ref=f"session:{session_id}",
            payload={"level": level, "reason": reason,
                     "label": ESCALATION_LABELS[level],
                     "needs_teacher": level >= 3},
        )

    @staticmethod
    def _detect_progress(text: str) -> bool | None:
        """判定学生这一轮是否有进展。

        刻意做成保守的规则而非模型判断——铁律 3：涉及成绩判定必须走确定性工具。
        返回 None 表示"无法判定"，此时不写任何掌握度证据。
        """
        t = (text or "").strip()
        if len(t) < 4:
            return None
        stuck_words = ("不会", "不知道", "没思路", "卡住", "看不懂", "不懂", "?", "？")
        if any(w in t for w in stuck_words) and len(t) < 30:
            return False
        # 出现推理连接词且有一定长度，视为一次实质推进
        reason_words = ("因为", "所以", "推导", "先", "然后", "如果", "假设", "代入", "得到")
        if len(t) >= 25 and sum(1 for w in reason_words if w in t) >= 2:
            return True
        return None

    @staticmethod
    def _detect_frustration(session_id: int, text: str, self_reported: bool) -> bool:
        if self_reported:
            return True
        rows = get_db().query(
            "SELECT text FROM asking_turn WHERE session_id=? AND role='student'"
            " ORDER BY id DESC LIMIT 3",
            (session_id,),
        )
        stuck = sum(
            1 for r in rows
            if any(w in r["text"] for w in ("不会", "不知道", "没思路", "卡住", "不懂"))
        )
        return stuck >= CONFIG.teaching.frustration_repeats

    # ---------- 查询 ----------
    def session_view(self, session_id: int) -> dict:
        db = get_db()
        s = db.query_one("SELECT * FROM asking_session WHERE id=?", (session_id,))
        turns = db.query(
            "SELECT role, text, citations, created_at FROM asking_turn WHERE session_id=?"
            " ORDER BY id",
            (session_id,),
        )
        for t in turns:
            if t["role"] == "system" and "|" in t["text"]:
                t["style"], t["text"] = t["text"].split("|", 1)
        return {"session": dict(s) if s else None, "turns": turns}
