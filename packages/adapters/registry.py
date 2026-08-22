"""适配器注册表。新增项目类型只在此登记，核心代码不改。"""
from __future__ import annotations

from datetime import datetime

from .base import ProjectSignal, persist_signals

_REGISTRY: dict[str, type] = {}


def register(key: str):
    def deco(cls):
        cls.adapter_key = key
        _REGISTRY[key] = cls
        return cls

    return deco


def get_adapter(key: str):
    if key not in _REGISTRY:
        raise KeyError(f"未注册的适配器：{key}；实现 ProjectAdapter 并 @register('{key}')")
    return _REGISTRY[key]()


def list_adapters() -> list[str]:
    return sorted(_REGISTRY)


def collect_all(since: datetime | str | None = None) -> dict[str, int]:
    """遍历库中项目，按 adapter_key 调用对应适配器并落库。"""
    from packages.graph import repo as graph_repo

    out: dict[str, int] = {}
    for p in graph_repo.list_projects():
        try:
            ad = get_adapter(p["adapter_key"])
        except KeyError:
            out[p["code"]] = -1
            continue
        signals = ad.collect(since)
        for s in signals:
            s.project_code = s.project_code or p["code"]
        out[p["code"]] = persist_signals(signals)
    return out


# 导入以触发注册（新增适配器只需在此加一行 import）
from . import agv, dac3d, gitea, vision  # noqa: E402,F401
