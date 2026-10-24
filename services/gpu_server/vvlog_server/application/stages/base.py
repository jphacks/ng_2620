"""What a stage sees while it runs, and the Stage interface (designdocs/features/analysis-pipeline §設計)."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ...domain.errors import VvlogError
from ...domain.job import Job
from ..ports import ArtifactStorePort, EventBusPort, JobRepositoryPort, PipelineRunnerPort

PROGRESS_MIN_INTERVAL_S = 0.5


class JobReporter:
    """Keeps job.json and events.jsonl in step: every change to the job is saved, then published."""

    def __init__(self, job: Job, repo: JobRepositoryPort, bus: EventBusPort, store: ArtifactStorePort):
        self.job, self.repo, self.bus, self.store = job, repo, bus, store
        self._last_progress: dict[str, float] = {}

    def _emit(self, event: str, data: dict) -> None:
        self.repo.save(self.job)
        self.bus.publish(self.job.jobId, event, data)

    def stage_started(self, stage: str) -> None:
        self.job.stage_started(stage)
        self._emit("stage_started", {"stage": stage})

    def progress(self, stage: str, progress: float, eta: float | None = None, message: str | None = None,
                 force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_progress.get(stage, 0.0) < PROGRESS_MIN_INTERVAL_S:
            self.job.stage_progress(stage, progress, eta)
            return
        self._last_progress[stage] = now
        self.job.stage_progress(stage, progress, eta)
        data = {"stage": stage, "progress": round(self.job.stage(stage).progress, 4)}
        if eta is not None:
            data["etaSec"] = round(eta, 1)
        if message:
            data["message"] = message
        self._emit("progress", data)

    def artifact(self, name: str, path: Path) -> None:
        a = self.store.publish(self.job, name, path)
        self.job.add_artifact(name)
        self._emit("artifact_ready", a.to_dict())

    def stage_finished(self, stage: str, skipped: bool = False) -> None:
        s = self.job.stage(stage)
        started = s.startedAt
        self.job.stage_finished(stage, skipped)
        dur = 0.0
        if started:
            from datetime import datetime
            dur = (datetime.fromisoformat(s.finishedAt.replace("Z", "+00:00"))
                   - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds()
        self._emit("stage_finished", {"stage": stage, "durationSec": round(dur, 2)})


@dataclass
class JobContext:
    job: Job
    project: dict
    project_root: Path
    work: Path                       # scratch of this job: projects/<pid>/work/<jobId>
    deps: dict[str, Job]             # finished jobs this one builds on, by type ("analyze", "render")
    runner: PipelineRunnerPort
    report: JobReporter
    settings: object
    extra: dict = field(default_factory=dict)

    def work_of(self, job_type: str) -> Path:
        """Scratch directory of a dependency (the analyze job's pout/, occ/ ...)."""
        return self.project_root / "work" / self.deps[job_type].jobId


class Stage(Protocol):
    names: tuple[str, ...]           # the contract stages this step reports (TrackMesh: ("track", "mesh"))

    async def run(self, ctx: JobContext) -> None: ...


class StagePlannerPort(Protocol):
    def stages(self, job_type: str) -> list[Stage]: ...


# --- helpers shared by subprocess stages ------------------------------------------------------------------------------
class PipelineFailed(Exception):
    pass


_OOM = re.compile(r"CUDA out of memory|OutOfMemoryError|CUBLAS_STATUS_ALLOC_FAILED")


def raise_for_exit(code: int, log_path: Path, what: str) -> None:
    if code == 0:
        return
    tail = log_tail(log_path, 40)
    if _OOM.search(tail):
        raise VvlogError("GPU_OOM", "GPU のメモリが足りません", {"process": what, "logTail": tail[-2000:]})
    raise VvlogError("PIPELINE_FAILED", f"{what} が失敗しました（終了コード {code}）",
                     {"process": what, "exitCode": code, "logTail": tail[-2000:]})


def log_tail(path: Path, lines: int = 200) -> str:
    try:
        data = path.read_bytes()[-256 * 1024:].decode("utf-8", "replace")
    except FileNotFoundError:
        return ""
    return "\n".join(data.replace("\r", "\n").splitlines()[-lines:])
