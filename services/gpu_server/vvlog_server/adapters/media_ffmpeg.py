"""MediaPort with ffprobe / ffmpeg."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..domain.media import MediaMeta, Probe
from .system import ffprobe


class FfmpegMedia:
    def probe(self, path: Path) -> Probe:
        return ffprobe(path)

    def normalize(self, src: Path, meta: MediaMeta, out_full: Path, out_proxy: Path, out_audio: Path | None,
                  on_progress: Callable[[float], None]) -> None:
        raise NotImplementedError("ingest stage")
