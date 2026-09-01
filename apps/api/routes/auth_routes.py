"""鉴权与 RBAC 路由：登录换不透明令牌、谁在、权限矩阵、账号同步。"""
from __future__ import annotations

from packages.auth import accounts, rbac, sessions

from .. import auth
from ..microapi import App, HTTPError, Request


def register(app: App) -> None:
    @app.get("/api/auth/demo", public=True)
    def demo_accounts(req: Request):
        """演示可用身份清单（不含密钥）。本地/课堂演示入口。"""
        return {
            "note": (
                "演示令牌：teacher:T001 / student:<学号> / admin:A001；"
                "或 POST /api/auth/login 换取 session:<不透明串>（推荐）。"
            ),
            "legacy_tokens": [
                {"token": "teacher:T001", "role": "teacher", "name": "张导师"},
                {"token": "teacher:T003", "role": "teacher", "name": "王主任（全班）"},
                {"token": "admin:A001", "role": "admin", "name": "教务演示"},
                {"token": "student:2026001", "role": "student", "name": "演示学号示例"},
            ],
            "login": {
                "method": "POST",
                "path": "/api/auth/login",
                "body": {"kind": "teacher|student|admin", "ident": "T001"},
            },
        }

    @app.post("/api/auth/login", public=True)
    def login(req: Request):
        b = req.json or {}
        kind = (b.get("kind") or "").strip()
        ident = (b.get("ident") or "").strip()
        if not kind or not ident:
            raise HTTPError(400, "需要 kind 与 ident")
        try:
            out = sessions.login(kind, ident, ttl_hours=b.get("ttl_hours", 72))
        except ValueError as exc:
            raise HTTPError(401, str(exc)) from exc
        return out

    @app.post("/api/auth/logout")
    def logout(req: Request):
        token = req.headers.get("x-auth-token") or req.q("token") or ""
        if token.startswith("session:"):
            sessions.revoke(token)
        return {"ok": True}

    @app.get("/api/auth/matrix", role="teacher")
    def matrix(req: Request):
        auth.require_perm(req, "workflow.read")
        # 教师可看矩阵；改矩阵仅 admin
        return rbac.list_matrix()

    @app.post("/api/auth/sync", perm="admin.seed")
    def sync_accounts(req: Request):
        rbac.ensure_matrix()
        return accounts.sync_from_legacy()

    @app.get("/api/auth/accounts", perm="admin.ops")
    def list_acc(req: Request):
        return {"items": accounts.list_accounts(req.q("role"), req.qi("limit", 200) or 200)}
