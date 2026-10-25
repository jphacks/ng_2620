"""Which H.264 encoder this ffmpeg can actually use, and the arguments for a CRF-like quality.

The Windows installer ships the LGPL build of ffmpeg (no libx264, which is GPL). It still has Media Foundation
(Windows' own encoder) and OpenH264, so the first of these that encodes a few test frames is used. NVENC is left out:
it refuses frames smaller than about 145x49, and Media Foundation already uses the GPU where it can.
"""
from __future__ import annotations

import functools
import subprocess

CANDIDATES = ("libx264", "h264_mf", "libopenh264")
_SRC = ["-f", "lavfi", "-i", "testsrc2=size=64x36:rate=30:duration=0.1"]


def _quality(encoder: str, crf: int) -> list[str]:
    """`crf` is libx264's scale (0-51, lower is better); the others get their nearest equivalent."""
    if encoder == "libx264":
        return ["-crf", str(crf)]
    if encoder == "h264_mf":  # quality 0-100, higher is better; crf 18 -> 64, 23 -> 54
        return ["-rate_control", "quality", "-quality", str(max(1, min(100, 100 - 2 * crf)))]
    return ["-rc_mode", "quality", "-qmin", str(crf), "-qmax", str(min(51, crf + 6))]


@functools.lru_cache(maxsize=None)
def pick_encoder(ffmpeg: str = "ffmpeg") -> str:
    for enc in CANDIDATES:
        try:
            r = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", *_SRC, "-c:v", enc, "-pix_fmt", "yuv420p",
                                "-f", "null", "-"], capture_output=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return enc
    return "libx264"  # nothing worked: keep the old behaviour so the ffmpeg error names the encoder


def video_args(crf: int, ffmpeg: str = "ffmpeg", high_profile: bool = False) -> list[str]:
    """`-c:v <encoder>` plus its quality options (and the High profile where asked and the encoder has it)."""
    enc = pick_encoder(ffmpeg)
    args = ["-c:v", enc, *_quality(enc, crf)]
    if high_profile and enc == "libx264":
        args += ["-profile:v", "high"]
    elif high_profile and enc == "h264_mf":
        args += ["-profile", "100"]  # eAVEncH264VProfile_High
    return args
