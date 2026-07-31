"""本机 OpenAI 兼容接口的假底座，用于在没有 Key / 没有网络时验证接入通路。

它存在的目的只有一个：证明"换底座只改配置，不改业务代码"这条验收标准是真的，
并且让评审现场可以当着人的面把大模型"接上去"，再看结论变没变。

    python3 scripts/mock_llm.py --port 8910          # 另开一个终端
    LLM_BASE_URL=http://127.0.0.1:8910/v1 LLM_API_KEY=demo \\
    LLM_MODEL=mock-qwen python3 aiedu.py dev

它按 prompt 里的结构化字段生成措辞（风格与真实模型接近、略啰嗦），
但**绝不产生任何新的事实判断**——这正是被验证的那件事。
"""
from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import _bootstrap  # noqa: F401
from packages.llm.offline import OfflineClient, hashed_embedding

_offline = OfflineClient()

PREFIX = {
    "苏格拉底追问": "好，我们不急着写答案。",
    "班级诊断": "先说结论。",
    "学习副驾驶": "这个现象我见过几次，通常出在三个地方。",
    "教师简报": "本周概况如下。",
    "拉取式学习建议": "从当前任务倒推，",
}


def _fields(user: str) -> dict:
    return dict(re.findall(r"^-\s*([一-鿿A-Za-z_]+)\s*[:：]\s*(.+)$", user, flags=re.M))


def _compose(messages: list[dict]) -> str:
    """在离线模板之上加一层"更像真模型"的措辞。事实内容完全来自模板。"""
    user = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
    body = _offline.complete_sync(messages).text
    intent = _fields(user).get("意图", "")
    head = PREFIX.get(intent, "")
    tail = "\n\n（以上判断来自系统的确定性计算，本模型只负责表达。）"
    return f"{head}\n\n{body}{tail}" if head else body + tail


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"  ← {self.path}")

    def _json(self, obj, status=200):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.endswith("/chat/completions"):
            text = _compose(body.get("messages", []))
            return self._json({
                "model": body.get("model", "mock-qwen"),
                "system_fingerprint": "mock-v1",
                "choices": [{"message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 128, "completion_tokens": len(text) // 2},
            })
        if self.path.endswith("/embeddings"):
            texts = body.get("input") or []
            if isinstance(texts, str):
                texts = [texts]
            return self._json({
                "model": body.get("model", "mock-embed"),
                "data": [{"embedding": hashed_embedding(t), "index": i}
                         for i, t in enumerate(texts)],
            })
        self._json({"error": f"未实现的路径 {self.path}"}, 404)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8910)
    a = ap.parse_args()
    print(f"假底座已启动：http://127.0.0.1:{a.port}/v1   (Ctrl-C 停止)")
    print("接法：LLM_BASE_URL=http://127.0.0.1:%d/v1 LLM_API_KEY=demo "
          "LLM_MODEL=mock-qwen python3 aiedu.py dev" % a.port)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
