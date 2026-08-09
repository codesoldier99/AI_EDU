"""`- 键：值` 是本系统生成层与表达层之间唯一的接口格式（见 packages/agents/base.Agent.express
的说明）。这个约定原本只用在"提示词怎么拼"上；这里把它反过来用在"回复怎么解析"上——
要求大模型用同样的格式回答，就能用同一条确定性规则把结构化字段抠出来，
不必引入 JSON mode（不是所有底座都稳定支持）也不必相信模型输出的自由格式。
"""
from __future__ import annotations

import re

_FIELD_RE = re.compile(r"^-\s*([一-鿿A-Za-z_]+)\s*[:：]\s*(.+)$", re.M)


def parse_kv_fields(text: str) -> dict[str, str]:
    """把 `- 字段名：内容` 逐行格式解析成 dict。解析不出结构就返回空 dict——
    调用方必须自己决定空结果时怎么降级，不能假装解析总是成功。"""
    return dict(_FIELD_RE.findall(text or ""))


def split_items(value: str, sep: str = "；") -> list[str]:
    """把字段值里用分隔符列出的多条内容拆开，去空去首尾空白。"""
    return [p.strip() for p in (value or "").split(sep) if p.strip()]
