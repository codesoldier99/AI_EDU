"""L3 智能体基类与编排约定。

铁律 1：生成层只能读取学生状态、写入行为事件；掌握度变更必须经 packages.state.tracker。
铁律 3：凡涉及数值计算、代码正确性、成绩判定，必须走确定性工具，模型只做解释与表达。

因此每个智能体的实现被拆成两段：
    plan()    —— 确定性计算，产出结构化 Plan（可单测、可复现、可被教师质疑）
    express() —— 交给大模型把 Plan 说成人话（可替换、可降级、不改变结论）
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.models import Message
from packages.llm import gateway


@dataclass
class AgentOutput(Message):
    agent: str = ""
    narrative: str = ""
    plan: dict = field(default_factory=dict)
    citations: list = field(default_factory=list)
    confidence: float = 0.0
    evidence_count: int = 0
    degraded: bool = False
    caveat: str = ""


class Agent:
    name = "agent"
    system_prompt = "你是教学助理，只依据给定的结构化字段组织语言，禁止引入新的事实判断。"

    @staticmethod
    def _flatten(v) -> str:
        """字段值压成一行。

        `- 键：值` 是本系统与生成层之间唯一的接口格式；一个字段占一行，
        解析才是无歧义的（离线表达器与真实模型看到的是同一份结构）。
        """
        return " ".join(str(v).split())

    def express(self, fields: dict, max_tokens: int = 600) -> tuple[str, bool]:
        """把结构化字段交给模型表达。字段格式固定为 `- 键：值`，离线表达器也能解析。"""
        body = "\n".join(
            f"- {k}：{self._flatten(v)}" for k, v in fields.items() if v not in (None, "")
        )
        r = gateway.complete(self.name, self.system_prompt, body, max_tokens=max_tokens)
        return r.text, r.degraded

    @staticmethod
    def caveat_for(evidence_count: int) -> str:
        return (
            ""
            if evidence_count >= CONFIG.teaching.min_evidence_for_report
            else "数据不足，仅供参考"
        )
