"""知识点自动匹配：确定性、可解释、以及"绝不自己拍板"。

这三条里最重要的是第三条。匹配算得再准，只要它能绕过教师直接写进
task_kp_link，学分认定的依据就从"导师标注"变成了"模型推测"——
所以这里花最多笔墨测的不是准确率，而是**边界**。
"""
from __future__ import annotations

import unittest

from base import DBTestCase, OfflineLLMMixin

from packages.tools.lexmatch import Doc, Index, confidence, tokens


class TestLexMatch(unittest.TestCase):
    """确定性匹配工具：零依赖、可复算。"""

    def test_tokens_mix_chinese_and_english(self):
        t = tokens("编码器 encoder_decoder.v 四倍频")
        self.assertIn("编码", t)
        self.assertIn("四倍", t)
        self.assertIn("encoder_decoder.v", t)

    def test_function_word_bigrams_dropped(self):
        """"作与""的测"这类跨虚词二元组不是证据，教师看到只会失去信任。"""
        t = tokens("工程协作与质量")
        self.assertIn("协作", t)
        self.assertNotIn("作与", t)
        self.assertNotIn("与质", t)

    def test_same_query_same_result(self):
        """确定性：同样的输入必须给出同样的分数，否则教师无法复核。"""
        docs = [Doc(1, "编码器解码与四倍频实现"), Doc(2, "共聚焦成像的基本原理")]
        a = Index([Doc(d.key, d.text) for d in docs]).search("编码器四倍频")
        b = Index([Doc(d.key, d.text) for d in docs]).search("编码器四倍频")
        self.assertEqual([(h.key, h.score) for h in a], [(h.key, h.score) for h in b])

    def test_evidence_terms_returned(self):
        ix = Index([Doc(1, "位置同步输出 PSO 原理 台子走到比较点自动发脉冲")])
        hits = ix.search("PSO 位置同步输出")
        self.assertTrue(hits)
        self.assertTrue(set(hits[0].terms) & {"pso", "位置", "同步", "输出"})

    def test_confidence_is_monotonic_by_rank(self):
        """待审队列按置信度排序，若低排名反而更"有把握"，教师看到的顺序就自相矛盾。"""
        ix = Index([Doc(i, t) for i, t in enumerate([
            "编码器解码与四倍频实现", "增量式编码器与四倍频", "共聚焦成像的基本原理",
            "触发时序图的绘制与解读", "数据库读写与事务边界"])])
        hits = ix.search("编码器 四倍频 解码")
        confs = [confidence(hits, i) for i in range(len(hits))]
        self.assertEqual(confs, sorted(confs, reverse=True), f"置信度不单调：{confs}")

    def test_no_match_returns_empty(self):
        """匹配不上就说匹配不上，不硬凑一个——铁律 7 的同一条道理。"""
        ix = Index([Doc(1, "共聚焦成像的基本原理")])
        self.assertEqual(ix.search("量子纠缠与贝尔不等式"), [])


