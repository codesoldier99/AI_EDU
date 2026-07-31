"""RAG 与模型网关：图谱结构化过滤、可替换性、调用留痕。"""
from __future__ import annotations

from base import DBTestCase

from packages.core.config import CONFIG
from packages.llm import gateway
from packages.llm.base import LLMResponse
from packages.rag import retriever, store


class FakeClient:
    """假客户端：验证"切换底座模型只改配置，不改业务代码"。"""

    name = "fake-model"

    def complete_sync(self, messages, *, model_hint="", max_tokens=800):
        return LLMResponse(text="FAKE", model_name=self.name, model_version="9.9",
                           prompt_hash="deadbeef", tokens_in=10, tokens_out=1)

    async def complete(self, messages, **kw):
        return self.complete_sync(messages)

    def embed(self, texts):
        from packages.llm.offline import OfflineClient

        return OfflineClient().embed(texts)


class TestRAG(DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        store.index_text("course", "backprop.md", "反向传播讲义",
                         "反向传播依赖链式法则，逐层把导数乘回去。\n\n"
                         "最常见的错误是漏掉激活函数的导数。", ["A", "B"])
        store.index_text("course", "kmeans.md", "聚类讲义",
                         "K 均值把样本划分到最近的簇心，对初始化敏感。", ["C"])
        store.index_text("project", "agv.md", "AGV 文档",
                         "训练服务偏斜会导致线上精度骤降，预处理必须与模型一起打包。", [])

    def test_graph_filter_boosts_related_chunks(self):
        plain = retriever.search("导数", kb="course", top_k=3)
        boosted = retriever.search("导数", kb="course", top_k=3, kp_id=self.kp["B"])
        self.assertTrue(boosted.hits)
        self.assertGreaterEqual(boosted.hits[0]["score"], plain.hits[0]["score"]
                                if plain.hits else 0)
        self.assertIn("A", boosted.expanded_kps)  # 前置点被自动纳入检索范围

    def test_route_selects_kb(self):
        self.assertEqual(retriever.route("ask"), "course")
        self.assertEqual(retriever.route("review"), "project")
        self.assertEqual(retriever.route("credit_map"), "program")

    def test_embedding_records_model_version(self):
        rows = store.load_chunks("course")
        self.assertTrue(all(r["embed_model"] and r["embed_version"] for r in rows))
        self.assertEqual(store.stale_embeddings(), 0)

    def test_stale_embeddings_detected_on_model_switch(self):
        old = CONFIG.embed_version
        try:
            CONFIG.embed_version = "v2"
            self.assertGreater(store.stale_embeddings(), 0)
        finally:
            CONFIG.embed_version = old


class TestGateway(DBTestCase):
    def tearDown(self):
        gateway.set_client(None)
        super().tearDown()

    def test_offline_by_default(self):
        gateway.set_client(None)
        self.assertTrue(gateway.is_degraded())

    def test_swap_model_without_changing_business_code(self):
        gateway.set_client(FakeClient())
        r = gateway.complete("test", "sys", "- 意图：随便")
        self.assertEqual(r.text, "FAKE")
        self.assertFalse(gateway.is_degraded())

    def test_every_call_is_logged(self):
        gateway.set_client(FakeClient())
        gateway.complete("diagnosis", "sys", "- 意图：班级诊断")
        rows = self.db.query("SELECT * FROM llm_call_log")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model_name"], "fake-model")
        self.assertTrue(rows[0]["prompt_hash"])

    def test_offline_client_is_deterministic(self):
        from packages.llm.offline import OfflineClient

        c = OfflineClient()
        msg = [{"role": "system", "content": "s"},
               {"role": "user", "content": "- 意图：苏格拉底追问\n- 目标知识点：反向传播"}]
        self.assertEqual(c.complete_sync(msg).text, c.complete_sync(msg).text)
