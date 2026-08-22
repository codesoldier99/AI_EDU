"""导入种子数据：能力模块 → 课程知识图谱 → 项目任务与任务-知识点映射 → RAG 三库。

导入流程（DEVELOPMENT_PLAN §0.2）：
    大模型预抽取 → 教师审核 → 依赖边半自动生成 → **环检测** → 入库
本脚本承担最后两步：环检测不通过则拒绝入库。

用法：
    python3 scripts/seed.py            # 全部
    python3 scripts/seed.py course     # 仅课程图谱
    python3 scripts/seed.py kb         # 仅知识库
    python3 scripts/seed.py questions  # 仅起始题库
"""
from __future__ import annotations

import sys

import _bootstrap  # noqa: F401
from packages.core.config import ROOT
from packages.core.db import get_db
from packages.graph import algo, repo
from packages.rag import store
from packages.state import repo as state_repo

SEED = ROOT / "data" / "seed"

# 多门课程 / 多个项目并存：新增一门课或一个项目就在这里加一行文件名。
# 课程顺序有意义——跨课程前置边（如 DAC-05-10 依赖 ML-05-01）要求被引课先入库。
PROGRAM_FILE = "program_ai_zsb.yaml"
COURSE_FILES = ["course_ml.yaml", "course_dac.yaml"]
PROJECT_FILES = ["projects.yaml", "projects_dac.yaml"]


def _yaml(path):
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _split_row(row: str, n: int) -> list[str]:
    parts = [p.strip() for p in str(row).split("|")]
    parts += [""] * (n - len(parts))
    return parts[:n]


# ---------------------------------------------------------------- 培养方案
def seed_program(file: str = "program_ai_zsb.yaml") -> dict:
    """导入培养方案：专业、课程清单、课程代码前缀。

    **只导入"有哪些课"，不导入"每门课有哪些知识点"。**
    绝大多数课程导入后知识点数是 0，这是刻意的：图谱按项目拉取建设，
    没有任何项目拉取过的课先不建。该建哪门课的哪一部分由 kp_demand 队列回答。
    """
    data = _yaml(SEED / file)
    p = data["program"]
    pid = repo.upsert_program(p["code"], p["name"], p.get("level", ""), p.get("klass", ""),
                              str(p.get("version", "")), p.get("note", ""))
    n = 0
    for seq, row in enumerate(data.get("courses", [])):
        code, name, module, sem, credit, hours, exam, prefix = _split_row(row, 8)
        cid = repo.upsert_course(code, name, float(credit or 0), str(sem or ""))
        repo.link_program_course(pid, cid, module, int(sem or 0), float(credit or 0),
                                 int(hours or 0), exam, seq)
        if prefix:
            repo.set_course_prefix(prefix, cid)
        n += 1

    # 项目知识域：不属于培养方案任何一门课，因此不折算学分。
    n_dom = 0
    for row in data.get("project_domains", []):
        code, name = _split_row(row, 2)
        repo.upsert_course(code, name, 0, "")
        n_dom += 1

    total = repo.program_credit_total(pid)
    return {"program": p["code"], "courses": n, "domains": n_dom,
            "total_credit": total}


def retire_empty_courses(file: str = "program_ai_zsb.yaml") -> list[str]:
    """删掉知识点已全部移交出去的空壳课程。

    **必须在课程图谱导入之后调用**——移交是 seed_course 干的，
    在那之前 ML 还挂着 183 个知识点，看上去"非空"。
    只删确实一个知识点都不剩的，剩一个都不动：宁可留个空壳，
    也不能把还有人引用的课删掉。
    """
    data = _yaml(SEED / file)
    retired = []
    for code in data.get("retire_courses", []):
        c = repo.get_course(code)
        if not c:
            continue
        left = repo.list_kps(c["id"])
        if left:
            print(f"  ⚠ 课程 {code} 仍有 {len(left)} 个知识点，未退役（归属尚未全部移交）")
            continue
        repo.delete_course(c["id"])
        retired.append(code)
    return retired


def _ensure_course(code: str, unit_name: str) -> int:
    """取移交目标课程的 id；不存在就先把培养方案补载一遍再取。

    归属移交依赖培养方案先入库。与其要求每个调用方都记得这个顺序
    （测试里直接调 seed_course() 的地方就会踩到），不如让它自己补上——
    培养方案导入是幂等的，补一次没有副作用。
    补完还是找不到，才是真的配错了课程代码，这时必须报错而不是默默归错人。
    """
    c = repo.get_course(code)
    if c:
        return c["id"]
    seed_program(PROGRAM_FILE)
    c = repo.get_course(code)
    if not c:
        raise SystemExit(
            f"✗ 章节「{unit_name}」要移交给课程 {code}，但培养方案里没有这门课。"
            f"\n  检查 {PROGRAM_FILE} 的课程代码，或该章节的 course: 写错了。")
    return c["id"]


