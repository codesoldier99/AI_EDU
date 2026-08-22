"""知识点自动匹配的运维入口：生成候选 → 教师审 → 采纳入库。

    python3 scripts/kpmatch.py propose PRJ-DAC [--rationale]   为项目全部任务生成候选
    python3 scripts/kpmatch.py queue [PRJ-DAC] [--limit 40]    看待审队列
    python3 scripts/kpmatch.py why <候选id>                     让模型解释某一条
    python3 scripts/kpmatch.py accept <候选id> [--helpful]      采纳（写入 task_kp_link）
    python3 scripts/kpmatch.py reject <候选id>                  否决（下次不再提出）
    python3 scripts/kpmatch.py stats [PRJ-DAC]                  队列概况与采纳率
    python3 scripts/kpmatch.py eval PRJ-DAC                     拿导师已标注的映射当标准答案自测

`propose` 默认**不调用大模型**：一次几百条候选，逐条让模型写理由既慢又费，
而理由只在教师真正看那一条时才有用。加 --rationale 才批量生成。
判定部分（谁是候选、多少分）任何时候都不经模型。

`eval` 是这套匹配值不值得开的唯一诚实答案：把导师已经标好的映射当标准答案，
看自动匹配能召回多少。数字难看就该改算法，而不是改宣传口径。
"""
from __future__ import annotations

import sys

import _bootstrap  # noqa: F401
from packages.agents.kpmatch import KPMatchAgent
from packages.graph import repo as graph_repo


def _corpus(project_code: str) -> dict[str, str]:
    """向适配器要项目语料。适配器没有这个能力就算了，匹配照常跑，只是召回低些。"""
    proj = graph_repo.get_project(project_code)
    if not proj:
        raise SystemExit(f"项目不存在：{project_code}")
    try:
        from packages.adapters.registry import get_adapter

        ad = get_adapter(proj["adapter_key"])
    except Exception as e:
        print(f"  （适配器不可用：{e}，仅用任务名匹配）")
        return {}
    if not hasattr(ad, "task_corpus"):
        print(f"  （适配器 {proj['adapter_key']} 未提供语料，仅用任务名匹配）")
        return {}
    c = ad.task_corpus()
    print(f"  语料：{len(c)} 个任务拿到了项目自带的验收清单/源码说明")
    return c


def cmd_propose(argv: list[str]) -> None:
    code = argv[0] if argv else "PRJ-DAC"
    with_rat = "--rationale" in argv
    print(f"→ 为 {code} 生成知识点候选")
    out = KPMatchAgent().propose_project(code, corpus=_corpus(code), with_rationale=with_rat)
    print("  " + out.narrative)
    p = out.plan
    print(f"  队列：待审 {p['queue']['pending']} / 已采纳 {p['queue']['accepted']}"
          f" / 已否决 {p['queue']['rejected']}")
    if p["preview"]:
        print("\n  新增候选（前 12 条）：")
        for x in p["preview"]:
            print(f"    {x['task']:12s} → {x['kp']:10s} {x['kp_name'][:20]:22s}"
                  f" {x['necessity']:8s} conf={x['confidence']:<5} 证据={'、'.join(x['terms'][:4])}")
    print(f"\n  下一步：python3 scripts/kpmatch.py queue {code}")


def cmd_queue(argv: list[str]) -> None:
    code = next((a for a in argv if not a.startswith("--")), None)
    limit = 40
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    rows = graph_repo.list_candidates(code, "pending", limit)
    if not rows:
        print("待审队列为空。")
        return
    print(f"待审 {len(rows)} 条（按置信度降序，先看最有把握的）：\n")
    print(f"  {'id':>5}  {'任务':12s} {'知识点':10s} {'名称':22s} {'建议':8s} {'置信':>5}  证据")
    import json
    for r in rows:
        ev = "、".join(json.loads(r["evidence"] or "[]")[:4])
        print(f"  {r['id']:>5}  {r['task_code']:12s} {r['kp_code']:10s}"
              f" {r['kp_name'][:20]:22s} {r['necessity']:8s} {r['confidence']:>5}  {ev}")
    print("\n  采纳：kpmatch.py accept <id>      否决：kpmatch.py reject <id>")


