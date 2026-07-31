"""换底座模型只改配置，且**不改变系统的任何判断**。

这是本项目最重要的架构主张的可执行版本：
    大模型是嘴，不是脑 —— 换掉嘴，说的话变了，判断一个字都不能变。

用一个本机假底座（`scripts/mock_llm.py` 同款逻辑）走真实的 HTTP 通路，
因此验证的是 OpenAICompatClient 本身，而不是一个内存里的桩。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from base import DBTestCase

from packages.agents.diagnosis import DiagnosisAgent
from packages.agents.task import compute_gap
from packages.core.config import CONFIG
from packages.graph import repo as g
from packages.llm import gateway
from packages.state import tracker


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.endswith("/chat/completions"):
            obj = {
                "model": body.get("model", "mock"),
                "system_fingerprint": "mock-v1",
                "choices": [{"message": {"content": "【假底座措辞】" + str(len(
                    body.get("messages", [])))}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            }
        else:
            obj = {"data": [{"embedding": [0.0] * CONFIG.embed_dim, "index": 0}]}
        payload = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class TestModelSwap(DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

        pid = g.upsert_project("P1", "测试项目", "robot", "demo")
        self.task = g.upsert_task(pid, "T1", "训练一个网络")
        for code in ("A", "B", "C"):
            g.link_task_kp(self.task, self.kp[code], "required")
        for i in range(6):
            tracker.record(self.student, "quiz", self.kp["A"], i % 2 == 0, source="quiz")
            tracker.record(self.student, "quiz", self.kp["B"], False, source="quiz")

    def tearDown(self):
        self.srv.shutdown()
        gateway.set_client(None)
        CONFIG.llm_base_url = CONFIG.llm_api_key = ""
        CONFIG.llm_timeout = 60
        super().tearDown()

    def _switch_on(self):
        CONFIG.llm_base_url = f"http://127.0.0.1:{self.port}/v1"
        CONFIG.llm_api_key = "test"
        CONFIG.llm_model = "mock"
        CONFIG.llm_timeout = 2          # 别让"后端挂掉"的用例干等默认 60 秒
        gateway.set_client(None)

    @staticmethod
    def _stable(plan: dict) -> str:
        p = json.loads(json.dumps(plan, default=str, sort_keys=True))
        p.pop("generated_at", None)
        return json.dumps(p, sort_keys=True, ensure_ascii=False)

    def test_switch_needs_no_code_change(self):
        self.assertTrue(gateway.is_degraded())
        self._switch_on()
        self.assertFalse(gateway.is_degraded())
        r = gateway.complete("test", "sys", "- 意图：随便")
        self.assertIn("假底座措辞", r.text)
        self.assertFalse(r.degraded)

    def test_conclusions_are_identical_across_models(self):
        """同一份数据，离线表达器与真实模型给出的结论必须逐字节相同。"""
        off = DiagnosisAgent().class_report("T", "实验班A")
        off_gap = compute_gap(self.student, self.task)
        self._switch_on()
        on = DiagnosisAgent().class_report("T", "实验班A")
        on_gap = compute_gap(self.student, self.task)

        self.assertEqual(self._stable(off.plan), self._stable(on.plan))
        self.assertEqual(self._stable(off_gap.to_dict()), self._stable(on_gap.to_dict()))
        self.assertEqual(off.confidence, on.confidence)
        self.assertEqual(off.evidence_count, on.evidence_count)
        # 只有措辞变了
        self.assertNotEqual(off.narrative, on.narrative)

    def test_calls_are_logged_with_model_identity(self):
        self._switch_on()
        gateway.complete("diagnosis", "sys", "- 意图：班级诊断")
        row = self.db.query_one(
            "SELECT * FROM llm_call_log WHERE model_name='mock' ORDER BY id DESC")
        self.assertIsNotNone(row)
        self.assertEqual(row["model_version"], "mock-v1")
        self.assertTrue(row["prompt_hash"])
        self.assertEqual(row["degraded"], 0)

    def test_backend_failure_degrades_but_leaves_trace(self):
        """线上抖动不应打断教学流程：降级但留痕，且判断不受影响。"""
        self._switch_on()
        self.srv.shutdown()
        r = gateway.complete("asking", "sys", "- 意图：苏格拉底追问\n- 目标知识点：反向传播")
        self.assertTrue(r.degraded)
        self.assertTrue(r.error)
        self.assertIn("反向传播", r.text)
