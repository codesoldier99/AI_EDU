"""生成一个可演示的虚拟班级：60 名学生 8 周的真实形态数据。

数据是模拟的，但**生成方式不作弊**：所有掌握度都由事件流经 tracker 产生，
`make replay` 可以从事件流完整重算并逐条比对。演示与生产走的是同一条通路。

刻意埋入的教学现象（供诊断报告演示）：
  - 约六成学生在"链式法则(ML-02-08)"上薄弱，导致"反向传播(ML-10-05)"大面积不过关
    —— 根因回溯应当指向链式法则，而不是停在反向传播；
  - "数据泄漏(ML-03-08)"是少数人错但错得离谱的知识点；
  - 若干学生中断一周后回归 —— 用于演示 comeback 激励。
"""
from __future__ import annotations

import json
import random
import sys
from datetime import timedelta

import _bootstrap  # noqa: F401
from packages.core.config import CONFIG, ROOT
from packages.core.db import get_db
from packages.core.timeutil import now, to_str
from packages.engagement import service as engagement
from packages.errors import service as errors
from packages.graph import repo as graph_repo
from packages.state import repo as state_repo
from packages.state import tracker

RNG = random.Random(20260731)

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN = ["伟", "芳", "娜", "敏", "静", "强", "磊", "洋", "艳", "勇", "军", "杰",
         "娟", "涛", "明", "超", "秀兰", "霞", "平", "刚", "桂英", "文轩", "梓涵",
         "浩然", "欣怡", "俊杰", "思远", "雨桐", "宇航", "沐辰"]

# 埋点：这些知识点被设计为班级薄弱区
WEAK_SEEDS = {
    "ML-02-08": 0.35,   # 链式法则 —— 真正的根因
    "ML-10-05": 0.30,   # 反向传播 —— 表层现象
    "ML-03-08": 0.30,   # 数据泄漏
    "ML-06-11": 0.40,   # GBDT
    "ML-07-03": 0.35,   # 对偶与KKT
}

TYPICAL_ERRORS = {
    "ML-10-05": [
        "把 ∂a/∂z 当成 1，直接用上层权重连乘，漏掉了激活函数的导数",
        "softmax 和交叉熵分开求导，中间那一步的雅可比矩阵写错了维度",
        "梯度公式里 W 的转置漏了，导致矩阵维度对不上就随手加了个 .T",
    ],
    "ML-02-08": [
        "复合三层以上就漏项，只乘了最外和最内两层",
        "把偏导当全导数用，忽略了中间变量对同一参数的多条路径",
    ],
    "ML-03-08": [
        "先对全量数据 StandardScaler().fit_transform，再 train_test_split",
        "用 y_test 挑了最佳阈值，然后报告这个阈值下的测试集指标",
    ],
    "ML-06-11": [
        "认为 GBDT 每棵树都在拟合原始标签，说不出残差与负梯度的关系",
    ],
}


def make_students(n: int = 60) -> list[dict]:
    out = []
    used = set()
    for i in range(n):
        while True:
            name = RNG.choice(SURNAMES) + RNG.choice(GIVEN)
            if name not in used:
                used.add(name)
                break
        sid = f"2026{i + 1:03d}"
        klass = "实验班A" if i < 32 else "实验班B"
        student_id = state_repo.upsert_student(sid, name, "2026级", klass)
        out.append({"id": student_id, "sid": sid, "name": name, "klass": klass})
    return out


def student_profile(idx: int) -> dict:
    """给每个学生一组潜在能力参数。真实系统里这是未知量，这里仅用于生成数据。"""
    base = RNG.gauss(0.62, 0.16)
    return {
        "base": max(0.15, min(0.95, base)),
        "diligence": max(0.2, min(1.0, RNG.gauss(0.7, 0.2))),   # 影响活跃天数
        "weak_bias": RNG.random(),                              # 是否落入薄弱区
        "absent_week": RNG.random() < 0.22,                     # 是否中断一周
    }


def p_correct(prof: dict, kp, mastered_prereq_ratio: float, code: str) -> float:
    p = prof["base"] * (1.15 - kp.difficulty) + 0.25 * mastered_prereq_ratio
    if code in WEAK_SEEDS and prof["weak_bias"] < WEAK_SEEDS[code] + 0.3:
        p *= 0.45
    return max(0.05, min(0.96, p))


