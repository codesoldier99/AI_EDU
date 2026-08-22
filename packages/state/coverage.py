"""培养方案覆盖度：一个项目铺开到哪些课，一个学生能认定哪些学分。

## 这个模块回答两个问题

1. **项目视角**：`program_map()` —— DAC 项目铺到培养方案的哪几门课上？
   每门课有多少知识点已建、被项目拉了多少、还欠多少。
   这张表是"该建哪门课"的依据，也是汇报时最能说明问题的一页。

2. **学生视角**：`program_coverage()` —— 这个学生在每门课上的覆盖度是多少，
   够不够认定学分。规则来自 `data/seed/kb/program_outline.md` §4：
   知识点覆盖不足 25% 不予认定，且结论必须可追溯到具体知识点节点。

## 三条不能松的规矩

**一、知识点归属唯一。** 覆盖度只算这门课**拥有**的知识点。
"向量与矩阵运算"归线性代数A，机器学习只是依赖它——不能拿它去认定机器学习的学分。
这条一松，同一个知识点会在三门课里各算一次，学分就重复计了。

**二、只认已验证掌握。** 覆盖度的分子用 `verification` 的"已验证掌握"
（跨时间再次做对），不是当堂做对。学分是要落到档案里的东西，
"一次做对"这种证据强度不够——理由见 CLAUDE.md 铁律 6。

**三、这是派生视图，不落表。** 全部从事件流与图谱现算。
凡是能算出来的就不要落第二份真相，否则总有一天它会和事件流对不上
（同 `verification.py`、`engagement`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.models import Message
from packages.graph import algo as graph_algo
from packages.graph import repo as graph_repo

from . import repo as state_repo

# 学分认定门槛来自培养方案（program_outline.md §4），值在 config 里可调
def _thresholds() -> tuple[float, int]:
    from packages.core.config import CONFIG

    t = CONFIG.teaching
    return t.credit_min_coverage, t.credit_min_kps


# 兼容旧引用；真正生效的是 _thresholds()
CREDIT_MIN_COVERAGE = 0.25


@dataclass
class CourseCoverage(Message):
    course_code: str = ""
    course_name: str = ""
    module: str = ""
    semester: int = 0
    credit: float = 0.0
    kps_built: int = 0          # 这门课已建的知识点数（0 = 还没被任何项目拉取过）
    pulled_direct: int = 0      # 被项目任务直接标注的
    pulled_via_prereq: int = 0  # 沿依赖边间接需要的
    demanded: int = 0           # 还欠着没建的（悬空需求）
    mastered: int = 0
    validated: int = 0
    coverage: float = 0.0       # 已验证掌握 / 已建知识点
    credit_eligible: bool = False
    via_projects: list = field(default_factory=list)  # 经哪些项目折算（集中实践环节）
    caveat: str = ""


def _project_pull(project_code: str) -> tuple[set[int], set[int]]:
    """项目直接标注的知识点，以及沿依赖边间接需要的知识点。

    间接的那部分是拉取式的关键：任务只标了"训练基线分类模型"，
    但要做这件事，它的全部前置也都得会——那些前置往往落在别的课上。
    """
    proj = graph_repo.get_project(project_code)
    if not proj:
        return set(), set()
    direct: set[int] = set()
    for t in graph_repo.list_tasks(proj["id"]):
        for r in graph_repo.required_kps(t.id, include_helpful=True):
            direct.add(r["kp_id"])
    indirect: set[int] = set()
    for kp_id in direct:
        for anc, _d in graph_algo.ancestors(kp_id, max_depth=12):
            if anc not in direct:
                indirect.add(anc)
    return direct, indirect


def program_map(program_code: str, project_code: str | None = None) -> dict:
    """培养方案全景：项目铺到了哪几门课，每门课建了多少、还欠多少。

    大量课程 kps_built=0 是**正常状态**，不是数据缺失——
    图谱按项目拉取建设，没被拉过的课先不建。见 program_ai_zsb.yaml 顶部说明。
    """
    courses = graph_repo.program_courses(program_code)
    direct, indirect = _project_pull(project_code) if project_code else (set(), set())

    # 悬空需求按课程汇总：这门课欠了几个还没建的知识点
    demand = {r["course_code"]: r["kps"] for r in graph_repo.demand_by_course()}

    kp_course: dict[int, int] = {}
    for kp in graph_repo.list_kps():
        kp_course[kp.id] = kp.course_id

    plan_ids = {c["course_id"] for c in courses}
    # 项目专有知识：不属于培养方案任何一门理论课。
    # 它们不该被硬塞进某门理论课凑覆盖度，但经**集中实践环节绑定**可以折算学分。
    project_only_ids = {k for k in (direct | indirect) if kp_course.get(k) not in plan_ids}

    rows: list[dict] = []
    for c in courses:
        cid = c["course_id"]
        bound = graph_repo.practice_projects_of(cid)
        # 实践课程承接被绑定项目的项目专有知识，计入它的"已建"
        via = len(project_only_ids) if (bound and project_code in bound) else 0
        rows.append({
            "course_code": c["code"], "course_name": c["name"], "module": c["module"],
            "semester": c["semester"], "credit": c["credit"],
            "kps_built": c["kps"] + via,
            "pulled_direct": sum(1 for k in direct if kp_course.get(k) == cid),
            "pulled_via_prereq": sum(1 for k in indirect if kp_course.get(k) == cid),
            "demanded": demand.get(c["code"], 0),
            "via_practice": via,
        })
    for r in rows:
        r["touched"] = bool(r["pulled_direct"] or r["pulled_via_prereq"]
                            or r["demanded"] or r["via_practice"])

    project_only = len(project_only_ids)
    practice_course = graph_repo.practice_course_of(project_code) if project_code else None

    return {
        "program": program_code,
        "project": project_code,
        "courses": rows,
        "n_courses": len(rows),
        "n_touched": sum(1 for r in rows if r["touched"]),
        "n_built": sum(1 for r in rows if r["kps_built"]),
        "n_empty_but_needed": sum(1 for r in rows if r["touched"] and not r["kps_built"]),
        "pulled_total": len(direct) + len(indirect),
        "project_only_kps": project_only,
        "practice_course": practice_course["code"] if practice_course else "",
        "practice_course_name": practice_course["name"] if practice_course else "",
        "demand_open": sum(demand.values()),
    }


def program_coverage(program_code: str, student_id: int) -> list[CourseCoverage]:
    """学生在培养方案每门课上的覆盖度与学分认定资格。

    分子只算**已验证掌握**（跨时间再次做对）。当堂做对不算——
    学分要进档案，证据强度不够不能认。
    """
    from . import verification

    view = verification.build(student_id)
    # verification.build() 把 items 存成 dict（见其 to_dict() 调用），不是对象
    validated_ids = {q["kp_id"] for q in view.items if q.get("validated")}
    mastery = state_repo.mastery_vector(student_id)
    from packages.core.config import CONFIG

    thr = CONFIG.teaching.mastery_threshold
    min_cov, min_kps = _thresholds()

    out: list[CourseCoverage] = []
    for c in graph_repo.program_courses(program_code):
        kps = graph_repo.list_kps(c["course_id"])
        ids = {k.id for k in kps}

        # 集中实践环节：承接被绑定项目的**项目专有知识**。
        # 那些知识点（DMD 结构光、这台设备的触发时序）不属于任何理论课，
        # 但学生确实做了，学分该落在实践环节上——理由见 program_ai_zsb.yaml。
        bound = graph_repo.practice_projects_of(c["course_id"])
        via_project: list[str] = []
        for proj in bound:
            extra = set(graph_repo.project_domain_kp_ids(proj))
            if extra - ids:
                via_project.append(proj)
            ids |= extra
        built = len(ids)
        mastered = sum(1 for i in ids if mastery.get(i, 0.0) >= thr)
        validated = len(ids & validated_ids)
        cov = round(validated / built, 3) if built else 0.0
        cc = CourseCoverage(
            course_code=c["code"], course_name=c["name"], module=c["module"],
            semester=c["semester"], credit=c["credit"], kps_built=built,
            mastered=mastered, validated=validated, coverage=cov,
            via_projects=via_project,
        )
        # 认定资格有两道闸，缺一不可：
        #   ① 图谱建够了吗——只建了 3 个点时说"覆盖 33%"是假精度，
        #      那是"判不了"，不是"判不合格"（铁律 7）。给个 ✓ 比不给更糟。
        #   ② 覆盖度够不够——培养方案 §4 的 25% 门槛。
        if not built:
            cc.caveat = "该课程图谱尚未建设，判不了"
        elif built < min_kps:
            cc.caveat = (f"图谱仅建了 {built} 个知识点（认定需 ≥{min_kps}），"
                         f"覆盖度不构成结论，判不了")
        else:
            cc.credit_eligible = cov >= min_cov
            if not cc.credit_eligible:
                cc.caveat = f"覆盖度未达 {min_cov:.0%}"
        if via_project:
            # 实践环节的学分不能只看知识点覆盖度：里程碑验收是必须的。
            # 系统给出覆盖度这一项事实，签字仍然是教师的事——不替教师下结论。
            ms = sum(graph_repo.project_milestones(pj)["milestones"] for pj in via_project)
            cc.caveat = (f"经项目 {'、'.join(via_project)} 折算；"
                         f"另需 {ms} 个里程碑通过导师验收，系统不代签" +
                         (f"；{cc.caveat}" if cc.caveat else ""))
        out.append(cc)
    return out


@dataclass
class DemandItem(Message):
    code: str = ""
    course_code: str = ""
    course_name: str = ""
    demands: int = 0
    projects: list = field(default_factory=list)
    demanded_by: list = field(default_factory=list)


def build_queue(project_code: str | None = None, limit: int = 50) -> list[DemandItem]:
    """悬空需求队列：下一门课该先建哪几个知识点。

    按被需求次数排序。被 3 个项目拉过的知识点，比没人拉的 300 个更该先建——
    这就是"图谱建设本身也拉取式"的具体形态。
    """
    out: list[DemandItem] = []
    for r in graph_repo.demand_queue(project_code)[:limit]:
        c = graph_repo.get_course(r["course_code"]) if r["course_code"] else None
        out.append(DemandItem(
            code=r["code"], course_code=r["course_code"],
            course_name=c["name"] if c else "（未归类：代码前缀不认识）",
            demands=r["demands"],
            projects=[x for x in (r["projects"] or "").split(",") if x],
            demanded_by=[x for x in (r["by_whom"] or "").split(",") if x],
        ))
    return out
