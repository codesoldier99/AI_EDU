"""人工审核流程（workflow）。

不另起 BPMN：只覆盖本系统已有的人机协同队列——
题库草案、KP 映射候选、审查发现、错误模式确认、考试/练习人工判分。

业务真相仍在各业务表；本包提供统一排队、认领、状态机与只追加审计。
"""
from __future__ import annotations

from . import service

__all__ = ["service"]
