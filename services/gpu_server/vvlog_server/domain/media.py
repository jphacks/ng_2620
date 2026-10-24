"""Input limits, the proxy size and MediaMeta (designdocs/adr/0011). No I/O here."""
from __future__ import annotations

from dataclasses import dataclass

from .errors import VvlogError

MAX_DURATION_SEC = 180.0
MAX_LONG_SIDE = 1920
MAX_FPS = 30.0
PROXY_SHORT_SIDE = 480
CONTAINERS = ("mov", "mp4", "m4a", "3gp", "3g2", "mj2")      # ffprobe's format_name for mp4 / mov
VIDEO_CODECS = ("h264", "hevc")


@dataclass(frozen=True)
class Probe:
    """What ffprobe tells about a source file (rotation not applied)."""
    format_name: str
    codec: str
    width: int
    height: int
    rotation: int            # degrees, as in the display matrix (0, 90, 180, 270)
    fps: float               # average frame rate
    duration_sec: float
    has_audio: bool

    @property
    def display_size(self) -> tuple[int, int]:
        return (self.height, self.width) if self.rotation % 180 else (self.width, self.height)


def validate(p: Probe, max_duration: float = MAX_DURATION_SEC, max_long_side: int = MAX_LONG_SIDE) -> None:
    if not any(c in p.format_name.split(",") for c in CONTAINERS) or p.codec not in VIDEO_CODECS:
        raise VvlogError("MEDIA_UNSUPPORTED", "対応していない形式です（mp4 / mov の H.264 / HEVC）",
                         {"format": p.format_name, "codec": p.codec})
    if p.duration_sec > max_duration + 0.05:
        raise VvlogError("MEDIA_TOO_LONG", f"動画が {max_duration:g} 秒を超えています",
                         {"durationSec": round(p.duration_sec, 2), "limitSec": max_duration})
    w, h = p.display_size
    if max(w, h) > max_long_side:
        raise VvlogError("MEDIA_TOO_LARGE", f"解像度が長辺 {max_long_side}px を超えています",
                         {"width": w, "height": h, "limitLongSide": max_long_side})


def output_fps(src_fps: float) -> float:
    """CFR rate after ingest: the source rate, halved until it is at most 30 (60 -> 30, 50 -> 25)."""
    fps = src_fps if src_fps > 0 else MAX_FPS
    while fps > MAX_FPS + 0.01:
        fps /= 2
    return round(fps, 3)


def proxy_scale(width: int, height: int, target_short: int = PROXY_SHORT_SIDE) -> int:
    """The integer factor s (full = proxy * s) whose proxy short side is closest to target_short.

    Both proxy sides must be whole and even (H.264); ties go to the larger proxy."""
    best = None
    for s in range(1, 9):
        if width % s or height % s or (width // s) % 2 or (height // s) % 2:
            continue
        key = (abs(min(width, height) // s - target_short), s)
        if best is None or key < best:
            best = key
    return best[1] if best else 1


def even_size(width: int, height: int) -> tuple[int, int]:
    return width - width % 2, height - height % 2


@dataclass(frozen=True)
class MediaMeta:
    durationSec: float
    frameCount: int
    fps: float
    full: dict
    proxy: dict
    proxyScale: int
    hasAudio: bool
    sourceCodec: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def plan_meta(p: Probe) -> MediaMeta:
    """MediaMeta as ingest will produce it: rotation applied, even full size, CFR rate, integer proxy."""
    w, h = even_size(*p.display_size)
    s = proxy_scale(w, h)
    fps = output_fps(p.fps)
    return MediaMeta(durationSec=round(p.duration_sec, 3), frameCount=max(1, int(round(p.duration_sec * fps))),
                     fps=fps, full={"width": w, "height": h}, proxy={"width": w // s, "height": h // s},
                     proxyScale=s, hasAudio=p.has_audio, sourceCodec=p.codec)
