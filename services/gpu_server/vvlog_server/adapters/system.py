"""GPU info from nvidia-smi (the server itself never imports torch) and ffprobe."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..domain.errors import VvlogError
from ..domain.media import Probe


class NvidiaSmiGpuInfo:
    def __init__(self, gpu_id: str = "0"):
        self.gpu_id = gpu_id
        self._cached: dict | None = None

    def info(self) -> dict:
        if self._cached is None:
            self._cached = {}
            if shutil.which("nvidia-smi"):
                try:
                    out = subprocess.run(["nvidia-smi", "-i", self.gpu_id, "--query-gpu=name,memory.total",
                                          "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                         timeout=10).stdout.strip()
                    name, mem = [s.strip() for s in out.splitlines()[0].split(",")]
                    self._cached = {"name": name, "vramMB": int(float(mem))}
                except (subprocess.SubprocessError, ValueError, IndexError):
                    pass
        return self._cached


def ffprobe(path: Path) -> Probe:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                              str(path)], capture_output=True, text=True, timeout=60, check=True).stdout
        d = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError) as e:
        raise VvlogError("MEDIA_UNSUPPORTED", "動画として読めません", {"reason": str(e)[:300]})
    video = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise VvlogError("MEDIA_UNSUPPORTED", "映像トラックがありません", {})
    rot = 0
    for sd in video.get("side_data_list", []):
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = int(video.get("tags", {}).get("rotate", rot))
    num, _, den = video.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    dur = float(video.get("duration") or d.get("format", {}).get("duration") or 0)
    return Probe(format_name=d.get("format", {}).get("format_name", ""), codec=video.get("codec_name", ""),
                 width=int(video["width"]), height=int(video["height"]), rotation=abs(rot) % 360, fps=fps,
                 duration_sec=dur, has_audio=any(s.get("codec_type") == "audio" for s in d.get("streams", [])))
