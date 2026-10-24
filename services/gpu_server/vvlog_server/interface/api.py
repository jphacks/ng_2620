"""FastAPI routes of /v1 (contracts §2.1). Thin: parse, call a use case, shape the response."""
from __future__ import annotations

import asyncio
import json
import secrets

from fastapi import APIRouter, Depends, FastAPI, File, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from .. import __version__
from ..application.deps import Deps
from ..application.usecases import avatars, jobs, projects, uploads
from ..domain.errors import VvlogError
from . import schemas as S
from .ranges import range_response

HEARTBEAT_S = 15
CORS_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost", "http://localhost:1420"]
_PARAMS = {"analyze": S.AnalyzeParams, "render": S.RenderParams, "export": S.ExportParams}
_ERR = {"model": S.ApiError}


def build_app(deps: Deps, lifespan=None) -> FastAPI:
    app = FastAPI(title="vvlog GPU server", version=__version__, lifespan=lifespan,
                  description="designdocs/architecture/contracts.md")
    app.state.deps = deps
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["Content-Range", "Accept-Ranges"])

    @app.exception_handler(VvlogError)
    async def _vvlog_error(_req, e: VvlogError):
        status = e.http_status if e.http_status >= 400 else 409
        return JSONResponse({"error": e.to_dict()}, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req, e: RequestValidationError):
        return JSONResponse({"error": {"code": "VALIDATION_FAILED", "message": "パラメータが不正です",
                                       "detail": {"errors": json.loads(json.dumps(e.errors(), default=str))}}},
                            status_code=422)

    def auth(authorization: str | None = Header(default=None)) -> None:
        token = deps.settings.api_token
        given = (authorization or "").removeprefix("Bearer ").strip()
        if not token or not secrets.compare_digest(given.encode(), token.encode()):
            raise VvlogError("UNAUTHORIZED", "トークンが違います")

    @app.get("/v1/health", response_model=S.Health, tags=["connection"])
    def health():
        return {"status": "ok", "version": __version__, "apiVersion": 1}

    r = APIRouter(prefix="/v1", dependencies=[Depends(auth)], responses={401: _ERR, 404: _ERR, 422: _ERR})

    @r.get("/capabilities", response_model=S.Capabilities, tags=["connection"])
    def capabilities():
        return jobs.capabilities(deps)

    # --- projects ----------------------------------------------------------------------------------------------
    @r.post("/projects", response_model=S.CreateProjectResponse, status_code=201, tags=["projects"])
    def create_project(body: S.CreateProjectRequest):
        return projects.create_project(deps, body.name)

    @r.get("/projects/{projectId}", response_model=S.Project, tags=["projects"])
    def get_project(projectId: str):
        return projects.get_project(deps, projectId)

    @r.delete("/projects/{projectId}", status_code=204, tags=["projects"])
    async def delete_project(projectId: str):
        await projects.delete_project(deps, projectId)
        return Response(status_code=204)

    # --- uploads -----------------------------------------------------------------------------------------------
    @r.post("/uploads", response_model=S.StartUploadResponse, status_code=201, tags=["uploads"])
    def start_upload(body: S.StartUploadRequest):
        return uploads.start_upload(deps, body.projectId, body.kind, body.filename, body.size, body.sha256)

    @r.put("/uploads/{uploadId}/chunks/{index}", response_model=S.UploadStatus, tags=["uploads"],
           openapi_extra={"requestBody": {"required": True, "content": {
               "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
    async def put_chunk(uploadId: str, index: int, request: Request):
        data = await request.body()
        return await asyncio.to_thread(uploads.put_chunk, deps, uploadId, index, data)

    @r.get("/uploads/{uploadId}", response_model=S.UploadStatus, tags=["uploads"])
    def get_upload(uploadId: str):
        return uploads.get_upload(deps, uploadId)

    @r.post("/uploads/{uploadId}/complete", response_model=S.CompleteUploadResponse, tags=["uploads"],
            responses={409: _ERR})
    async def complete_upload(uploadId: str):
        return await asyncio.to_thread(uploads.complete_upload, deps, uploadId)

    # --- avatars -----------------------------------------------------------------------------------------------
    @r.post("/avatars", response_model=S.Avatar, status_code=201, tags=["avatars"])
    async def upload_avatar(file: UploadFile = File(...)):
        data = await file.read()
        return await asyncio.to_thread(avatars.upload_avatar, deps, data, file.filename or "avatar.vrm")

    @r.get("/avatars/{avatarId}", response_model=S.Avatar, tags=["avatars"])
    def get_avatar(avatarId: str):
        return avatars.get_avatar(deps, avatarId)

    # --- jobs --------------------------------------------------------------------------------------------------
    @r.post("/projects/{projectId}/jobs", response_model=S.Job, status_code=201, tags=["jobs"],
            responses={409: _ERR, 501: _ERR})
    def submit_job(projectId: str, body: S.SubmitJobRequest):
        try:
            params = _PARAMS[body.type].model_validate(body.params).model_dump(exclude_none=True)
        except ValidationError as e:
            raise VvlogError("VALIDATION_FAILED", "パラメータが不正です",
                             {"errors": json.loads(e.json(include_url=False))})
        return jobs.submit_job(deps, projectId, body.type, params)

    @r.get("/projects/{projectId}/jobs", response_model=list[S.Job], tags=["jobs"])
    def list_jobs(projectId: str):
        return jobs.list_jobs(deps, projectId)

    @r.get("/jobs/{jobId}", response_model=S.Job, tags=["jobs"])
    def get_job(jobId: str):
        return jobs.get_job(deps, jobId)

    @r.get("/queue", response_model=S.Queue, tags=["jobs"])
    def get_queue():
        return jobs.queue(deps)

    @r.post("/jobs/{jobId}/cancel", response_model=S.Job, tags=["jobs"])
    async def cancel_job(jobId: str):
        return await jobs.cancel_job(deps, jobId)

    @r.get("/jobs/{jobId}/events", tags=["jobs"], response_class=Response,
           responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}},
                            "description": "SSE: snapshot, then the events of contracts §3.3"}})
    async def job_events(jobId: str, last_event_id: str | None = Header(default=None)):
        snapshot = jobs.get_job(deps, jobId)
        after = int(last_event_id) if (last_event_id or "").isdigit() else 0
        return EventSourceResponse(_sse(deps, jobId, snapshot, after), ping=3600)

    @r.get("/jobs/{jobId}/artifacts", response_model=list[S.Artifact], tags=["artifacts"])
    def list_artifacts(jobId: str):
        return jobs.list_artifacts(deps, jobId)

    @r.get("/jobs/{jobId}/artifacts/{name}", tags=["artifacts"], response_class=Response,
           responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string",
                                                                               "format": "binary"}}}},
                      206: {"description": "Partial content (Range)"}})
    def get_artifact(jobId: str, name: str, range: str | None = Header(default=None)):
        path, a = jobs.open_artifact(deps, jobId, name)
        return range_response(path, a["contentType"], range)

    @r.get("/jobs/{jobId}/logs", response_class=PlainTextResponse, tags=["jobs"])
    def job_logs(jobId: str, tail: int = Query(200, ge=1, le=5000)):
        return jobs.job_logs(deps, jobId, tail)

    @r.post("/projects/{projectId}/units:prompt", tags=["occlusion"], responses={501: _ERR})
    def units_prompt(projectId: str, body: S.UnitPromptRequest):
        raise VvlogError("NOT_IMPLEMENTED", "取りこぼした遮蔽物の追加は今後対応です（P1）")

    @r.get("/me", tags=["enterprise"], responses={501: _ERR})
    def me():
        raise VvlogError("NOT_IMPLEMENTED", "ユーザー管理はスコープ外です（ADR-0010）")

    app.include_router(r)
    return app


async def _sse(deps: Deps, job_id: str, snapshot: dict, after: int):
    """snapshot (no id, so Last-Event-ID keeps pointing at real events), the stored events after `after`, live ones,
    a heartbeat every 15 s; ends after the job's terminal event."""
    yield {"event": "snapshot", "data": json.dumps(snapshot, ensure_ascii=False)}
    q: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for e in deps.bus.subscribe(job_id, after):
                await q.put(e)
        finally:
            await q.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                e = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
            except asyncio.TimeoutError:
                yield {"event": "heartbeat", "data": "{}"}
                continue
            if e is None:
                return
            yield {"id": str(e["id"]), "event": e["event"], "data": json.dumps(e["data"], ensure_ascii=False)}
    finally:
        task.cancel()
