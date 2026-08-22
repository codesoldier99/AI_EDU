"""项目数据适配层。

五类标准信号是模块三、四、五的共同数据基础。新项目接入 = 实现本文件的
ProjectAdapter，**不改核心代码**；接第 5 个和第 10 个适配器的成本应当相同。
（tests/test_adapters.py 用"核心代码零改动"来检验这一点）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from packages.core.config import CONFIG
from packages.core.db import get_db
from packages.core.timeutil import now_str

SIGNAL_CLASSES = (
    "code_commit",     # 提交行为：频次、粒度、变更量、评审记录
    "build_test",      # 构建质量：CI 成功率、测试覆盖、缺陷修复周期
    "runtime",         # 运行结果：任务成功率、关键指标、异常重试、调参轨迹
    "doc_delivery",    # 文档交付：设计文档、技术报告、答辩材料完整性
    "collaboration",   # 协作记录：看板流转、评审互动、接口沟通
)


@dataclass
class ProjectSignal:
    project_code: str = ""
    student_sid: str = ""
    signal_class: str = ""
    metric: str = ""
    value: float = 0.0
    occurred_at: str = ""
    raw_ref: str = ""

    def validate(self) -> None:
        if self.signal_class not in SIGNAL_CLASSES:
            raise ValueError(f"非法信号类别：{self.signal_class}（必须归一化为五类之一）")
        if not self.student_sid or not self.project_code:
            raise ValueError("信号必须能定位到学生与项目")


@runtime_checkable
class ProjectAdapter(Protocol):
    """所有项目适配器的统一契约。只需实现 collect。"""

    project_type: str
    adapter_key: str

    def collect(self, since: datetime | str | None = None) -> list[ProjectSignal]: ...


def persist_signals(signals: list[ProjectSignal]) -> int:
    """把归一化信号落库。核心代码对项目类型一无所知——这正是目的。"""
    db, n = get_db(), 0
    for s in signals:
        s.validate()
        proj = db.query_one("SELECT id FROM project WHERE code=?", (s.project_code,))
        stu = db.query_one("SELECT id FROM student WHERE sid=?", (s.student_sid,))
        if not proj or not stu:
            continue
        # 采集必须幂等：同一个 commit 被采两次只能算一条。
        # 否则运维多跑一次 make signals，学生的"投入"就凭空翻倍，
        # 而报表上只会显示他很勤奋（migrations/007 把这条固化成唯一索引）。
        # 这里走的正是那条索引，是一次索引查找，不是全表扫描。
        at = s.occurred_at or now_str()
        key = (proj["id"], stu["id"], s.signal_class, s.metric, s.raw_ref, at)
        if db.query_one(
            "SELECT 1 FROM project_signal WHERE project_id=? AND student_id=?"
            " AND signal_class=? AND metric=? AND raw_ref=? AND occurred_at=?", key
        ):
            continue
        db.execute(
            "INSERT INTO project_signal(project_id, student_id, signal_class,"
            " metric, value, occurred_at, raw_ref) VALUES(?,?,?,?,?,?,?)",
            (proj["id"], stu["id"], s.signal_class, s.metric, s.value, at, s.raw_ref),
        )
        n += 1
    return n


def signal_to_ability(signal_class: str) -> dict[str, float]:
    """五类信号 → M1–M8 权重矩阵。写在配置里，不硬编码。"""
    return CONFIG.teaching.signal_weights.get(signal_class, {})
