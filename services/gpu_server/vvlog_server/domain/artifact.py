"""Artifacts a stage publishes (contracts §4)."""
from __future__ import annotations

from dataclasses import dataclass

# name -> (kind, stage, file name inside the job's artifact directory, content type)
CATALOG = {
    "meta": ("json", "ingest", "meta.json", "application/json"),
    "proxy_frames": ("zip", "ingest", "proxy_frames.zip", "application/zip"),
    "persons": ("json", "track", "persons.json", "application/json"),
    "person_thumbs": ("zip", "track", "person_thumbs.zip", "application/zip"),
    "track_masks": ("zip", "track", "track_masks.zip", "application/zip"),
    "mesh_preview": ("video", "mesh", "mesh_preview.mp4", "video/mp4"),
    "units": ("zip", "occlusion", "units.zip", "application/zip"),
    "units_index": ("json", "occlusion", "units.json", "application/json"),
    "layers": ("zip", "layers", "layers.zip", "application/zip"),
    "layers_index": ("json", "layers", "layers.json", "application/json"),
    "preview_video": ("video", "layers", "preview.mp4", "video/mp4"),
    "export_video": ("video", "encode", "export.mp4", "video/mp4"),
    "stats": ("json", "", "stats.json", "application/json"),
}


@dataclass(frozen=True)
class Artifact:
    name: str
    kind: str
    stage: str
    size: int
    sha256: str
    contentType: str
    url: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)
