"""模型网关：选型、调用、留痕。业务代码只认这一个入口。"""
from __future__ import annotations

from packages.core.config import CONFIG
from packages.core.db import get_db
from packages.core.timeutil import now_str

from .base import LLMResponse, Vector
from .offline import OfflineClient
from .openai_compat import OpenAICompatClient

_client = None


def get_client():
    global _client
    if _client is None:
        if CONFIG.llm_base_url and CONFIG.llm_api_key:
            _client = OpenAICompatClient(
                CONFIG.llm_base_url, CONFIG.llm_api_key, CONFIG.llm_model, CONFIG.llm_timeout
            )
        else:
            _client = OfflineClient()
    return _client


def set_client(client) -> None:
    """测试或多模型分账时替换客户端。"""
    global _client
    _client = client


def is_degraded() -> bool:
    return isinstance(get_client(), OfflineClient)


def _log(purpose: str, r: LLMResponse) -> None:
    get_db().execute(
        "INSERT INTO llm_call_log(purpose, model_name, model_version, prompt_hash, tokens_in,"
        " tokens_out, latency_ms, degraded, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (purpose, r.model_name, r.model_version, r.prompt_hash, r.tokens_in, r.tokens_out,
         r.latency_ms, int(r.degraded), now_str()),
    )


def complete(purpose: str, system: str, user: str, max_tokens: int = 800) -> LLMResponse:
    """唯一的文本生成入口。purpose 用于成本分账与问题定位。"""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    r = get_client().complete_sync(messages, max_tokens=max_tokens)
    _log(purpose, r)
    return r


def embed(texts: list[str]) -> list[Vector]:
    return get_client().embed(texts)


def usage_summary() -> list[dict]:
    return get_db().query(
        "SELECT purpose, model_name, COUNT(*) AS calls, SUM(tokens_in) AS tokens_in,"
        " SUM(tokens_out) AS tokens_out, SUM(degraded) AS degraded_calls"
        " FROM llm_call_log GROUP BY purpose, model_name ORDER BY calls DESC"
    )
