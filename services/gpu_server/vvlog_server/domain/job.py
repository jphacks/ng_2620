"""Job and its stages: the state machine of contracts §3.2. No I/O here."""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .errors import VvlogError

JOB_TYPES = ("analyze", "render", "export")
STAGES = {
    "analyze": ("ingest", "track", "mesh", "occlusion"),
    "render": ("removal", "avatar", "layers"),
    "export": ("composite", "encode"),
}
DEPENDS_ON = {"render": ("analyzeJobId", "analyze"), "export": ("renderJobId", "render")}
TERMINAL = frozenset({"succeeded", "failed", "canceled"})
_TRANSITIONS = {
    "queued": {"running", "canceled", "failed"},      # failed: its dependency failed
    "running": {"succeeded", "failed", "canceled"},
}
_STAGE_STATUS = ("pending", "running", "done", "failed", "skipped")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


@dataclass
class StageState:
    name: str
    status: str = "pending"
    progress: float = 0.0
    etaSec: float | None = None
    startedAt: str | None = None
    finishedAt: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Job:
    jobId: str
    projectId: str
    type: str
    params: dict
    status: str = "queued"
    createdAt: str = field(default_factory=now_iso)
    startedAt: str | None = None
    finishedAt: str | None = None
    dependsOn: str | None = None
    stages: list[StageState] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    error: dict | None = None
    queuePosition: int | None = None

    @classmethod
    def create(cls, project_id: str, job_type: str, params: dict) -> "Job":
        if job_type not in JOB_TYPES:
            raise VvlogError("VALIDATION_FAILED", f"不明なジョブ種別です: {job_type}", {"type": job_type})
        dep = None
        if job_type in DEPENDS_ON:
            key = DEPENDS_ON[job_type][0]
            dep = params.get(key)
            if not dep:
                raise VvlogError("VALIDATION_FAILED", f"{key} が必要です", {"field": key})
        return cls(jobId=new_id("job"), projectId=project_id, type=job_type, params=params, dependsOn=dep,
                   stages=[StageState(s) for s in STAGES[job_type]])

    # --- state machine ---------------------------------------------------------------------------------------------
    def _move(self, to: str) -> None:
        if to not in _TRANSITIONS.get(self.status, set()):
            raise ValueError(f"job {self.jobId}: {self.status} -> {to} is not allowed")
        self.status = to
        if to == "running":
            self.startedAt = now_iso()
        if to in TERMINAL:
            self.finishedAt = now_iso()
        if to != "queued":
            self.queuePosition = None

    def start(self) -> None:
        self._move("running")

    def succeed(self) -> None:
        self._move("succeeded")

    def fail(self, error: dict, stage: str | None = None) -> None:
        self._move("failed")
        self.error = error
        if stage:
            self.stage(stage).status = "failed"
            self.stage(stage).finishedAt = self.finishedAt

    def cancel(self) -> None:
        self._move("canceled")
        self.error = {"code": "CANCELED", "message": "取り消されました"}
        for s in self.stages:
            if s.status == "running":
                s.status, s.finishedAt = "failed", self.finishedAt

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL

    # --- stages ----------------------------------------------------------------------------------------------------
    def stage(self, name: str) -> StageState:
        for s in self.stages:
            if s.name == name:
                return s
        raise KeyError(name)

    def stage_started(self, name: str) -> None:
        s = self.stage(name)
        s.status, s.startedAt, s.progress = "running", now_iso(), 0.0

    def stage_progress(self, name: str, progress: float, eta: float | None = None) -> None:
        s = self.stage(name)
        s.progress = min(1.0, max(s.progress, float(progress)))
        s.etaSec = eta

    def stage_finished(self, name: str, skipped: bool = False) -> None:
        s = self.stage(name)
        s.status, s.finishedAt, s.progress, s.etaSec = ("skipped" if skipped else "done"), now_iso(), 1.0, None

    def add_artifact(self, name: str) -> None:
        if name not in self.artifacts:
            self.artifacts.append(name)

    # --- persistence -----------------------------------------------------------------------------------------------
    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v is not None and k != "stages"}
        d["stages"] = [s.to_dict() for s in self.stages]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        d = dict(d)
        stages = [StageState(**s) for s in d.pop("stages", [])]
        return cls(**d, stages=stages)