# ---------------------------------------------------------------- 课程图谱
def seed_course(file: str = "course_ml.yaml") -> dict:
    data = _yaml(SEED / file)
    c = data["course"]
    course_id = repo.upsert_course(c["code"], c["name"], c.get("credit", 0), str(c.get("term", "")))

    mod_ids = {}
    for m in data.get("modules", []):
        code, name, desc = (m + ["", "", ""])[:3] if isinstance(m, list) else (m, m, "")
        mod_ids[code] = repo.upsert_module(code, name, desc)

    rows: list[tuple] = []
    for unit in data["units"]:
        # unit 可以写 `course:` 覆盖归属，把整章移交给培养方案里的另一门课。
        # 例：原「第2章 数学基础」拆成三段，分别归线性代数A / 高等数学B / 概率论。
        # 移交只改 knowledge_point.course_id，知识点代码不动——
        # 代码被考卷与事件流引用着，是身份；归属是属性。
        owner = course_id
        if unit.get("course"):
            owner = _ensure_course(unit["course"], unit["name"])
        for row in unit["kps"]:
            code, name, ktype, diff, mods, prereqs, brief = _split_row(row, 7)
            rows.append((unit["name"], code, name, ktype, diff, mods, prereqs, brief, owner))

    # 第一遍：建节点
    owners: set[int] = set()
    for unit, code, name, ktype, diff, mods, _pre, brief, owner in rows:
        owners.add(owner)
        kp_id = repo.upsert_kp(
            owner, code, name, brief, "atomic", ktype or "concept",
            float(diff or 0.5), unit,
        )
        for mc in [x.strip() for x in mods.split(",") if x.strip()]:
            if mc in mod_ids:
                repo.link_kp_module(kp_id, mod_ids[mc], 1.0)

    # 第二遍：建依赖边
    n_edges, missing = 0, []
    for _unit, code, *_rest in rows:
        pre_codes = _rest[4]
        to = repo.get_kp_by_code(code)
        for pc in [x.strip() for x in pre_codes.split(",") if x.strip()]:
            frm = repo.get_kp_by_code(pc)
            if not frm:
                # 引用了尚不存在的知识点：登记成需求，不是报个警就算了。
                # 这是拉取式图谱建设的入口——见 migrations/006_program.sql。
                missing.append((code, pc))
                repo.add_demand(pc, demanded_by=f"kp:{code}", kind="prereq")
                continue
            repo.add_edge(frm.id, to.id, "prereq", 1.0)
            n_edges += 1

    cycles = algo.detect_cycles()
    if cycles:
        raise SystemExit(f"✗ 依赖图存在环，拒绝入库：{cycles[:3]}")
    repo.close_built_demands()
    return {"course": c["code"], "kps": len(rows), "edges": n_edges,
            "missing_prereqs": missing, "modules": len(mod_ids),
            "owner_courses": len(owners)}


# ---------------------------------------------------------------- 项目
def seed_projects(file: str = "projects.yaml") -> dict:
    data = _yaml(SEED / file)
    n_tasks = n_links = 0
    unknown: list[str] = []
    for p in data["projects"]:
        pid = repo.upsert_project(p["code"], p["name"], p["ptype"], p["adapter_key"],
                                  p.get("description", ""))
        for seq, row in enumerate(p.get("tasks", [])):
            code, name, parent, milestone, req, helpful = _split_row(row, 6)
            tid = repo.upsert_task(pid, code, name, parent or None,
                                   int(milestone or 0), seq)
            n_tasks += 1
            for nec, codes in (("required", req), ("helpful", helpful)):
                for kc in [x.strip() for x in codes.split(",") if x.strip()]:
                    kp = repo.get_kp_by_code(kc)
                    if not kp:
                        # 教师标注了一个还没建的知识点。这不是笔误，是需求：
                        # "这个任务需要概率论里的正态分布，但那门课的图谱还没建"。
                        # 登记进队列，等它被建出来时自动闭合。
                        unknown.append(kc)
                        repo.add_demand(kc, demanded_by=f"task:{code}",
                                        project_code=p["code"], kind=f"task_{nec}")
                        continue
                    repo.link_task_kp(tid, kp.id, nec, "teacher")
                    n_links += 1
        for dep in p.get("deps", []):
            a, b = [x.strip() for x in dep.split(">")]
            repo.add_task_dependency(a, b)
    repo.close_built_demands()
    return {"tasks": n_tasks, "task_kp_links": n_links, "unknown_kp_codes": sorted(set(unknown))}


