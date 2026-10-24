"""Composition root: settings -> adapters -> use cases -> FastAPI app.

  python -m vvlog_server            (VVLOG_API_TOKEN, VVLOG_DATA_ROOT, ... see config.py)
"""
from __future__ import annotations

import contextlib
import logging

from .adapters.events import JsonlEventBus
from .adapters.fs import FsArtifactStore, FsAvatarStore, FsJobRepository, FsProjectRepository, FsUploadRepository
from .adapters.media_ffmpeg import FfmpegMedia
from .adapters.queue import InProcessJobQueue
from .adapters.subprocess_runner import SubprocessPipelineRunner
from .adapters.system import NvidiaSmiGpuInfo
from .application.deps import Deps
from .application.stages.planner import planner_for
from .application.worker import Worker
from .config import Settings
from .interface.api import build_app


def create_app(settings: Settings | None = None, runner_factory=None, planner=None):
    s = settings or Settings.from_env()
    root = s.data_root
    root.mkdir(parents=True, exist_ok=True)
    projects, jobs = FsProjectRepository(root), FsJobRepository(root)
    bus, queue = JsonlEventBus(root), InProcessJobQueue()
    store = FsArtifactStore(projects, jobs)
    worker = Worker(jobs=jobs, queue=queue, bus=bus, store=store, projects=projects,
                    planner=planner or planner_for(s), runner_factory=runner_factory or SubprocessPipelineRunner,
                    settings=s, gpu_id=s.gpu_ids[0] if s.gpu_ids else "0")
    deps = Deps(settings=s, projects=projects, uploads=FsUploadRepository(root), avatars=FsAvatarStore(root),
                jobs=jobs, queue=queue, bus=bus, store=store, media=FfmpegMedia(), gpu=NvidiaSmiGpuInfo(worker.gpu_id),
                worker=worker)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        worker.recover()
        worker.start()
        yield
        await worker.stop()

    return build_app(deps, lifespan=lifespan)


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings.from_env()
    if not s.api_token:
        raise SystemExit("VVLOG_API_TOKEN を設定してください")
    uvicorn.run(create_app(s), host="127.0.0.1", port=s.port, log_level="info")


if __name__ == "__main__":
    main()