def cmd_why(argv: list[str]) -> None:
    cid = int(argv[0])
    c = graph_repo.get_candidate(cid)
    if not c:
        raise SystemExit(f"候选不存在：{cid}")
    task = graph_repo.get_task(c["task_id"])
    kp = graph_repo.get_kp(c["kp_id"])
    import json
    from packages.agents.kpmatch import Candidate

    agent = KPMatchAgent()
    cand = Candidate(kp_id=kp.id, kp_code=kp.code, kp_name=kp.name, score=c["score"],
                     confidence=c["confidence"], terms=json.loads(c["evidence"] or "[]"))
    text, degraded = agent.explain(task.name, cand)
    print(f"任务 {task.code} {task.name}")
    print(f"候选 {kp.code} {kp.name}   置信度 {c['confidence']}")
    print(f"证据 {'、'.join(cand.terms)}")
    print(f"理由 {text}" + ("   （模型不可用，已降级为确定性说明）" if degraded else ""))


def cmd_decide(argv: list[str], accept: bool) -> None:
    cid = int(argv[0])
    nec = "helpful" if "--helpful" in argv else None
    r = graph_repo.decide_candidate(cid, accept, decided_by="cli", necessity=nec)
    if not r.get("ok"):
        raise SystemExit(f"未处理：{r.get('reason')}")
    kp = graph_repo.get_kp(r["kp_id"])
    task = graph_repo.get_task(r["task_id"])
    verb = f"已采纳 → 写入 task_kp_link（{r['necessity']}，署名 teacher）" if accept else "已否决"
    print(f"{verb}：{task.code} ← {kp.code} {kp.name}")


def cmd_stats(argv: list[str]) -> None:
    code = argv[0] if argv else None
    s = graph_repo.candidate_stats(code)
    print(f"待审 {s['pending']} / 已采纳 {s['accepted']} / 已否决 {s['rejected']}")
    if s["accept_rate"] is None:
        print("采纳率：尚无判决。教师审过一批之后，这个数字才是判断匹配好不好用的依据。")
    else:
        print(f"采纳率：{s['accept_rate']:.0%}（{s['accepted']}/{s['decided']}）")


def cmd_eval(argv: list[str]) -> None:
    """拿导师标注当标准答案，自测召回率。"""
    code = argv[0] if argv else "PRJ-DAC"
    proj = graph_repo.get_project(code)
    if not proj:
        raise SystemExit(f"项目不存在：{code}")
    agent, corpus = KPMatchAgent(), _corpus(code)
    tot = hit_any = hit_top1 = 0
    tasks_with_truth = 0
    for t in graph_repo.list_tasks(proj["id"]):
        truth = {r["kp_id"] for r in graph_repo.required_kps(t.id, include_helpful=True)}
        if not truth:
            continue
        tasks_with_truth += 1
        _t, cands = agent.match_task(t.code, corpus.get(t.code, ""), top_k=6)
        got = [c.kp_id for c in cands]
        tot += len(truth)
        hit_any += len(truth & set(got))
        if got and got[0] in truth:
            hit_top1 += 1
    print(f"\n标准答案：{tasks_with_truth} 个任务、{tot} 条导师标注的映射")
    print(f"Top-6 召回率：{hit_any}/{tot} = {hit_any / tot:.1%}"
          if tot else "无标注可比")
    print(f"Top-1 命中任务数：{hit_top1}/{tasks_with_truth} = "
          f"{hit_top1 / tasks_with_truth:.1%}" if tasks_with_truth else "")
    print("\n说明：召回率不是 100% 是正常的——导师会标进一些字面上完全不相关、"
          "但工程上确实需要的知识点。\n那部分恰恰是机器替代不了的，也是这套东西"
          "只做候选、不做决定的原因。")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, argv = sys.argv[1], sys.argv[2:]
    {
        "propose": lambda: cmd_propose(argv),
        "queue": lambda: cmd_queue(argv),
        "why": lambda: cmd_why(argv),
        "accept": lambda: cmd_decide(argv, True),
        "reject": lambda: cmd_decide(argv, False),
        "stats": lambda: cmd_stats(argv),
        "eval": lambda: cmd_eval(argv),
    }.get(cmd, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
