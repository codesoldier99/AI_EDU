"""OpenAI 兼容接口客户端（Qwen / DeepSeek / vLLM / Ollama 等均可）。

只用标准库 urllib，避免为一个 HTTP 调用引入 SDK 依赖，
也顺带保证了"换底座只改配置"这条验收标准不被 SDK 绑架。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from packages.core.config import CONFIG

from .base import LLMResponse, Vector, prompt_hash, rough_tokens
from .offline import OfflineClient, hashed_embedding


class OpenAICompatClient:
    name = "openai-compat"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._fallback = OfflineClient()

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def complete_sync(self, messages: list[dict], *, model_hint: str = "",
                      max_tokens: int = 800) -> LLMResponse:
        t0 = time.time()
        body = {
            "model": model_hint or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        try:
            data = self._post("/chat/completions", body)
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return LLMResponse(
                text=text,
                model_name=data.get("model", body["model"]),
                model_version=str(data.get("system_fingerprint", "")),
                prompt_hash=prompt_hash(messages),
                tokens_in=usage.get("prompt_tokens", rough_tokens(json.dumps(messages))),
                tokens_out=usage.get("completion_tokens", rough_tokens(text)),
                latency_ms=int((time.time() - t0) * 1000),
            )
        except Exception as exc:  # 线上抖动不应打断教学流程，降级但留痕
            r = self._fallback.complete_sync(messages, max_tokens=max_tokens)
            r.error = f"{type(exc).__name__}: {exc}"
            return r

    async def complete(self, messages: list[dict], *, model_hint: str = "",
                       max_tokens: int = 800) -> LLMResponse:
        return self.complete_sync(messages, model_hint=model_hint, max_tokens=max_tokens)

    def embed(self, texts: list[str]) -> list[Vector]:
        try:
            data = self._post("/embeddings", {"model": CONFIG.embed_model, "input": texts})
            return [
                Vector(values=d["embedding"], model=CONFIG.embed_model,
                       version=CONFIG.embed_version)
                for d in data["data"]
            ]
        except Exception:
            return [
                Vector(values=hashed_embedding(t), model="hashed-char-bigram-tfidf",
                       version="v1")
                for t in texts
            ]
