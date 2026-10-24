"""Request / response models of /v1 (designdocs/architecture/contracts.md §2). packages/contracts/openapi.yaml is
exported from these (scripts/export_openapi.py) and checked by tests/test_contract.py; the mock server uses them too."""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

JobType = Literal["analyze", "render", "export"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]
StageName = Literal["ingest", "track", "mesh", "occlusion", "removal", "avatar", "layers", "composite", "encode"]
StageStatus = Literal["pending", "running", "done", "failed", "skipped"]
ArtifactKind = Literal["json", "zip", "video"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiErrorBody(_M):
    code: str
    message: str
    detail: Optional[dict] = None


class ApiError(_M):
    error: ApiErrorBody


class Health(_M):
    status: Literal["ok"]
    version: str
    apiVersion: Literal[1]


class GpuInfo(_M):
    name: str
    vramMB: int


class Limits(_M):
    maxDurationSec: float
    maxLongSide: int
    maxFps: int
    maxTargets: int
    formats: list[str]


class Capabilities(_M):
    gpu: Optional[GpuInfo] = None
    limits: Limits
    queueLength: int
    backend: str


class Size(_M):
    width: int
    height: int


class MediaMeta(_M):
    durationSec: float
    frameCount: int
    fps: float
    full: Size
    proxy: Size
    proxyScale: int
    hasAudio: bool
    sourceCodec: str


class CreateProjectRequest(_M):
    name: str = Field(min_length=1, max_length=200)


class CreateProjectResponse(_M):
    projectId: str


class Media(_M):
    mediaId: str
    filename: str
    size: int
    sha256: str
    path: str
    meta: MediaMeta
    createdAt: str


class AnalyzeParams(_M):
    mediaId: str
    maxTargets: Optional[int] = Field(default=None, ge=1, le=4)


class CastEntry(_M):
    personId: int = Field(ge=1)
    action: Literal["replace", "erase", "blur"]
    avatarId: str


class Finish(_M):
    smooth: Optional[float] = Field(default=None, ge=0, le=1)
    footLock: Optional[bool] = None
    spring: Optional[bool] = None


class RenderParams(_M):
    analyzeJobId: str
    cast: list[CastEntry] = Field(min_length=1)
    finish: Optional[Finish] = None


class OcclusionOverride(_M):
    model_config = ConfigDict(extra="allow")
    id: str
    kind: Literal["unit", "brush"]
    personId: int
    mode: Literal["front", "behind"]
    unitId: Optional[int] = None
    range: Optional[list[int]] = Field(default=None, min_length=2, max_length=2)
    frame: Optional[int] = None
    mask: Optional[str] = None            # brush: base64 PNG (P1)
    createdAt: Optional[str] = None


class ExportSettings(_M):
    format: Literal["mp4_h264", "prores4444", "png_seq", "layers", "vrma"] = "mp4_h264"
    crf: Optional[int] = Field(default=None, ge=0, le=51)
    audio: Optional[bool] = None
    resolution: Optional[Literal["full", "proxy"]] = None


class ExportParams(_M):
    renderJobId: str
    overrides: list[OcclusionOverride] = []
    settings: ExportSettings = ExportSettings()


class SubmitJobRequest(_M):
    type: JobType
    params: dict


class Stage(_M):
    name: StageName
    status: StageStatus
    progress: float
    etaSec: Optional[float] = None
    startedAt: Optional[str] = None
    finishedAt: Optional[str] = None


class Job(_M):
    jobId: str
    projectId: str
    type: JobType
    status: JobStatus
    createdAt: str
    startedAt: Optional[str] = None
    finishedAt: Optional[str] = None
    queuePosition: Optional[int] = None
    dependsOn: Optional[str] = None
    stages: list[Stage]
    artifacts: list[str]
    error: Optional[ApiErrorBody] = None
    params: Union[AnalyzeParams, RenderParams, ExportParams, dict]


class Project(_M):
    projectId: str
    name: str
    createdAt: str
    media: list[Media]
    jobs: list[Job]


class StartUploadRequest(_M):
    projectId: str
    kind: Literal["source"]
    filename: str
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class StartUploadResponse(_M):
    uploadId: str
    chunkSize: int


class UploadStatus(_M):
    receivedChunks: list[int]
    size: int


class CompleteUploadResponse(_M):
    mediaId: str
    meta: MediaMeta


class AvatarLicense(_M):
    name: Optional[str] = None
    url: Optional[str] = None
    commercial: Optional[str] = None
    avatarPermission: Optional[str] = None
    redistribution: Optional[bool] = None


class AvatarMeta(_M):
    name: str
    authors: list[str]
    version: Literal["0.x", "1.0"]
    license: AvatarLicense
    thumbnail: Optional[str] = None


class Avatar(_M):
    avatarId: str
    meta: AvatarMeta


class QueueItem(_M):
    jobId: str
    projectId: str
    type: JobType
    status: JobStatus
    position: int                         # -1 = running now


class Queue(_M):
    items: list[QueueItem]


class Artifact(_M):
    name: str
    kind: ArtifactKind
    stage: str
    size: int
    sha256: str
    contentType: str
    url: str


class UnitPromptRequest(_M):
    frame: int
    x: float
    y: float
    range: list[int] = Field(min_length=2, max_length=2)


# --- SSE event payloads (contracts §3.3); documented in openapi.yaml under components only ----------------------------
class QueuedEvent(_M):
    position: int


class StageStartedEvent(_M):
    stage: StageName


class ProgressEvent(_M):
    stage: StageName
    progress: float
    etaSec: Optional[float] = None
    message: Optional[str] = None


class StageFinishedEvent(_M):
    stage: StageName
    durationSec: float


class JobFinishedEvent(_M):
    status: Literal["succeeded", "canceled"]


class JobFailedEvent(_M):
    stage: Optional[StageName] = None
    error: ApiErrorBody
    logTail: str


EVENT_MODELS = {"snapshot": Job, "queued": QueuedEvent, "stage_started": StageStartedEvent, "progress": ProgressEvent,
                "artifact_ready": Artifact, "stage_finished": StageFinishedEvent, "job_finished": JobFinishedEvent,
                "job_failed": JobFailedEvent}