def simulate(students: list[dict], weeks: int = 8) -> dict:
    kps = graph_repo.list_kps()
    by_code = {k.code: k for k in kps}
    # 教学顺序仅用于生成"哪些知识点这周被考到"，与系统调度无关
    ordered = sorted(kps, key=lambda k: (k.unit, k.code))
    per_week = max(1, len(ordered) // weeks)
    start = now() - timedelta(weeks=weeks)

    n_events = 0
    profiles = {s["id"]: student_profile(i) for i, s in enumerate(students)}
    for w in range(weeks):
        week_kps = ordered[w * per_week: (w + 1) * per_week]
        for s in students:
            prof = profiles[s["id"]]
            if prof["absent_week"] and w == weeks - 3:
                continue  # 中断一周，稍后回归 -> comeback
            active_days = sorted(RNG.sample(range(7), k=max(1, int(prof["diligence"] * 5))))
            for d in active_days:
                day = start + timedelta(weeks=w, days=d, hours=RNG.randint(9, 21))
                todo = RNG.sample(week_kps, k=min(len(week_kps), RNG.randint(2, 5)))
                for kp in todo:
                    pre = graph_repo.prereqs_of(kp.id)
                    mv = state_repo.mastery_vector(s["id"])
                    ratio = (
                        sum(1 for p in pre if mv.get(p, 0) >= CONFIG.teaching.mastery_threshold)
                        / len(pre) if pre else 1.0
                    )
                    ok = RNG.random() < p_correct(prof, kp, ratio, kp.code)
                    etype = RNG.choice(["quiz", "homework", "homework", "practice"])
                    r = tracker.record(
                        student_id=s["id"], event_type=etype, kp_id=kp.id, is_correct=ok,
                        source="homework" if etype == "homework" else etype,
                        source_ref=f"w{w + 1}", occurred_at=to_str(day),
                        payload={"week": w + 1},
                    )
                    n_events += 1
                    # 答错时留下错误实例（错误模式库从第一天开始积累）
                    if not ok and kp.code in TYPICAL_ERRORS and RNG.random() < 0.5:
                        errors.record_error(
                            student_id=s["id"], kp_id=kp.id,
                            raw_text=RNG.choice(TYPICAL_ERRORS[kp.code]),
                            event_id=r.event_id,
                        )
    return {"events": n_events, "students": len(students)}


def seed_projects_membership(students: list[dict]) -> dict:
    db = get_db()
    projects = graph_repo.list_projects()
    if not projects:
        return {}
    counts = {}
    for i, s in enumerate(students):
        p = projects[i % len(projects)]
        db.execute(
            "INSERT OR REPLACE INTO project_member(project_id, student_id, role)"
            " VALUES(?,?,?)", (p["id"], s["id"], "member"))
        counts[p["code"]] = counts.get(p["code"], 0) + 1
        tasks = graph_repo.list_tasks(p["id"])
        for t in tasks[:6]:
            status = RNG.choice(["done", "done", "doing", "todo"])
            db.execute(
                "INSERT OR REPLACE INTO task_assignment(task_id, student_id, status, updated_at)"
                " VALUES(?,?,?,?)", (t.id, s["id"], status, to_str(now())))
            if status == "done" and t.milestone:
                tracker.record(
                    student_id=s["id"], event_type="milestone", kp_id=None, source="task",
                    source_ref=f"task:{t.id}", payload={"detail": f"完成里程碑：{t.name}"})
    return counts


def write_signal_files(students: list[dict]) -> dict:
    """写出适配器要读的原始数据文件（模拟 Gitea/真机/实验平台的产出）。"""
    d = ROOT / "data" / "seed" / "signals"
    d.mkdir(parents=True, exist_ok=True)
    agv, vis = [], []
    base = now() - timedelta(weeks=8)
    for i, s in enumerate(students):
        proj = "PRJ-AGV" if i % 2 == 0 else "PRJ-VIS"
        for k in range(RNG.randint(3, 9)):
            at = to_str(base + timedelta(days=RNG.randint(0, 55), hours=RNG.randint(9, 22)))
            if proj == "PRJ-AGV":
                agv.append({
                    "sid": s["sid"], "project": proj, "at": at, "run_id": f"run{i}-{k}",
                    "success_rate": round(min(0.99, 0.55 + 0.05 * k + RNG.gauss(0, 0.06)), 3),
                    "localization_rmse_m": round(max(0.05, 0.30 - 0.02 * k + RNG.gauss(0, 0.03)), 3),
                    "retries": RNG.randint(0, 6),
                    "ci_pass": RNG.choice([0, 1, 1, 1]),
                })
            else:
                vis.append({
                    "sid": s["sid"], "project": proj, "at": at, "exp_id": f"exp{i}-{k}",
                    "map50": round(min(0.95, 0.50 + 0.045 * k + RNG.gauss(0, 0.05)), 3),
                    "dataset_version": k + 1,
                    "doc_pages": RNG.randint(0, 12),
                    "review_comments": RNG.randint(0, 9),
                })
    (d / "agv_runs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in agv), encoding="utf-8")
    (d / "vision_runs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in vis), encoding="utf-8")
    return {"agv_rows": len(agv), "vision_rows": len(vis)}


def collect_signals() -> dict:
    from packages.adapters import registry

    return registry.collect_all()


def simulate_asking(students: list[dict], n: int = 12) -> dict:
    """跑几段真实的追问会话，包含一次降级，用于演示降级留痕与降级热点。"""
    from packages.agents.asking import AskingAgent

    agent = AskingAgent()
    kp = graph_repo.get_kp_by_code("ML-10-05")
    sessions = []
    for s in RNG.sample(students, k=min(n, len(students))):
        out = agent.start(s["id"], kp.id)
        sid = out.plan["session_id"]
        for text in ("不知道怎么下手", "还是不会", "完全没思路"):
            out = agent.reply(sid, text)
        sessions.append({"student": s["name"], "session_id": sid,
                         "escalation": out.plan["escalation_label"]})
    return {"sessions": len(sessions), "sample": sessions[:3]}


def simulate_review(students: list[dict]) -> dict:
    """提交一份有典型问题的脚本，走两级审查并演示"教师采纳后才回写 L2"。"""
    from packages.agents.review import ReviewAgent

    bad_src = '''
import os
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

API_KEY = "sk-1234567890abcdefghij"

def load(path, cache={}):
    """读取数据。"""
    X = np.load(path)
    y = np.load(path.replace("X", "y"))
    scaler = StandardScaler()
    X = scaler.fit_transform(X)          # 划分之前就 fit 了全量数据
    return X, y

def run(path):
    X, y = load(path)
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2)
    model = train(X_tr, y_tr)
    best_threshold = tune(model, X_te, y_te)   # 在测试集上调阈值
    try:
        acc = accuracy_score(y_te, model.predict(X_te) > best_threshold)
    except:
        acc = 0
    print("accuracy", acc)      # 类别不平衡，只报准确率
    return acc
'''
    agent = ReviewAgent()
    s = students[0]
    out = agent.review_source(s["id"], "student_submission.py", bad_src, use_llm=True)
    # 教师逐条处理：可靠映射的采纳后回写，不可靠的即便采纳也不回写
    actions = []
    for fid, f in zip(out.plan["finding_ids"], out.plan["findings"]):
        act = "accepted" if f["severity"] in ("error", "warn") else "rejected"
        actions.append(agent.teacher_action(fid, act))
    wrote = [a for a in actions if a.get("wrote_back_kp_ids")]
    return {"findings": len(out.plan["findings"]),
            "reliable_mappings": out.plan["n_reliable_mappings"],
            "wrote_back": len(wrote), "student": s["name"]}


def finalize(students: list[dict]) -> dict:
    """刷新派生态：能力画像、连续天数与成就。"""
    n_ach = 0
    for s in students:
        tracker.recompute_ability(s["id"])
        n_ach += len(engagement.scan_achievements(s["id"]))
        # 破关：把已攻克的根因知识点标出来（只奖励坚持类行为）
        mv = state_repo.mastery_vector(s["id"])
        for code in ("ML-02-08", "ML-03-08"):
            kp = graph_repo.get_kp_by_code(code)
            if kp and mv.get(kp.id, 0) >= CONFIG.teaching.mastery_threshold:
                from packages.graph import algo

                if engagement.mark_breakthrough(
                    s["id"], kp.id, kp.name, len(algo.descendants(kp.id, 2))
                ):
                    n_ach += 1
    return {"achievements": n_ach}


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    db = get_db()
    db.migrate(verbose=False)
    if not graph_repo.list_kps():
        raise SystemExit("请先执行 make seed 导入知识图谱")

    print("→ 建立虚拟班级")
    students = make_students(n)
    print(f"  {len(students)} 名学生（实验班A/B）")

    print("→ 模拟 8 周学习行为（全部经 tracker 写入事件流）")
    r = simulate(students)
    print(f"  {r['events']} 条学习事件")

    print("→ 项目成员与任务分派")
    print(f"  {seed_projects_membership(students)}")

    print("→ 生成项目原始数据并经适配器归一化为五类信号")
    print(f"  原始：{write_signal_files(students)}")
    print(f"  归一化入库：{collect_signals()}")

    print("→ 模拟苏格拉底追问（含降级留痕）")
    print(f"  {simulate_asking(students)['sessions']} 段会话")

    print("→ 模拟代码审查与教师处置")
    print(f"  {simulate_review(students)}")

    print("→ 刷新画像与参与度")
    print(f"  {finalize(students)}")

    print("\n演示数据就绪。")
    print("  教师端令牌：teacher:T001    学生端令牌：student:2026001")
    print("  启动：make dev（或 python3 aiedu.py dev）后打开 http://127.0.0.1:8900/")


if __name__ == "__main__":
    main()
