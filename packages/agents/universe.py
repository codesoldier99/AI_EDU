"""知识宇宙的视图投影：把 L1 图谱与 L2 状态拼成 3D 视图要的一份数据。

它放在 L3 是因为**它读 L1 与 L2、但什么都不写**——是一次纯投影，不是新的事实来源。
放在 graph 层不行（那样 L1 会依赖 L2，方向反了），放在路由层也不行
（apps/api 只做路由与鉴权，tests/test_layering.py 会拦）。

这个模块里没有一句大模型调用：知识宇宙里看到的每一个位置、每一种颜色、
每一条被点亮的根因链，都是算出来的。
"""
from __future__ import annotations

from packages.core.config import CONFIG
from packages.graph import algo, repo
from packages.state import repo as state_repo
from packages.state import verification


# 跨课程全图的伪课程代码。单课程视图会过滤掉两端不在同一门课里的依赖边，
# 而全图里有 70/547 条边正是跨课程的——包括演示时最常讲的那一条
# 「链式法则（高等数学B）→ 反向传播（深度学习）」。归属移交之后，
# 这条链在任何单课程视图里都看不见了，于是"根因不在你以为的那门课里"
# 这句话反而没法当场演示。全图模式就是为它准备的。
ALL_COURSES = "ALL"
_ALL_ALIASES = {ALL_COURSES, "*"}


def build_universe(course_code: str, student_id: int | None = None) -> dict | None:
    # 真实课程优先：万一哪天真有一门课的代码叫 ALL，它仍然按课程解析，
    # 不会被这个哨兵值抢走。
    course = repo.get_course(course_code)
    cross = course is None and course_code in _ALL_ALIASES
    if cross:
        course = {"code": ALL_COURSES, "name": "全部课程（跨课程视图）"}
    elif not course:
        return None

    course_id = None if cross else course["id"]
    kps = repo.list_kps(course_id)
    depth = algo.depth_map(course_id)
    pre, post = repo.adjacency(course_id)
    mods = repo.module_weight_map()
    # 全图模式下按课程归组：知识点归属唯一，所以一门课就是一个星系。
    # 这比按章节分组更贴合这套系统要讲的事——一个项目会同时点亮十几个星系。
    course_of = {c["id"]: c for c in repo.list_courses()} if cross else {}

    mastery: dict[int, float] = {}
    quality: dict[int, dict] = {}
    if student_id:
        mastery = state_repo.mastery_vector(student_id)
        for q in verification.build(student_id).items:
            quality[q["kp_id"]] = q

    def group_of(k) -> str:
        if cross:
            c = course_of.get(k.course_id)
            return c["name"] if c else "未归属课程"
        return k.unit or ""

    units: list[str] = []
    for k in kps:
        g = group_of(k)
        if g and g not in units:
            units.append(g)

    nodes = []
    for k in kps:
        q = quality.get(k.id, {})
        g = group_of(k)
        c = course_of.get(k.course_id) if cross else course
        nodes.append({
            "id": k.id, "code": k.code, "name": k.name, "unit": g,
            "chapter": k.unit,
            "course_code": (c or {}).get("code", ""),
            "course_name": (c or {}).get("name", ""),
            "unit_idx": units.index(g) if g in units else 0,
            "type": k.kp_type, "difficulty": k.difficulty,
            "depth": depth.get(k.id, 0),
            "n_pre": len(pre.get(k.id, [])), "n_post": len(post.get(k.id, [])),
            "severity": algo.blocking_severity(k.id),
            "modules": sorted(mods.get(k.id, {})),
            "tasks": len(repo.tasks_requiring(k.id)),
            # 没有作答记录时给 None（前端显示中性灰）——"没数据"与"掌握度 0"是两回事
            "mastery": (round(mastery[k.id], 4)
                        if student_id and k.id in mastery else None),
            "retained": q.get("retained"),
            "validated": q.get("validated"),
            "due": q.get("due"),
        })

    edges = [[e.from_kp_id, e.to_kp_id] for e in repo.list_edges()
             if e.from_kp_id in depth and e.to_kp_id in depth]

    cross_edges = 0
    if cross:
        course_by_kp = {k.id: k.course_id for k in kps}
        cross_edges = sum(1 for a, b in edges
                          if course_by_kp.get(a) != course_by_kp.get(b))

    return {
        "course": {"code": course["code"], "name": course["name"]},
        "cross_course": cross,
        "cross_edges": cross_edges,
        "units": units,
        "nodes": nodes,
        "edges": edges,
        "max_depth": max(depth.values()) if depth else 0,
        "threshold": CONFIG.teaching.mastery_threshold,
        "student_id": student_id,
    }


def root_cause_path(student_id: int, kp_id: int) -> dict:
    """给定薄弱点，返回根因回溯路径——3D 视图里用来点亮那条链。

    与班级诊断走的是同一个算法（graph.algo.trace_root_cause），
    因此图上点亮的那条链，和诊断报告里写的那句话，一定是同一个结论。
    """
    mastery = state_repo.mastery_vector(student_id)
    t = algo.trace_root_cause(kp_id, mastery, CONFIG.teaching.mastery_threshold)
    root = repo.get_kp(t["root_kp_id"])
    return {
        "kp_id": kp_id,
        "root_kp_id": t["root_kp_id"],
        "root_name": root.name if root else "",
        "is_self": t["is_self"],
        "path": t["path"],
        "depth": t["depth"],
    }
