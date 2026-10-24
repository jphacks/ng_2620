"""MediaPort with ffprobe / ffmpeg: one decode writes the full-resolution frames and the proxy frames (rotation
applied by ffmpeg's autorotate, CFR by the fps filter), a second pass copies or re-encodes the audio to AAC."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

from ..domain.errors import VvlogError
from ..domain.media import MediaMeta, Probe
from .system import ffprobe

_FRAME = re.compile(r"frame=\s*(\d+)")
JPEG_Q_FULL, JPEG_Q_PROXY = 2, 3          # ffmpeg -q:v (2 = best)


class FfmpegMedia:
    def probe(self, path: Path) -> Probe:
        return ffprobe(path)

    def normalize(self, src: Path, meta: MediaMeta, out_full: Path, out_proxy: Path, out_audio: Path | None,
                  on_progress: Callable[[float], None]) -> None:
        out_full.mkdir(parents=True, exist_ok=True)
        out_proxy.mkdir(parents=True, exist_ok=True)
        fw, fh = meta.full["width"], meta.full["height"]
        pw, ph = meta.proxy["width"], meta.proxy["height"]
        graph = (f"[0:v]fps={meta.fps},scale={fw}:{fh}:flags=lanczos,setsar=1,split=2[f][p];"
                 f"[p]scale={pw}:{ph}:flags=area[q]")
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-progress", "pipe:1", "-i", str(src),
               "-filter_complex", graph,
               "-map", "[f]", "-q:v", str(JPEG_Q_FULL), "-start_number", "0", str(out_full / "%05d.jpg"),
               "-map", "[q]", "-q:v", str(JPEG_Q_PROXY), "-start_number", "0", str(out_proxy / "%05d.jpg")]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in p.stdout:
            m = _FRAME.match(line.strip())
            if m:
                on_progress(min(0.99, int(m[1]) / max(1, meta.frameCount)))
        err = p.stderr.read()
        if p.wait():
            raise VvlogError("MEDIA_UNSUPPORTED", "動画を展開できませんでした", {"ffmpeg": err[-1500:]})
        if out_audio is not None and meta.hasAudio:
            r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(src), "-vn", "-map", "0:a:0",
                                "-c:a", "aac", "-b:a", "192k", str(out_audio)], capture_output=True, text=True)
            if r.returncode:
                raise VvlogError("MEDIA_UNSUPPORTED", "音声を取り出せませんでした", {"ffmpeg": r.stderr[-1500:]})
        on_progress(1.0)

    def mux(self, video: Path, audio: Path | None, out: Path, crf: int = 18) -> None:
        """The deliverable: H.264 High, yuv420p, CRF, AAC audio when given, +faststart (designdocs/features/export)."""
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(video)]
        if audio is not None:
            cmd += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        cmd += ["-c:v", "libx264", "-profile:v", "high", "-crf", str(crf), "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise VvlogError("PIPELINE_FAILED", "動画を書き出せませんでした", {"ffmpeg": r.stderr[-1500:]})

    def encode_mp4(self, frames_pattern: str, fps: float, out: Path, crf: int = 18) -> None:
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-framerate", str(fps), "-start_number", "0",
                            "-i", frames_pattern, "-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", str(out)], capture_output=True, text=True)
        if r.returncode:
            raise VvlogError("PIPELINE_FAILED", "動画を書き出せませんでした", {"ffmpeg": r.stderr[-1500:]})
