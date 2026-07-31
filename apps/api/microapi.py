"""极简 HTTP 框架（FastAPI 风格的路由与依赖注入，标准库实现）。

为什么不直接用 FastAPI：本项目要求"零外部依赖也能本地演示"。
接口风格刻意与 FastAPI 保持一致（装饰器路由、路径参数、JSON 出入参），
将来换成 FastAPI 时，路由函数签名可原样迁移。
"""
from __future__ import annotations

import json
import mimetypes
import re
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse


class HTTPError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Request:
    method: str = "GET"
    path: str = "/"
    query: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    path_params: dict = field(default_factory=dict)
    body: bytes = b""
    principal: dict = field(default_factory=dict)   # 鉴权结果

    @property
    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception:
            raise HTTPError(400, "请求体不是合法 JSON")

    def q(self, key: str, default=None):
        v = self.query.get(key)
        return v[0] if isinstance(v, list) and v else (default if v is None else v)

    def qi(self, key: str, default: int | None = None) -> int | None:
        v = self.q(key)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default


class App:
    def __init__(self, static_dir: Path | None = None) -> None:
        self.routes: list[tuple[str, re.Pattern, Callable, dict]] = []
        self.static_dir = static_dir
        self.middlewares: list[Callable] = []

    def route(self, method: str, path: str, **meta):
        # /api/students/{sid}/mastery -> 正则
        pattern = re.compile(
            "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$"
        )

        def deco(fn):
            self.routes.append((method.upper(), pattern, fn, meta))
            return fn

        return deco

    def get(self, path: str, **meta):
        return self.route("GET", path, **meta)

    def post(self, path: str, **meta):
        return self.route("POST", path, **meta)

    def use(self, fn: Callable) -> None:
        self.middlewares.append(fn)

    # ---- 分发 ----
    def dispatch(self, req: Request):
        for method, pattern, fn, meta in self.routes:
            if method != req.method:
                continue
            m = pattern.match(req.path)
            if not m:
                continue
            req.path_params = m.groupdict()
            for mw in self.middlewares:
                mw(req, meta)
            return fn(req)
        raise HTTPError(404, f"未找到路由 {req.method} {req.path}")

    def serve(self, host: str, port: int) -> None:
        app = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "AIEDU/1.0"

            def log_message(self, fmt, *args):  # 静默访问日志，保留错误输出
                pass

            def _send(self, status: int, payload: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Auth-Token")
                self.end_headers()
                self.wfile.write(payload)

            def _json(self, status: int, obj) -> None:
                self._send(status, json.dumps(obj, ensure_ascii=False, default=str)
                           .encode("utf-8"), "application/json; charset=utf-8")

            def do_OPTIONS(self):  # noqa: N802
                self._send(204, b"", "text/plain")

            def do_GET(self):  # noqa: N802
                self._handle("GET")

            def do_POST(self):  # noqa: N802
                self._handle("POST")

            def _handle(self, method: str) -> None:
                u = urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                req = Request(
                    method=method, path=u.path, query=parse_qs(u.query),
                    headers={k.lower(): v for k, v in self.headers.items()}, body=body,
                )
                if method == "GET" and not u.path.startswith("/api/"):
                    return self._static(u.path)
                try:
                    result = app.dispatch(req)
                    if isinstance(result, tuple):
                        status, obj = result
                    else:
                        status, obj = 200, result
                    self._json(status, obj)
                except HTTPError as e:
                    self._json(e.status, {"error": e.message})
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    self._json(500, {"error": f"{type(e).__name__}: {e}"})

            def _static(self, path: str) -> None:
                if app.static_dir is None:
                    return self._json(404, {"error": "no static dir"})
                rel = path.lstrip("/") or "index.html"
                f = (app.static_dir / rel).resolve()
                if not str(f).startswith(str(app.static_dir.resolve())) or not f.exists():
                    f = app.static_dir / "index.html"
                ctype = mimetypes.guess_type(str(f))[0] or "text/plain"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                self._send(200, f.read_bytes(), ctype)

        httpd = ThreadingHTTPServer((host, port), Handler)
        print(f"  → http://{host}:{port}/   (Ctrl-C 停止)")
        httpd.serve_forever()