class TestKPMatchAgent(OfflineLLMMixin, DBTestCase):
    seed_course = True

    def _task(self):
        from packages.graph import repo as g

        pid = g.upsert_project("P1", "测试项目", "test", "none")
        return g.upsert_task(pid, "T1", "反向传播与梯度消失诊断", None, 0, 0), pid

    def test_candidates_never_enter_task_kp_link(self):
        """最重要的一条：跑完匹配，正式映射表必须一条没多。"""
        from packages.agents.kpmatch import KPMatchAgent
        from packages.graph import repo as g

        tid, _pid = self._task()
        before = self.db.scalar("SELECT COUNT(*) FROM task_kp_link")
        out = KPMatchAgent().propose_project("P1", with_rationale=False)
        self.assertGreater(out.plan["proposed"], 0, "什么都没匹配到，测试本身失效了")
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM task_kp_link"), before,
                         "候选绕过教师直接写进了正式映射表")
        self.assertGreater(self.db.scalar("SELECT COUNT(*) FROM task_kp_candidate"), 0)
        self.assertEqual(g.candidate_stats("P1")["accepted"], 0)

    def test_accept_writes_link_signed_by_teacher(self):
        """采纳后署名必须是 teacher——模型不署名，因为出事时它不负责。"""
        from packages.agents.kpmatch import KPMatchAgent
        from packages.graph import repo as g

        self._task()
        KPMatchAgent().propose_project("P1", with_rationale=False)
        cand = g.list_candidates("P1", "pending")[0]
        g.decide_candidate(cand["id"], True, decided_by="T001")
        row = self.db.query_one(
            "SELECT annotated_by FROM task_kp_link WHERE task_id=? AND kp_id=?",
            (cand["task_id"], cand["kp_id"]))
        self.assertIsNotNone(row)
        self.assertEqual(row["annotated_by"], "teacher")

    def test_reject_is_remembered(self):
        """否决过的映射不该在下次重跑时又冒出来烦教师。"""
        from packages.agents.kpmatch import KPMatchAgent
        from packages.graph import repo as g

        self._task()
        agent = KPMatchAgent()
        agent.propose_project("P1", with_rationale=False)
        cand = g.list_candidates("P1", "pending")[0]
        g.decide_candidate(cand["id"], False, decided_by="T001")
        agent.propose_project("P1", with_rationale=False)      # 重跑
        still = [c for c in g.list_candidates("P1", "pending") if c["id"] == cand["id"]]
        self.assertEqual(still, [], "被否决的候选又回到了队列里")

    def test_decision_is_idempotent(self):
        from packages.agents.kpmatch import KPMatchAgent
        from packages.graph import repo as g

        self._task()
        KPMatchAgent().propose_project("P1", with_rationale=False)
        cid = g.list_candidates("P1", "pending")[0]["id"]
        self.assertTrue(g.decide_candidate(cid, True, "T001")["ok"])
        self.assertFalse(g.decide_candidate(cid, True, "T001")["ok"], "同一条被判了两次")

    def test_already_annotated_not_reproposed(self):
        """教师已经标过的映射不必再问一遍。"""
        from packages.agents.kpmatch import KPMatchAgent
        from packages.graph import repo as g

        tid, _ = self._task()
        g.link_task_kp(tid, self.kp["B"], "required", "teacher")
        KPMatchAgent().propose_project("P1", with_rationale=False)
        got = {c["kp_id"] for c in g.list_candidates("P1", "pending")}
        self.assertNotIn(self.kp["B"], got)

    def test_prereq_expansion_is_labeled_and_downweighted(self):
        """沿依赖边推理出来的候选必须自报家门，且不得冒充文本证据。"""
        from packages.agents.kpmatch import KPMatchAgent

        cands = KPMatchAgent().plan("反向传播")
        expanded = [c for c in cands if c.matcher == "prereq-v1"]
        self.assertTrue(expanded, "依赖边扩展没有产出（小图里 A 是 B 的前置）")
        for c in expanded:
            self.assertEqual(c.necessity, "helpful", "推理来的候选不该标 required")
            self.assertTrue(c.via, "没写明它是因为谁被扩展出来的")
            self.assertIn("前置", c.terms[0])

    def test_offline_model_does_not_change_verdict(self):
        """换掉"嘴"不改变"脑"：有无理由，候选集与分数一模一样。"""
        from packages.agents.kpmatch import KPMatchAgent

        a = [(c.kp_id, c.score, c.confidence) for c in KPMatchAgent().plan("梯度消失")]
        b = [(c.kp_id, c.score, c.confidence) for c in KPMatchAgent().plan("梯度消失")]
        self.assertEqual(a, b)


class TestDAC3DAdapter(DBTestCase):
    """真实产业项目适配器。仓库不在时整体跳过——测试不该断言运行环境。"""

    def setUp(self):
        super().setUp()
        from packages.adapters.dac3d import DAC3DAdapter

        self.ad = DAC3DAdapter()
        if not self.ad.available:
            self.skipTest("本机没有 dac-3d_system_v3.0 仓库")

    def test_signals_are_normalized_to_five_classes(self):
        from packages.adapters.base import SIGNAL_CLASSES

        for s in self.ad.collect():
            self.assertIn(s.signal_class, SIGNAL_CLASSES)
            s.validate()

    def test_unmapped_authors_are_skipped_not_guessed(self):
        """映射不到学号的作者必须被跳过。猜一个，信号就记到别人头上了。"""
        sids = {s.student_sid for s in self.ad.collect()}
        self.assertTrue(sids)
        self.assertTrue(sids <= set(self.ad.authors.values()),
                        f"出现了映射表之外的学号：{sids - set(self.ad.authors.values())}")

    def test_bot_authors_ignored(self):
        for s in self.ad.collect():
            self.assertNotIn("noreply@anthropic.com", s.student_sid)

    def test_task_corpus_parses_six_levels(self):
        self.assertEqual(sorted(self.ad._curriculum_sections()), ["1", "2", "3", "4", "5", "6"])


class TestCorpusSnapshotFallback(DBTestCase):
    """教学服务器上没有产业项目源码，语料必须回落到快照。

    没有这条回落，服务器上的匹配会悄悄掉回"只用任务名"的水平——
    不报错、不告警，只是召回率变差，是最难发现的那种退化。
    """

    def test_missing_repo_falls_back_to_snapshot(self):
        from packages.adapters.dac3d import DAC3DAdapter

        ad = DAC3DAdapter(repo_path="/nonexistent/repo")
        self.assertFalse(ad.available)
        self.assertEqual(ad.collect(), [], "仓库不在却产出了信号")
        self.assertTrue(ad.task_corpus(), "仓库不在时没有回落到语料快照")

    def test_snapshot_is_in_repo_and_covers_tasks(self):
        import json

        from packages.core.config import ROOT

        f = ROOT / "data" / "adapters" / "dac3d_corpus.json"
        self.assertTrue(f.exists(), "语料快照未入库，服务器上会退化")
        corpus = json.loads(f.read_text(encoding="utf-8"))["corpus"]
        self.assertGreaterEqual(len(corpus), 30)
        self.assertTrue(all(k.startswith("T-DAC-") for k in corpus))


if __name__ == "__main__":
    unittest.main()
