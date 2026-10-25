"""/v1/local/weights — 重みの取得(#33)。初回に約 7GB を取得するための API。

  GET  /v1/local/weights           -> 保存先・ファイルごとの状態・空き容量・実行中ジョブ
  POST /v1/local/weights/start     {"hfToken"?: str}  取得(と再開)を始める。202。実行中なら 409 WEIGHTS_BUSY
  POST /v1/local/weights/cancel    止める(.part は残る。次の start で続きから)
  GET  /v1/local/weights/events    SSE: `progress` イベントでジョブの状態を流し、終わったら `done`

HF トークンはこのリクエスト本文でだけ受け取り、メモリ上で取得に使うだけ(保存・ログ・応答への反映をしない)。
開始・キャンセルは、このサーバーが配る画面(同一オリジン)からだけ受け付ける。
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from ..adapters.local_weights import WeightsManager
from ..domain.errors import VvlogError
from .local import _same_origin

TERMINAL = ("succeeded", "failed", "canceled", "idle")


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hfToken: str | None = Field(default=None, repr=False, max_length=512)


def mount_local_weights(app: FastAPI, mgr: WeightsManager, auth: Callable, port: int, poll_s: float = 0.5) -> None:
    def same_app_only(request: Request) -> None:
        if not _same_origin(request, port):
            raise VvlogError("DIALOG_LOCAL_ONLY", "この操作はローカルアプリの画面からだけ行えます")

    r = APIRouter(prefix="/v1/local/weights", tags=["local-weights"], dependencies=[Depends(auth)])

    @r.get("")
    async def status():
        return await run_in_threadpool(mgr.status)

    @r.post("/start", status_code=202, dependencies=[Depends(same_app_only)])
    async def start(body: StartBody):
        return await run_in_threadpool(mgr.start, body.hfToken)

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
