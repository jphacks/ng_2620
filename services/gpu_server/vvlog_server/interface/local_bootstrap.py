"""/v1/local/bootstrap — 初回セットアップ(環境 B の作成、#32)。

  GET  /v1/local/bootstrap           -> 準備できているか・GPU とその判定・手順ごとの完了・実行中ジョブ(ログ末尾つき)
  POST /v1/local/bootstrap/start     {"force"?: bool}  作成(と再開)を始める。202。実行中なら 409 BOOTSTRAP_BUSY、
                                     GPU 非対応なら 409 BOOTSTRAP_UNSUPPORTED(force で押し切れるが画面は出さない)
  POST /v1/local/bootstrap/cancel    止める(終わった手順は残る。次の start で続きから)
  GET  /v1/local/bootstrap/events    SSE: `progress` でジョブの状態とログを流し、終わったら `done`

開始・キャンセルは、このサーバーが配る画面(同一オリジン)からだけ受け付ける。
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from ..adapters.local_bootstrap import BootstrapManager
from ..domain.errors import VvlogError
from .local import _same_origin

TERMINAL = ("succeeded", "failed", "canceled", "idle")


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False


def mount_local_bootstrap(app: FastAPI, mgr: BootstrapManager, auth: Callable, port: int, poll_s: float = 0.5) -> None:
    def same_app_only(request: Request) -> None:
        if not _same_origin(request, port):
            raise VvlogError("DIALOG_LOCAL_ONLY", "この操作はローカルアプリの画面からだけ行えます")

    r = APIRouter(prefix="/v1/local/bootstrap", tags=["local-bootstrap"], dependencies=[Depends(auth)])

    @r.get("")
    async def status():
        return await run_in_threadpool(mgr.status)

    @r.post("/start", status_code=202, dependencies=[Depends(same_app_only)])
    async def start(body: StartBody | None = None):
        return await run_in_threadpool(mgr.start, bool(body and body.force))

    @r.post("/cancel", dependencies=[Depends(same_app_only)])
    async def cancel():
        return mgr.cancel()

    @r.get("/events")
    async def events(request: Request):
        async def gen():
            last = None
            while True:
                job = mgr.job()
                if job != last:
                    yield {"event": "progress", "data": json.dumps(job, ensure_ascii=False)}
                    last = job
                if job.get("state") in TERMINAL:
                    yield {"event": "done", "data": json.dumps(job, ensure_ascii=False)}
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(poll_s)
        return EventSourceResponse(gen())

    app.include_router(r)
