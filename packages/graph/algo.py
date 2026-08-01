"""图算法：环检测、拓扑排序、根因回溯、挡路度。

注意范式转变（CLAUDE.md §3.1）：拓扑序**只用于计算缺口内部的先后**，
不得用于给全员排课。本模块不提供"下一课"式接口。
"""
from __future__ import annotations

from collections import deque

from . import repo


def detect_cycles() -> list[list[int]]:
    """返回依赖图中的环（导入时必须为空）。"""
    pre, post = repo.adjacency()
    color: dict[int, int] = {n: 0 for n in post}
    stack: list[int] = []
    cycles: list[list[int]] = []

    def dfs(n: int) -> None:
        color[n] = 1
        stack.append(n)
        for m in post.get(n, []):
            if color.get(m, 0) == 0:
                dfs(m)
            elif color.get(m) == 1:
                idx = stack.index(m)
                cycles.append(stack[idx:] + [m])
        stack.pop()
        color[n] = 2

    for n in list(color):
        if color[n] == 0:
            dfs(n)
    return cycles


def topological_sort(kp_ids: list[int]) -> list[int]:
    """对给定子集做拓扑排序（子集内部的先后）。环内节点按 id 兜底排序。"""
    sub = set(kp_ids)
    pre, _ = repo.adjacency()
    indeg = {n: len([p for p in pre.get(n, []) if p in sub]) for n in sub}
    q = deque(sorted([n for n, d in indeg.items() if d == 0]))
    out: list[int] = []
    while q:
        n = q.popleft()
        out.append(n)
        for m in sub:
            if n in pre.get(m, []):
                indeg[m] -= 1
                if indeg[m] == 0:
                    q.append(m)
    if len(out) < len(sub):  # 有环，剩余按 id 追加，保证函数总可用
        out += sorted(sub - set(out))
    return out


def ancestors(kp_id: int, max_depth: int = 6) -> list[tuple[int, int]]:
    """向上（前置方向）BFS，返回 (kp_id, 深度)。"""
    pre, _ = repo.adjacency()
    seen = {kp_id}
    out: list[tuple[int, int]] = []
    q = deque([(kp_id, 0)])
    while q:
        n, d = q.popleft()
        if d >= max_depth:
            continue
        for p in pre.get(n, []):
            if p not in seen:
                seen.add(p)
                out.append((p, d + 1))
                q.append((p, d + 1))
    return out


def descendants(kp_id: int, max_depth: int = 6) -> set[int]:
    _, post = repo.adjacency()
    seen = {kp_id}
    q = deque([(kp_id, 0)])
    while q:
        n, d = q.popleft()
        if d >= max_depth:
            continue
        for m in post.get(n, []):
            if m not in seen:
                seen.add(m)
                q.append((m, d + 1))
    seen.discard(kp_id)
    return seen


def trace_root_cause(
    kp_id: int, mastery: dict[int, float], threshold: float, max_depth: int = 6
) -> dict:
    """根因回溯：沿前置边反向 BFS，找最深的仍未掌握的祖先。

    例：反向传播出错 → 回溯到链式法则。
    纯大模型方案做不可靠的正是这一步，必须依赖 L1 的显式依赖结构。
    """
    pre, _ = repo.adjacency()
    best = (kp_id, 0)
    path_parent: dict[int, int] = {}
    seen = {kp_id}
    q = deque([(kp_id, 0)])
    while q:
        n, d = q.popleft()
        if d >= max_depth:
            continue
        for p in pre.get(n, []):
            if p in seen:
                continue
            seen.add(p)
            # 只沿"同样未掌握"的链条继续回溯；已掌握的祖先说明链条到此为止
            if mastery.get(p, 0.0) < threshold:
                path_parent[p] = n
                if d + 1 > best[1]:
                    best = (p, d + 1)
                q.append((p, d + 1))
    root, depth = best
    # 还原回溯路径 root -> ... -> kp_id
    path = [root]
    cur = root
    while cur in path_parent:
        cur = path_parent[cur]
        path.append(cur)
    return {"root_kp_id": root, "depth": depth, "path": path, "is_self": root == kp_id}


def blocking_severity(kp_id: int, pending_task_ids: set[int] | None = None) -> float:
    """挡路度 = 该知识点被多少个待办任务依赖 + 它阻塞多少后续知识点。

    两项都归一化后加权求和，权重固定写在此处（任务优先于知识连锁）。
    """
    tasks = set(repo.tasks_requiring(kp_id))
    if pending_task_ids is not None:
        tasks &= pending_task_ids
    downstream = descendants(kp_id)
    return round(2.0 * len(tasks) + 0.5 * len(downstream), 3)


def nearest_mastered_prerequisite(
    kp_id: int, mastery: dict[int, float], threshold: float
) -> int | None:
    """追问起点：从学生已掌握的最近前置点切入。找不到则返回 None（从头讲起）。"""
    pre, _ = repo.adjacency()
    q = deque([(kp_id, 0)])
    seen = {kp_id}
    best: tuple[int, int, float] | None = None
    while q:
        n, d = q.popleft()
        if d > 4:
            continue
        for p in pre.get(n, []):
            if p in seen:
                continue
            seen.add(p)
            m = mastery.get(p, 0.0)
            if m >= threshold:
                if best is None or d + 1 < best[1] or (d + 1 == best[1] and m > best[2]):
                    best = (p, d + 1, m)
            else:
                q.append((p, d + 1))
    return best[0] if best else None


def depth_map(course_id: int | None = None) -> dict[int, int]:
    """每个知识点的依赖深度 = 从任一无前置节点出发到它的最长路径。

    只用于**可视化分层**（3D 图谱的 Y 轴：前置在下、后继在上），
    不用于安排教学顺序——那正是本项目已经废弃的做法。
    """
    pre, _post = repo.adjacency(course_id)
    depth: dict[int, int] = {}

    def solve(n: int, seen: set[int]) -> int:
        if n in depth:
            return depth[n]
        if n in seen:      # 防御性：图谱应为 DAG，成环时不再递归
            return 0
        seen.add(n)
        parents = pre.get(n, [])
        d = 0 if not parents else 1 + max(solve(p, seen) for p in parents)
        seen.discard(n)
        depth[n] = d
        return d

    for n in pre:
        solve(n, set())
    return depth


def coverage_map(course_id: int, mastery: dict[int, float]) -> list[dict]:
    """知识覆盖图：在图谱上标注掌握深浅，供画像展示。"""
    out = []
    for kp in repo.list_kps(course_id):
        out.append(
            {
                "kp_id": kp.id,
                "code": kp.code,
                "name": kp.name,
                "unit": kp.unit,
                "mastery": round(mastery.get(kp.id, 0.0), 3),
            }
        )
    return out
