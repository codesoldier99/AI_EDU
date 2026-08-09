"""L3 生成层：教学资产的结构化模型（教学大纲 / 授课计划 / 课件）。

铁律 3：这些 dataclass 装的是"确定性 plan()"的产物——章节骨架、知识点引用、
渲染元数据。大模型只负责把已经定好边界的字段表达成通顺的文案（Agent.express()），
不负责决定"有哪些章节""覆盖哪些知识点"——那些字段在 express() 调用之前就已经
由 packages.graph / packages.state 的确定性查询算出来了。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.models import Message


@dataclass
class ChapterPlan(Message):
    """教学大纲里的一章，对应 knowledge_point.unit 分组。"""

    unit: str = ""
    seq: int = 0
    kp_codes: list = field(default_factory=list)
    kp_names: list = field(default_factory=list)
    narrative: str = ""      # 章节说明文字，express() 填充；未填充时为空，前端按需生成


@dataclass
class SyllabusPlan(Message):
    course_id: int = 0
    course_code: str = ""
    course_name: str = ""
    chapters: list = field(default_factory=list)      # list[dict]，来自 ChapterPlan.to_dict()
    total_kps: int = 0
    generator_version: str = "syllabus_v1"

    def kp_coverage(self) -> list[str]:
        out: list[str] = []
        for c in self.chapters:
            out += c.get("kp_codes", [])
        return out


@dataclass
class SessionPlan(Message):
    """授课计划里的一次课。"""

    seq: int = 0
    title: str = ""
    unit: str = ""
    kp_codes: list = field(default_factory=list)
    kp_names: list = field(default_factory=list)
    duration_min: int = 90
    narrative: str = ""


@dataclass
class TeachingPlanResult(Message):
    syllabus_id: int = 0
    course_code: str = ""
    items: list = field(default_factory=list)         # list[dict]，来自 SessionPlan.to_dict()
    generator_version: str = "teaching_plan_v1"


@dataclass
class SlideChart(Message):
    """图表数据必须来自确定性计算结果（如 class_mastery），不得是模型编造的数字。"""

    chart_type: str = "bar"          # bar | table
    title: str = ""
    categories: list = field(default_factory=list)
    series: list = field(default_factory=list)         # [{"name":..., "values":[...]}]
    source: str = ""                 # 数据来源标注，如 "class_mastery"


@dataclass
class SlidePlan(Message):
    layout: str = "bullets"          # title | bullets | chart
    title: str = ""
    subtitle: str = ""
    bullets: list = field(default_factory=list)
    chart: dict | None = None        # SlideChart.to_dict()，仅 layout=chart 时有值
    kp_codes: list = field(default_factory=list)       # 本页覆盖的知识点，用于覆盖率校验
    citations: list = field(default_factory=list)


@dataclass
class DeckPlan(Message):
    teaching_plan_id: int = 0
    course_code: str = ""
    title: str = ""
    slides: list = field(default_factory=list)         # list[dict]，来自 SlidePlan.to_dict()
    generator_version: str = "deck_v1"

    def kp_coverage(self) -> list[str]:
        seen: set[str] = set()
        for s in self.slides:
            seen |= set(s.get("kp_codes", []))
        return sorted(seen)


@dataclass
class RenderResult(Message):
    file_path: str = ""
    artifact_type: str = "pptx"      # pptx | markdown
    render_tool: str = ""            # officecli | builtin_stdlib
    render_tool_version: str = ""
    degraded: bool = False
    error: str = ""
