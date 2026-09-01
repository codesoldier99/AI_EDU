"""权限管理（RBAC）。

角色与权限是可积累配置；演示令牌与会话令牌都解析到同一套 principal。
业务包不得依赖本包做判分或掌握度——它只服务 apps/api 鉴权层。
"""
from __future__ import annotations

from . import accounts, permissions, rbac, sessions

__all__ = ["accounts", "permissions", "rbac", "sessions"]
