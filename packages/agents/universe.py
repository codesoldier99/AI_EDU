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


def build_universe(course_code: str, student_id: int | None = None) -> dict | None:
    course = repo.get_course(course_code)
    if not course:
        return None

    kps = repo.list_kps(course["id"])
    depth = algo.depth_map(course["id"])
    pre, post = repo.adjacency(course["id"])
    mods = repo.module_weight_map()

    mastery: dict[int, float] = {}
    quality: dict[int, dict] = {}
    if student_id:
        mastery = state_repo.mastery_vector(student_id)
        for q in verification.build(student_id).items:
            quality[q["kp_id"]] = q

    units: list[str] = []
    for k in kps:
        if k.unit and k.unit not in units:
            units.append(k.unit)

    nodes = []
    for k in kps:
        q = quality.get(k.id, {})
        nodes.append({
            "id": k.id, "code": k.code, "name": k.name, "unit": k.unit,
            "unit_idx": units.index(k.unit) if k.unit in units else 0,
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

    return {
        "course": {"code": course["code"], "name": course["name"]},
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
