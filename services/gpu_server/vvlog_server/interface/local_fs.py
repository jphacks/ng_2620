"""/v1/local/fs — the HTTP face of LocalFs (#23). Replaces the fs_* Tauri commands.

  GET  /exists?path=            -> {"exists": bool}
  GET  /stat?path=              -> {"size", "mtimeMs", "isDir"}
  GET  /text?path=              -> UTF-8 text            PUT /text?path=&private=   (atomic)
  GET  /raw?path=               -> bytes, Range-capable  PUT /raw?path=             (atomic, streamed to disk)
                                   (`t=<token>` is accepted here so <img>/<video> can load it without headers)
  POST /append?path=&expectedSize=   append a chunk (resumable downloads, #25)
  POST /rename {src, dest}      atomic move; replaces dest (finishes a .part download)
  GET  /list?path=              -> [names]               POST /copy /mkdir /remove /trash /unzip  (JSON bodies)
"""
from __future__ import annotations

import mimetypes
import secrets
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict

from ..adapters.local_fs import LocalFs
from ..domain.errors import VvlogError
from .ranges import range_response


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CopyBody(_Body):
    src: str
    dest: str


class PathBody(_Body):
    path: str


class UnzipBody(_Body):
    zipPath: str
    destDir: str


def mount_local_fs(app: FastAPI, fs: LocalFs, auth: Callable, token: str) -> None:
    def raw_auth(authorization: str | None = Header(default=None), t: str | None = Query(default=None)) -> None:
        given = (authorization or "").removeprefix("Bearer ").strip() or (t or "")
        if not token or not secrets.compare_digest(given.encode(), token.encode()):
            raise VvlogError("UNAUTHORIZED", "トークンが違います")

    r = APIRouter(prefix="/v1/local/fs", tags=["local-fs"], dependencies=[Depends(auth)])

    @r.get("/exists")
    def exists(path: str):
        return {"exists": fs.exists(path)}

    @r.get("/stat")
    def stat(path: str):
        return fs.stat(path)

    @r.get("/text", response_class=PlainTextResponse)
    def read_text(path: str):
        return fs.read_text(path)

    @r.put("/text", status_code=204)
    async def write_text(path: str, request: Request, private: bool = False):
        fs.write_text(path, (await request.body()).decode("utf-8"), private)
        return Response(status_code=204)

    @r.put("/raw", status_code=204)
    async def write_raw(path: str, request: Request):
        w = fs.begin_write(path)
        try:
            async for chunk in request.stream():
                await run_in_threadpool(w.write, chunk)
            await run_in_threadpool(w.commit)
        except BaseException:
            w.abort()
            raise
        return Response(status_code=204)

    @r.post("/append")
    async def append(path: str, request: Request, expectedSize: int | None = Query(default=None, ge=0)):
        data = await request.body()
        return {"size": await run_in_threadpool(fs.append, path, data, expectedSize)}

    @r.get("/list")
    def list_dir(path: str):
        return fs.list_dir(path)

    @r.post("/copy", status_code=204)
    def copy(body: CopyBody):
        fs.copy_file(body.src, body.dest)
        return Response(status_code=204)

    @r.post("/rename", status_code=204)
    def rename(body: CopyBody):
        fs.rename(body.src, body.dest)
        return Response(status_code=204)

    @r.post("/mkdir", status_code=204)
    def mkdir(body: PathBody):
        fs.ensure_dir(body.path)
        return Response(status_code=204)

    @r.post("/remove", status_code=204)
    def remove(body: PathBody):
        fs.remove(body.path)
        return Response(status_code=204)

    @r.post("/trash", status_code=204)
    def trash(body: PathBody):
        fs.move_to_trash(body.path)
        return Response(status_code=204)

    @r.post("/unzip", status_code=204)
    def unzip(body: UnzipBody):
        fs.unzip(body.zipPath, body.destDir)
        return Response(status_code=204)

    app.include_router(r)

    # kept out of the router above: it accepts the token as a query parameter too (see module docstring)
    @app.get("/v1/local/fs/raw", tags=["local-fs"], dependencies=[Depends(raw_auth)])
    def read_raw(path: str, range: str | None = Header(default=None)):
        p = fs.file_path(path)
        return range_response(p, mimetypes.guess_type(p.name)[0] or "application/octet-stream", range)
