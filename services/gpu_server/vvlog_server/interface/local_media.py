"""/v1/local/media/probe and /v1/local/hash/sha256 (#24). Replace the media_probe / hash_sha256 Tauri commands.

  POST /v1/local/media/probe  {"path"}  -> {durationSec, width, height, rotation, codec, fps, hasAudio, sizeBytes}
  POST /v1/local/hash/sha256  {"path"}  -> {"sha256"}
       with `Accept: text/event-stream`: `progress` events {"ratio"} (0..1), then one `done` event {"sha256"}
"""
from __future__ import annotations

import json
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, Header
from fastapi.concurrency import iterate_in_threadpool, run_in_threadpool
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from ..adapters.local_media import LocalMedia


class PathBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


def mount_local_media(app: FastAPI, media: LocalMedia, auth: Callable) -> None:
    r = APIRouter(prefix="/v1/local", tags=["local-media"], dependencies=[Depends(auth)])

    @r.post("/media/probe")
    def probe(body: PathBody):
        return media.probe(body.path)

    @r.post("/hash/sha256")
    async def sha256(body: PathBody, accept: str | None = Header(default=None)):
        steps = media.sha256(body.path)
        if "text/event-stream" not in (accept or ""):
            digest = ""
            async for step in iterate_in_threadpool(steps):
                digest = step
            return {"sha256": digest}
        first = await run_in_threadpool(next, steps)  # path errors surface as a normal JSON error, not a broken stream

        async def events():
            step = first
            while True:
                if isinstance(step, float):
                    yield {"event": "progress", "data": json.dumps({"ratio": step})}
                else:
                    yield {"event": "done", "data": json.dumps({"sha256": step})}
                    return
                step = await run_in_threadpool(next, steps)

        return EventSourceResponse(events())

    app.include_router(r)
