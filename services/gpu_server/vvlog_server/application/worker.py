"""One worker per GPU: takes queued jobs in FIFO order once their dependency succeeded, runs their stages.

designdocs/adr/0014 §2, features/gpu-server-runtime 受け入れ条件（キュー・取り消し・再起動）."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from ..domain.errors import VvlogError
from ..domain.job import DEPENDS_ON, Job
from .ports import ArtifactStorePort, EventBusPort, JobQueuePort, JobRepositoryPort, PipelineRunnerPort, \
    ProjectRepositoryPort
from .stages.base import JobContext, JobReporter, StagePlannerPort, log_tail

log = logging.getLogger("vvlog.worker")


class Canceled(Exception):
    pass


class Worker:
    def __init__(self, *, jobs: JobRepositoryPort, queue: JobQueuePort, bus: EventBusPort, store: ArtifactStorePort,
                 projects: ProjectRepositoryPort, planner: StagePlannerPort,
                 runner_factory: Callable[[], PipelineRunnerPort], settings: object, gpu_id: str = "0"):
        self.jobs, self.queue, self.bus, self.store, self.projects = jobs, queue, bus, store, projects
        self.planner, self.runner_factory, self.settings, self.gpu_id = planner, runner_factory, settings, gpu_id
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.current: tuple[Job, PipelineRunnerPort, asyncio.Task] | None = None
        self._canceling: set[str] = set()

    # --- lifecycle -------------------------------------------------------------------------------------------------
    def recover(self) -> None:
        """At start-up: running jobs died with the old process; queued ones go back in the queue in order."""
        for job in sorted(self.jobs.list(), key=lambda j: j.createdAt):
            if job.status == "running":
                job.fail({"code": "INTERRUPTED", "message": "サーバーの再起動で中断しました"},
                         next((s.name for s in job.stages if s.status == "running"), None))
                self.jobs.save(job)
                self.bus.publish(job.jobId, "job_failed", {"stage": None, "error": job.error, "logTail": ""})
            elif job.status == "queued":
                self.queue.enqueue(job.jobId)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name=f"worker-gpu{self.gpu_id}")

    async def stop(self) -> None:
        if self.current:
            await self.cancel(self.current[0].jobId)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        self._wake.set()

    # --- queue -----------------------------------------------------------------------------------------------------
    def publish_positions(self) -> None:
        for i, jid in enumerate(self.queue.items()):
            job = self.jobs.get(jid)
            if job.queuePosition != i:
                job.queuePosition = i
                self.jobs.save(job)
                self.bus.publish(jid, "queued", {"position": i})

    def _next(self) -> Job | None:
        for jid in self.queue.items():
            job = self.jobs.get(jid)
            if job.status != "queued":
                self.queue.remove(jid)
                continue
            if not job.dependsOn:
                return job
            dep = self.jobs.get(job.dependsOn)
            if dep.status == "succeeded":
                return job
            if dep.finished:            # failed / canceled: this one can never start
                self.queue.remove(jid)
                job.fail({"code": "DEPENDENCY_NOT_READY", "message": "依存するジョブが成功していません",
                          "detail": {"dependsOn": dep.jobId, "status": dep.status}})
                self.jobs.save(job)
                self.bus.publish(jid, "job_failed", {"stage": None, "error": job.error, "logTail": ""})
        return None

    async def _loop(self) -> None:
        while True:
            job = self._next()
            if job is None:
                self._wake.clear()
                self.publish_positions()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                continue
            self.queue.remove(job.jobId)
            self.publish_positions()
            runner = self.runner_factory()
            task = asyncio.create_task(self._run(job, runner))
            self.current = (job, runner, task)
            try:
                await task
            except asyncio.CancelledError:
                if not task.done():
                    raise
            finally:
                self.current = None

    async def cancel(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job.finished:
            return job
        if job.status == "queued":
            self.queue.remove(job_id)
            job.cancel()
            self.jobs.save(job)
            self.bus.publish(job_id, "job_finished", {"status": "canceled"})
            self.publish_positions()
            return job
        if self.current and self.current[0].jobId == job_id:
            cur, runner, task = self.current
            self._canceling.add(job_id)                   # read by _run
            await runner.terminate()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            return self.jobs.get(job_id)
        return job

    # --- one job ---------------------------------------------------------------------------------------------------
    def _deps(self, job: Job) -> dict[str, Job]:
        deps, cur = {}, job
        while cur.type in DEPENDS_ON and cur.dependsOn:
            dep = self.jobs.get(cur.dependsOn)
            deps[dep.type] = dep
            cur = dep
        return deps

    async def _run(self, job: Job, runner: PipelineRunnerPort) -> None:
        report = JobReporter(job, self.jobs, self.bus, self.store)
        root = self.projects.root(job.projectId)
        work = root / "work" / job.jobId
        work.mkdir(parents=True, exist_ok=True)
        job.start()
        self.jobs.save(job)
        ctx = JobContext(job=job, project=self.projects.get(job.projectId), project_root=root, work=work,
                         deps=self._deps(job), runner=runner, report=report, settings=self.settings,
                         extra={"gpu_id": self.gpu_id})
        current = None
        try:
            for stage in self.planner.stages(job.type):
                current = stage.names[0]
                await stage.run(ctx)
            job.succeed()
            self.jobs.save(job)
            self.bus.publish(job.jobId, "job_finished", {"status": "succeeded"})
        except asyncio.CancelledError:
            if job.jobId not in self._canceling:
                raise
            self._canceled(job)
        except VvlogError as e:
            if job.jobId in self._canceling:          # the killed process exited first: still a cancel
                self._canceled(job)
            else:
                self._failed(job, e.to_dict(), current, work)
        except Exception as e:                            # noqa: BLE001 — any bug fails the job, not the server
            if job.jobId in self._canceling:
                self._canceled(job)
            else:
                log.exception("job %s failed", job.jobId)
                self._failed(job, {"code": "PIPELINE_FAILED", "message": f"{type(e).__name__}: {e}"}, current, work)

    def _canceled(self, job: Job) -> None:
        self._canceling.discard(job.jobId)
        job.cancel()
        self.jobs.save(job)
        self.bus.publish(job.jobId, "job_finished", {"status": "canceled"})

    def _failed(self, job: Job, error: dict, stage: str | None, work: Path) -> None:
        running = next((s.name for s in job.stages if s.status == "running"), stage)
        tail = error.get("detail", {}).pop("logTail", None) if isinstance(error.get("detail"), dict) else None
        if tail is None:
            logs = sorted(work.glob("*.log"), key=lambda p: p.stat().st_mtime)
            tail = log_tail(logs[-1], 40) if logs else ""
        job.fail(error, running)
        self.jobs.save(job)
        self.bus.publish(job.jobId, "job_failed", {"stage": running, "error": {"code": error["code"],
                                                                               "message": error["message"]},
                                                   "logTail": tail})
