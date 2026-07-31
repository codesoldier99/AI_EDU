"""大模型统一封装（可替换层）。

铁律 2/3：大模型是嘴，不是脑。
- 业务代码禁止直接 import openai 等 SDK，一律经本包。
- 每次调用必须记录 model_name / model_version / prompt_hash / token_usage。
- embedding 必须记录模型版本，换模型可批量重算。
验收标准：切换底座模型只改配置，不改业务代码。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class LLMResponse:
    text: str = ""
    model_name: str = ""
    model_version: str = ""
    prompt_hash: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    degraded: bool = False   # True 表示走了离线降级表达器
    error: str = ""


@dataclass
class Vector:
    values: list[float] = field(default_factory=list)
    model: str = ""
    version: str = ""


@runtime_checkable
class LLMClient(Protocol):
    name: str

    async def complete(self, messages: list[dict], *, model_hint: str = "",
                       max_tokens: int = 800) -> LLMResponse: ...

    def complete_sync(self, messages: list[dict], *, model_hint: str = "",
                      max_tokens: int = 800) -> LLMResponse: ...

    def embed(self, texts: list[str]) -> list[Vector]: ...


def prompt_hash(messages: list[dict]) -> str:
    raw = "\n".join(f"{m.get('role')}::{m.get('content')}" for m in messages)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def rough_tokens(text: str) -> int:
    """粗略 token 估计：中文按字，英文按 4 字符。仅用于成本观测。"""
    zh = sum(1 for ch in text if "一" <= ch <= "鿿")
    return zh + max(0, (len(text) - zh)) // 4
