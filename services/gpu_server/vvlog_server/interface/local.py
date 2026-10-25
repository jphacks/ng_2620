"""Local app server mode (ADR-0015, #22): serves the SPA and defends a 127.0.0.1-only listener.

  * LocalGuard      rejects foreign Host headers (DNS rebinding) and foreign Origins (CSRF / cross-site calls)
  * /v1/local/info  tells the SPA it is running in full mode; hands out the API token to same-origin callers only
  * SPA fallback    static files from VVLOG_SPA_DIR, index.html for client-side routes (never outside the directory)
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import __version__

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def bind_local_socket(port: int = 0) -> socket.socket:
    """Listen on 127.0.0.1 only. port 0 picks a free port (read it back with getsockname())."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        sock.close()
        raise
    return sock


def self_origins(port: int) -> tuple[str, ...]:
    return tuple(f"http://{h}:{port}" for h in LOOPBACK_HOSTS)


def _split_host(host: str) -> tuple[str, int | None]:
    """'127.0.0.1:8765' -> ('127.0.0.1', 8765); '[::1]:80' -> ('[::1]', 80). Garbage -> ('', None)."""
    host = host.strip().lower()
    if host.startswith("["):
        name, _, rest = host.partition("]")
        name += "]"
        port_s = rest.removeprefix(":")
    else:
        name, _, port_s = host.partition(":")
    if port_s and not port_s.isdigit():
        return "", None
    return name, int(port_s) if port_s else None


class LocalGuard:
    """ASGI middleware. Runs outside CORS so a rejected request never reaches a route."""

    def __init__(self, app: ASGIApp, port: int, allowed_origins: tuple[str, ...] = ()):
        self.app = app
        self.port = port
        self.origins = frozenset(self_origins(port)) | {o.rstrip("/").lower() for o in allowed_origins}

    def host_ok(self, host: str) -> bool:
        name, port = _split_host(host)
        return name in LOOPBACK_HOSTS and port == self.port

    def origin_ok(self, origin: str) -> bool:
        return origin.rstrip("/").lower() in self.origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        problem = None
        if not self.host_ok(headers.get("host", "")):
            problem = ("HOST_NOT_ALLOWED", "このサーバーは 127.0.0.1 からのアクセスだけを受け付けます")
        elif "origin" in headers and not self.origin_ok(headers["origin"]):
            problem = ("ORIGIN_NOT_ALLOWED", "許可されていないオリジンからのアクセスです")
        if problem is None:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        body = json.dumps({"error": {"code": problem[0], "message": problem[1]}}, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _same_origin(request: Request, port: int) -> bool:
    """True when the caller is the SPA served by this very server (or a non-browser client on this machine)."""
    origin = request.headers.get("origin")
    if origin is not None:
        return origin.rstrip("/").lower() in self_origins(port)
    # Browsers send Sec-Fetch-Site on every request; a cross-site <img>/fetch(no-cors) would say cross-site/same-site.
    return request.headers.get("sec-fetch-site", "same-origin") in ("same-origin", "none")


def mount_local(app: FastAPI, *, port: int, token: str, spa_dir: Path | None, dirs: dict | None = None) -> None:
    @app.get("/v1/local/info", tags=["local"], include_in_schema=False)
    def local_info(request: Request):
        info = {"mode": "local", "version": __version__, "apiVersion": 1, "port": port,
                "spa": bool(spa_dir and (spa_dir / "index.html").is_file())}
        if _same_origin(request, port):
            info["token"] = token
            info["dirs"] = {k: str(v) for k, v in (dirs or {}).items()}  # home-dir paths: not for other origins
        return JSONResponse(info, headers={"Cache-Control": "no-store"})

    root = spa_dir.resolve() if spa_dir else None

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path == "v1" or path.startswith("v1/"):
            return JSONResponse({"error": {"code": "NOT_FOUND", "message": "そのエンドポイントはありません"}}, status_code=404)
        if root is None or not (root / "index.html").is_file():
            return JSONResponse({"error": {"code": "SPA_NOT_BUILT", "message": "画面のビルド成果物が見つかりません。"
                                           "apps/desktop で pnpm build し、VVLOG_SPA_DIR を指定してください"}}, status_code=404)
        rel = unquote(path)
        target = (root / rel).resolve() if rel else root / "index.html"
        if not target.is_relative_to(root):
            return JSONResponse({"error": {"code": "NOT_FOUND", "message": "見つかりません"}}, status_code=404)
        if target.is_file():
            return FileResponse(target)
        if Path(rel).suffix:  # a missing asset is a 404, not the app shell
            return JSONResponse({"error": {"code": "NOT_FOUND", "message": "見つかりません"}}, status_code=404)
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

