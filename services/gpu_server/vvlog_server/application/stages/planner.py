"""Which stages each job type runs. The real stages are added one by one (ingest, track+mesh, occlusion, ...)."""
from __future__ import annotations

from .base import Stage


class Planner:
    def __init__(self, table: dict[str, list[Stage]]):
        self.table = table

    def stages(self, job_type: str) -> list[Stage]:
        return list(self.table.get(job_type, []))


def planner_for(settings, media=None, outputs=None) -> Planner:
    """The composition root's choice of adapters for the real stages (main.py passes none: the defaults)."""
    from ...adapters.media_ffmpeg import FfmpegMedia
    from ...adapters.pipeline_outputs import FsPipelineOutputs
    from .ingest import IngestStage
    from .track_mesh import TrackMeshStage
    media, outputs = media or FfmpegMedia(), outputs or FsPipelineOutputs()
    return Planner({"analyze": [IngestStage(media), TrackMeshStage(outputs)], "render": [], "export": []})
