import asyncio
import sys

import pytest

from vvlog_server.adapters.events import JsonlEventBus
from vvlog_server.adapters.fs import FsArtifactStore, FsJobRepository, FsProjectRepository
from vvlog_server.adapters.queue import InProcessJobQueue
from vvlog_server.adapters.subprocess_runner import SubprocessPipelineRunner
from vvlog_server.application.stages.planner import Planner
from vvlog_server.application.worker import Worker
from vvlog_server.domain.errors import VvlogError
from vvlog_server.domain.job import Job


class SleepStage:
    """Reports its stages, runs `python -c sleep` through the runner, optionally fails."""

    def __init__(self, names, seconds=0.0, fail=None, order=None):
        self.names, self.seconds, self.fail, self.order = names, seconds, fail, order

    async def run(self, ctx):
        for n in self.names:
            ctx.report.stage_started(n)
            if self.order is not None:
                self.order.append((ctx.job.jobId, n))
            if self.seconds:
                code = await ctx.runner.run([sys.executable, "-c", f"import time; time.sleep({self.seconds})"], {},
                                            ctx.work, lambda _l: None, ctx.work / "sleep.log")
                if code:
                    raise VvlogError("PIPELINE_FAILED", "sleep failed")
            if self.fail:
                raise VvlogError(self.fail, "失敗させた")
            ctx.report.progress(n, 1.0, force=True)
            ctx.report.stage_finished(n)


def make(tmp_path, table):
    projects, jobs = FsProjectRepository(tmp_path), FsJobRepository(tmp_path)
    bus, queue = JsonlEventBus(tmp_path), InProcessJobQueue()
    w = Worker(jobs=jobs, queue=queue, bus=bus, store=FsArtifactStore(projects, jobs), projects=projects,
               planner=Planner(table), runner_factory=SubprocessPipelineRunner, settings=None)
    return w, projects.create("p")["projectId"]


def submit(w, pid, job_type, params):
    j = Job.create(pid, job_type, params)
    w.jobs.save(j)
    w.queue.enqueue(j.jobId)
    w.wake()
    return j.jobId


async def wait_done(w, *ids, timeout=10):
    async def poll():
        while not all(w.jobs.get(i).finished for i in ids):
            await asyncio.sleep(0.02)
    await asyncio.wait_for(poll(), timeout)


async def test_dependency_order_and_events(tmp_path):
    order = []
    w, pid = make(tmp_path, {"analyze": [SleepStage(("ingest", "track", "mesh", "occlusion"), order=order)],
                             "render": [SleepStage(("removal", "avatar", "layers"), order=order)]})
    a = submit(w, pid, "analyze", {"mediaId": "m"})
    r = submit(w, pid, "render", {"analyzeJobId": a})
    w.start()
    await wait_done(w, a, r)
    await w.stop()
    assert [w.jobs.get(i).status for i in (a, r)] == ["succeeded", "succeeded"]
    assert [n for _, n in order] == ["ingest", "track", "mesh", "occlusion", "removal", "avatar", "layers"]
    events = [e["event"] for e in w.bus.history(a)]
    assert events[-1] == "job_finished" and events.count("stage_finished") == 4


async def test_render_fails_when_analyze_failed(tmp_path):
    w, pid = make(tmp_path, {"analyze": [SleepStage(("ingest",), fail="NO_PERSON_FOUND")], "render": []})
    a = submit(w, pid, "analyze", {"mediaId": "m"})
    r = submit(w, pid, "render", {"analyzeJobId": a})
    w.start()
    await wait_done(w, a, r)
    await w.stop()
    ja, jr = w.jobs.get(a), w.jobs.get(r)
    assert ja.error["code"] == "NO_PERSON_FOUND" and ja.stage("ingest").status == "failed"
    assert jr.status == "failed" and jr.error["code"] == "DEPENDENCY_NOT_READY"
    failed = [e for e in w.bus.history(a) if e["event"] == "job_failed"][0]["data"]
    assert failed["stage"] == "ingest" and failed["error"]["code"] == "NO_PERSON_FOUND"


async def test_cancel_running_kills_the_process(tmp_path):
    w, pid = make(tmp_path, {"analyze": [SleepStage(("ingest",), seconds=30)]})
    a = submit(w, pid, "analyze", {"mediaId": "m"})
    b = submit(w, pid, "analyze", {"mediaId": "m"})
    w.start()
    while not (w.current and w.jobs.get(a).stage("ingest").status == "running"):
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.3)
    t0 = asyncio.get_running_loop().time()
    await w.cancel(b)                       # queued: just leaves the queue
    await w.cancel(a)                       # running: the sleep is killed
    assert asyncio.get_running_loop().time() - t0 < 5
    await w.stop()
    assert w.jobs.get(a).status == "canceled" and w.jobs.get(b).status == "canceled"
    assert w.bus.history(a)[-1]["data"] == {"status": "canceled"}


async def test_recover_marks_running_interrupted(tmp_path):
    w, pid = make(tmp_path, {"analyze": []})
    j = Job.create(pid, "analyze", {"mediaId": "m"})
    j.start()
    j.stage_started("track")
    w.jobs.save(j)
    q = Job.create(pid, "analyze", {"mediaId": "m"})
    w.jobs.save(q)
    w2, _ = make(tmp_path, {"analyze": []})
    w2.recover()
    got = w2.jobs.get(j.jobId)
    assert got.status == "failed" and got.error["code"] == "INTERRUPTED" and got.stage("track").status == "failed"
    assert w2.queue.items() == [q.jobId]
