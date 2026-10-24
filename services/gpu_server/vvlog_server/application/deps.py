"""The ports the use cases need, bundled by the composition root (main.py)."""
from __future__ import annotations

from dataclasses import dataclass

from .ports import (ArtifactStorePort, AvatarStorePort, EventBusPort, GpuInfoPort, JobQueuePort, JobRepositoryPort,
                    MediaPort, ProjectRepositoryPort, UploadRepositoryPort)
from .worker import Worker


@dataclass
class Deps:
    settings: object
    projects: ProjectRepositoryPort
    uploads: UploadRepositoryPort
    avatars: AvatarStorePort
    jobs: JobRepositoryPort
    queue: JobQueuePort
    bus: EventBusPort
    store: ArtifactStorePort
    media: MediaPort
    gpu: GpuInfoPort
    worker: Worker
