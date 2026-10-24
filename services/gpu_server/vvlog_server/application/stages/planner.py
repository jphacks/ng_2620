"""Which stages each job type runs. The real stages are added one by one (ingest, track+mesh, occlusion, ...)."""
from __future__ import annotations

from .base import Stage


class Planner:
    def __init__(self, table: dict[str, list[Stage]]):
        self.table = table

    def stages(self, job_type: str) -> list[Stage]:
        return list(self.table.get(job_type, []))


def planner_for(settings, media=None, outputs=None, avatars=None) -> Planner:
    """The composition root's choice of adapters for the real stages (main.py passes none: the defaults)."""
    from ...adapters.media_ffmpeg import FfmpegMedia
    from ...adapters.fs import FsAvatarStore
    from ...adapters.pipeline_outputs import FsPipelineOutputs
    from .ingest import IngestStage
    from .occlusion import OcclusionStage
    from .export import CompositeStage, EncodeStage
    from .render import RenderStage
    from .track_mesh import TrackMeshStage
    media, outputs = media or FfmpegMedia(), outputs or FsPipelineOutputs()
    avatars = avatars or FsAvatarStore(settings.data_root)
    return Planner({"analyze": [IngestStage(media), TrackMeshStage(outputs), OcclusionStage()],
                    "render": [RenderStage(avatars, outputs)],
                    "export": [CompositeStage(avatars), EncodeStage(media)]})