# ---------------------------------------------------------------- 知识库
def seed_kb() -> dict:
    out = {}
    for f in sorted((SEED / "kb").glob("*.md")):
        head = f.read_text(encoding="utf-8").splitlines()[:4]
        kb = "course"
        codes: list[str] = []
        for line in head:
            line = line.strip()
            if line.startswith("<!-- kb:"):
                kb = line.split(":", 1)[1].replace("-->", "").strip()
            elif line.startswith("<!-- kp:"):
                codes = [c.strip() for c in
                         line.split(":", 1)[1].replace("-->", "").split(",") if c.strip()]
        out[f.name] = store.index_file(kb, f, codes)
    return out


# ---------------------------------------------------------------- 起始题库
def seed_questions(file: str = "questions.yaml") -> dict:
    """导入教师录入的起始题库。

    教师录入 = 已审（teacher_verified=1），可直接组卷；
    模型出的题走 QuizAgent.draft，一律进待审队列。两条路径不混。
    """
    from packages.quiz import bank

    path = SEED / file
    if not path.exists():
        return {"questions": 0, "unknown_kp_codes": []}
    data = _yaml(path)
    n, unknown = 0, []
    for q in data.get("questions", []):
        kp = repo.get_kp_by_code(q["kp"])
        if not kp:
            unknown.append(q["kp"])
            continue
        bank.add(
            kp_id=kp.id, stem=q["stem"], answer=str(q["answer"]),
            qtype=q.get("type", "choice"), options=q.get("options") or [],
            rationale=(q.get("rationale") or "").strip(),
            difficulty=float(q.get("difficulty", 0.5)),
            origin="teacher", keywords=q.get("keywords") or [],
            tolerance=float(q.get("tolerance", 0.0) or 0.0),
        )
        n += 1
    return {"questions": n, "unknown_kp_codes": unknown}


# ---------------------------------------------------------------- 教师
def seed_teachers() -> int:
    db = get_db()
    people = [
        ("T001", "张导师", ["实验班A"]),
        ("T002", "李导师", ["实验班A"]),
        ("T003", "王主任", []),        # 空白名单 = 可见全部班级
    ]
    import json

    for code, name, klasses in people:
        exists = db.query_one("SELECT id FROM teacher WHERE code=?", (code,))
        if exists:
            db.execute("UPDATE teacher SET name=?, klasses=? WHERE id=?",
                       (name, json.dumps(klasses, ensure_ascii=False), exists["id"]))
        else:
            db.execute("INSERT INTO teacher(code, name, klasses) VALUES(?,?,?)",
                       (code, name, json.dumps(klasses, ensure_ascii=False)))
    return len(people)


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    db = get_db()
    print("→ 迁移数据库")
    db.migrate()
    if what in ("all", "program"):
        r = seed_program(PROGRAM_FILE)
        print(f"→ 培养方案 {r['program']}：{r['courses']} 门课 / 合计 {r['total_credit']} 学分"
              f"（另有 {r['domains']} 个项目知识域，不折算学分）")
    if what in ("all", "course"):
        for f in COURSE_FILES:
            r = seed_course(f)
            print(f"→ 课程图谱 {r['course']}：{r['kps']} 知识点 / {r['edges']} 依赖边 "
                  f"/ {r['modules']} 能力模块")
            if r["missing_prereqs"]:
                print(f"  → 前置引用了 {len(r['missing_prereqs'])} 个尚未建的知识点，"
                      f"已登记进需求队列（make demand 查看）")
        print("  ✓ 环检测通过（含跨课程边，全图仍为 DAG）")
        retired = retire_empty_courses(PROGRAM_FILE)
        if retired:
            print(f"  ✓ 已退役空壳课程：{'、'.join(retired)}（知识点已全部移交给培养方案课程）")
    if what in ("all", "projects"):
        for f in PROJECT_FILES:
            r = seed_projects(f)
            print(f"→ 项目任务 {f}：{r['tasks']} 个任务 / {r['task_kp_links']} 条任务-知识点映射")
            if r["unknown_kp_codes"]:
                print(f"  → 任务要求了 {len(r['unknown_kp_codes'])} 个尚未建的知识点，"
                      f"已登记进需求队列：{'、'.join(r['unknown_kp_codes'][:4])}…")
    if what in ("all", "kb"):
        r = seed_kb()
        print(f"→ 知识库：{sum(r.values())} 个文本块，来自 {len(r)} 个文件")
    if what in ("all", "questions"):
        r = seed_questions()
        print(f"→ 起始题库：{r['questions']} 道教师录入题（已审，可直接组卷）")
        if r["unknown_kp_codes"]:
            print(f"  ⚠ 未知知识点代码：{r['unknown_kp_codes'][:5]}")
    if what in ("all", "teachers"):
        print(f"→ 教师：{seed_teachers()} 人")
    repo.invalidate_stats_cache()   # 图谱结构变了，让 /api/health 立刻反映出来
    print("完成。")


if __name__ == "__main__":
    main()
