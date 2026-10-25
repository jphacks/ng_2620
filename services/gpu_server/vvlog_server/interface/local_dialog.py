"""/v1/local/dialog/{open-files|open-dir|save} (#46). Replaces tauri-plugin-dialog.

The dialog appears on this machine's screen, so only the app served by this server may open it: a request with a foreign
Origin (the GitHub Pages page, a remote tab) is refused even when it somehow holds the token. What the user picks is added
to the file API's allowed set (a folder becomes a root, a file is allowed on its own) — the one way to widen it.
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict

from ..adapters.local_fs import LocalRoots
from ..adapters.native_dialog import NativeDialog, build_request
from ..domain.errors import VvlogError
from .local import _same_origin


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    extensions: list[str]


class DialogBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: list[Filter] = []
    multiple: bool = False
    defaultPath: str | None = None
    title: str | None = None


def mount_local_dialog(app: FastAPI, dialog: NativeDialog, roots: LocalRoots, auth: Callable, port: int) -> None:
    def same_app_only(request: Request) -> None:
        if not _same_origin(request, port):
            raise VvlogError("DIALOG_LOCAL_ONLY", "このダイアログはローカルアプリからだけ開けます")

    r = APIRouter(prefix="/v1/local/dialog", tags=["local-dialog"], dependencies=[Depends(auth), Depends(same_app_only)])

    async def ask(kind: str, body: DialogBody) -> list[str]:
        req = build_request(kind, filters=[f.model_dump() for f in body.filters], multiple=body.multiple,
                            default_path=body.defaultPath, title=body.title)
        return await run_in_threadpool(dialog.ask, req)

    @r.post("/open-files")
    async def open_files(body: DialogBody):
        paths = await ask("open-files", body)
        paths = paths if body.multiple else paths[:1]
        for p in paths:
            roots.grant_user(p, is_dir=False)
        return {"paths": paths}

    @r.post("/open-dir")
    async def open_dir(body: DialogBody):
        paths = (await ask("open-dir", body))[:1]
        for p in paths:
            roots.grant_user(p, is_dir=True)
        return {"path": paths[0] if paths else None}

    @r.post("/save")
    async def save(body: DialogBody):
        paths = (await ask("save", body))[:1]
        for p in paths:
            roots.grant_user(p, is_dir=False)
        return {"path": paths[0] if paths else None}

    app.include_router(r)
