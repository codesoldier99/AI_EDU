"""L1 图谱层数据模型。禁止在本层引用 llm / state。"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.models import Message


@dataclass
class KnowledgePoint(Message):
    id: int = 0
    course_id: int = 0
    code: str = ""
    name: str = ""
    description: str = ""
    granularity: str = "atomic"
    kp_type: str = "concept"
    difficulty: float = 0.5
    unit: str = ""


@dataclass
class Edge(Message):
    from_kp_id: int = 0
    to_kp_id: int = 0
    edge_type: str = "prereq"
    strength: float = 1.0


@dataclass
class AbilityModule(Message):
    id: int = 0
    code: str = ""
    name: str = ""
    description: str = ""


@dataclass
class ProjectTask(Message):
    id: int = 0
    project_id: int = 0
    code: str = ""
    name: str = ""
    parent_id: int | None = None
    milestone: int = 0
    seq: int = 0
    description: str = ""


@dataclass
class TaskKPLink(Message):
    task_id: int = 0
    kp_id: int = 0
    necessity: str = "required"
    annotated_by: str = "teacher"


@dataclass
class GraphStats(Message):
    courses: int = 0
    kps: int = 0
    edges: int = 0
    modules: int = 0
    tasks: int = 0
    task_kp_links: int = 0
    cycles: list = field(default_factory=list)
