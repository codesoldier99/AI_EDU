"""求职智能体：把"两年内积累的百万级信息点"折叠成用人单位看得懂的三层结构。

## 它要回答的问题

传统推荐表只给 HR 看 100 个数据点（含课程成绩）。这个学生真正做过的事——
提交过的项目操作、掌握到什么程度的知识点、被追问卡住又是怎么想通的——
理论上有上百万个信息点，但没人会把上百万个点直接甩给 HR 看。

所以按 CLAUDE.md §3.1 同一条道理处理："拉取式"不止用在教学上，也用在这里：
    第一层  岗位需求（job_posting）—— 一个岗位要什么
    第二层  分解的岗位需求图谱（job_requirement 树）—— 拆成可判定的能力维度
    第三层  知识点与项目证据（knowledge_point / project_signal）—— 每个维度背后
            具体是哪些知识点、掌握到什么程度、由哪些项目任务佐证

三层都是**只读投影**：本模块不写 mastery_state，不写 project_signal，
岗位需求树本身是教师/企业导师维护的配置数据（见 packages/graph/repo.py 的注释），
和 course / project_task 同级，不是学生事实。

## 分工（铁律 3）

    fit_report()   确定性：把 L1 岗位需求树 + L2 掌握质量 + 项目信号，折叠成带
                   confidence / evidence_count 的匹配度报告——这是唯一的判定入口
    resume()       大模型：把 fit_report() 的结构化字段写成一段面向 HR 的简历叙述，
                   不引入字段之外的任何"事实"（弱项也照实写，不能只报喜）

模型换厂商、离线降级，匹配度、证据条数、差距清单一个字都不会变。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.config import CONFIG
from packages.core.db import get_db
from packages.core.models import Message, confidence_from_evidence
from packages.graph import repo as graph_repo
from packages.state import repo as state_repo
from packages.state import verification

from .base import Agent, AgentOutput


# ---------------------------------------------------------------- 数据视图
@dataclass
class CareerFitReport(Message):
    job: dict = field(default_factory=dict)
    student_id: int = 0
    overall_fit: float = 0.0
    confidence: float = 0.0
    evidence_count: int = 0
    requirements: list = field(default_factory=list)
    strengths: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    caveat: str = ""


def _student_signal_stats(student_id: int) -> dict[str, dict]:
    """该学生全部项目的五类信号聚合，供没有对应知识点的"工程/协作类"需求佐证。"""
    rows = get_db().query(
        "SELECT signal_class, COUNT(*) AS n, AVG(value) AS avg_v"
        " FROM project_signal WHERE student_id=? GROUP BY signal_class",
        (student_id,),
    )
    return {r["signal_class"]: {"n": r["n"], "avg_value": round(r["avg_v"] or 0.0, 4)}
            for r in rows}


def _completed_task_ids(student_id: int) -> set[int]:
    rows = get_db().query(
        "SELECT task_id FROM task_assignment WHERE student_id=? AND status='done'",
        (student_id,),
    )
    return {r["task_id"] for r in rows}


def _kp_evidence(kp_id: int, quality: dict, done_tasks: set[int]) -> dict:
    """一个知识点在该学生身上的证据：掌握质量 + 用到它的项目任务里做完了几个。"""
    q = quality.get(kp_id, {})
    task_ids = graph_repo.tasks_requiring(kp_id)
    n_done = len([t for t in task_ids if t in done_tasks])
    return {
        "kp_id": kp_id,
        "p_mastery": q.get("p_mastery", 0.0),
        "retained": q.get("retained", 0.0),
        "validated": q.get("validated", False),
        "due": q.get("due", False),
        "evidence_count": q.get("evidence_count", 0),
        "tasks_using_kp": len(task_ids),
        "tasks_done_by_student": n_done,
    }


class CareerAgent(Agent):
    name = "career"
    system_prompt = (
        "你是面向用人单位的求职简历撰写助理。只依据给定字段组织语言，禁止引入字段之外"
        "的任何事实、荣誉或经历；弱项与数据不足的部分必须照实写出，不能只挑亮点。"
        "输出一段 300 字以内的中文简历摘要，先总述岗位匹配度，再分点写匹配的能力维度"
        "与佐证，最后一句必须写清尚待提升之处。"
    )

    # ------------------------------------------------------------ 确定性部分
    def fit_report(self, student_id: int, job_code: str) -> CareerFitReport:
        job = graph_repo.get_job(job_code)
        if not job:
            raise KeyError(f"岗位不存在：{job_code}")
        reqs = graph_repo.list_requirements(job.id)
        by_parent: dict[int | None, list] = {}
        for r in reqs:
            by_parent.setdefault(r.parent_id, []).append(r)

        mastery_rows = state_repo.mastery_rows(student_id)
        quality = {i["kp_id"]: i for i in verification.build(student_id).items}
        done_tasks = _completed_task_ids(student_id)
        signals = _student_signal_stats(student_id)

        gaps: list[dict] = []
        strengths: list[dict] = []
        t = CONFIG.teaching

        def build_node(r) -> dict:
            children = by_parent.get(r.id, [])
            kps = graph_repo.requirement_kps(r.id)
            if children:
                child_nodes = [build_node(c) for c in children]
                tw = sum(max(c["weight"], 0.0) for c in child_nodes) or 1.0
                fit = sum(c["fit"] * c["weight"] for c in child_nodes) / tw
                evid = sum(c["evidence_count"] for c in child_nodes)
                conf = confidence_from_evidence(evid // max(1, len(child_nodes)))
                return {
                    "code": r.code, "name": r.name, "weight": r.weight,
                    "fit": round(fit, 4), "confidence": conf, "evidence_count": evid,
                    "children": child_nodes, "kps": [], "signals": [],
                }
            # 叶子节点：直接由知识点掌握质量与/或项目信号佐证
            kp_ev = []
            fits = []
            evid = 0
            for k in kps:
                e = _kp_evidence(k["kp_id"], quality, done_tasks)
                e.update({"code": k["code"], "name": k["name"]})
                kp_ev.append(e)
                fits.append(e["retained"])
                evid += e["evidence_count"]
                item = {"requirement": r.name, "kp_code": k["code"], "kp_name": k["name"],
                        "retained": e["retained"], "evidence_count": e["evidence_count"],
                        "validated": e["validated"], "weight": r.weight}
                if e["retained"] >= t.mastery_threshold and e["evidence_count"] > 0:
                    strengths.append(item)
                elif e["evidence_count"] > 0:
                    gaps.append(item)
                else:
                    gaps.append({**item, "note": "尚无作答证据"})
            sig_ev = []
            sig_score = None
            for sc in (r.signal_classes or []):
                s = signals.get(sc, {"n": 0, "avg_value": 0.0})
                sig_ev.append({"signal_class": sc, **s})
                evid += s["n"]
            if sig_ev:
                sig_score = min(1.0, sum(s["n"] for s in sig_ev) / 10.0)
            if fits:
                fit = sum(fits) / len(fits)
                if sig_score is not None:
                    fit = round((fit + sig_score) / 2, 4)
            elif sig_score is not None:
                fit = sig_score
            else:
                fit = 0.0
            return {
                "code": r.code, "name": r.name, "weight": r.weight,
                "fit": round(fit, 4), "confidence": confidence_from_evidence(evid),
                "evidence_count": evid, "children": [], "kps": kp_ev, "signals": sig_ev,
            }

        top = [build_node(r) for r in by_parent.get(None, [])]
        tw = sum(max(n["weight"], 0.0) for n in top) or 1.0
        overall = sum(n["fit"] * n["weight"] for n in top) / tw if top else 0.0
        total_evid = sum(n["evidence_count"] for n in top)
        strengths.sort(key=lambda x: -x["retained"])
        gaps.sort(key=lambda x: (-x["weight"], x.get("retained", 0.0)))
        evid_for_evidence_count = sum(r["evidence_count"] for r in mastery_rows) or total_evid

        return CareerFitReport(
            job={"code": job.code, "name": job.name, "company": job.company,
                 "description": job.description},
            student_id=student_id,
            overall_fit=round(overall, 4),
            confidence=confidence_from_evidence(total_evid // max(1, len(top) or 1)),
            evidence_count=total_evid,
            requirements=top,
            strengths=strengths[:8],
            gaps=gaps[:8],
            caveat="" if evid_for_evidence_count >= t.min_evidence_for_report * 3
            else "数据不足，仅供参考",
        )

    # ------------------------------------------------------------ 三层可视化投影
    def build_universe(self, job_code: str, student_id: int | None = None) -> dict | None:
        """把三层结构铺成 3D 视图要的一份数据：job -> requirement 树 -> 知识点叶子。"""
        job = graph_repo.get_job(job_code)
        if not job:
            return None
        reqs = graph_repo.list_requirements(job.id)
        quality: dict[int, dict] = {}
        mastery: dict[int, float] = {}
        if student_id:
            mastery = state_repo.mastery_vector(student_id)
            quality = {i["kp_id"]: i for i in verification.build(student_id).items}

        nodes = [{"id": "job", "layer": 0, "code": job.code, "name": job.name,
                  "company": job.company}]
        edges: list[list[str]] = []
        depth_of: dict[int, int] = {}

        def depth(r) -> int:
            if r.id in depth_of:
                return depth_of[r.id]
            if r.parent_id is None:
                depth_of[r.id] = 1
                return 1
            parent = next((p for p in reqs if p.id == r.parent_id), None)
            d = depth(parent) + 1 if parent else 1
            depth_of[r.id] = d
            return d

        kp_seen: set[int] = set()
        for r in reqs:
            d = depth(r)
            nid = f"req:{r.id}"
            nodes.append({"id": nid, "layer": d, "code": r.code, "name": r.name,
                          "weight": r.weight})
            pid = f"req:{r.parent_id}" if r.parent_id else "job"
            edges.append([pid, nid])
            for k in graph_repo.requirement_kps(r.id):
                kid = f"kp:{k['kp_id']}"
                edges.append([nid, kid])
                if k["kp_id"] in kp_seen:
                    continue
                kp_seen.add(k["kp_id"])
                q = quality.get(k["kp_id"], {})
                nodes.append({
                    "id": kid, "layer": d + 1, "code": k["code"], "name": k["name"],
                    "mastery": (round(mastery[k["kp_id"]], 4)
                                if student_id and k["kp_id"] in mastery else None),
                    "retained": q.get("retained"),
                    "validated": q.get("validated"),
                    "evidence_count": q.get("evidence_count", 0),
                })

        return {
            "job": {"code": job.code, "name": job.name, "company": job.company,
                    "description": job.description},
            "student_id": student_id,
            "nodes": nodes,
            "edges": edges,
            "threshold": CONFIG.teaching.mastery_threshold,
        }

    # ------------------------------------------------------------ 表达部分
    def resume(self, student_id: int, job_code: str) -> AgentOutput:
        student = state_repo.get_student(student_id)
        r = self.fit_report(student_id, job_code)
        text, degraded = self.express(
            {
                "求职学生": (student or {}).get("name", ""),
                "目标岗位": f"{r.job['name']}（{r.job['company']}）" if r.job.get("company")
                          else r.job.get("name", ""),
                "综合匹配度": f"{round(r.overall_fit * 100)}%",
                "匹配的能力维度": "；".join(
                    f"{s['requirement']}·{s['kp_name']}（掌握度 {round(s['retained'] * 100)}%，"
                    f"{'已跨时间验证' if s['validated'] else '尚未跨时间验证'}）"
                    for s in r.strengths[:5]
                ) or "暂无已验证的强项，证据尚不充分",
                "尚待提升": "；".join(
                    f"{g['requirement']}·{g['kp_name']}"
                    + (f"（{g['note']}）" if g.get("note") else f"（掌握度 {round(g.get('retained', 0) * 100)}%）")
                    for g in r.gaps[:5]
                ) or "暂无明显短板",
                "数据充分性": r.caveat or "证据充分",
            },
            max_tokens=500,
        )
        return AgentOutput(
            agent=self.name, narrative=text, plan=r.to_dict(),
            confidence=r.confidence, evidence_count=r.evidence_count,
            degraded=degraded, caveat=r.caveat,
        )
