"""Video metadata and sha256 for files the user picked (port of src-tauri/src/commands/{media,hash}.rs, #24).

These read files that usually live outside the allowed roots of /v1/local/fs (a clip on D:\\ chosen in the UI), so they
only accept an absolute path to an existing regular file, return derived values (never file content), and hand ffprobe
a `file`-only protocol whitelist.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Iterator

from ..domain.errors import VvlogError

BLOCK = 4 << 20
PROBE_TIMEOUT_S = 60
_CACHE_SIZE = 256


def resolve_media_file(raw: str) -> Path:
    if not raw or "\x00" in raw or not os.path.isabs(raw):
        raise VvlogError("PATH_NOT_ALLOWED", "絶対パスを指定してください", {"path": raw})
    p = Path(os.path.realpath(raw))
    if not p.is_file():
        raise VvlogError("NOT_FOUND", "ファイルが見つかりません", {"path": raw})
    return p


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rate(v) -> float | None:
    """'30000/1001' -> 29.97; '0/0' -> None"""
    if not isinstance(v, str):
        return None
    n, _, d = v.partition("/")
    n, d = _num(n), _num(d or "1")
    if n is None or d is None or n == 0 or d == 0:
        return None
    return n / d


def parse_probe(d: dict, size_bytes: int) -> dict:
    """ffprobe -print_format json -show_format -show_streams  ->  the shape the app's MediaProbePort expects."""
    streams = d.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise VvlogError("MEDIA_UNSUPPORTED", "映像トラックがありません")
    # rotation: old files carry tags.rotate; newer ffprobe a display matrix whose rotation is counter-clockwise
    rot = None
    tag = _num((video.get("tags") or {}).get("rotate"))
    if tag is not None:
        rot = int(tag)
    else:
        for sd in video.get("side_data_list") or []:
            r = _num(sd.get("rotation"))
            if r is not None:
                rot = -round(r)
                break
    return {
        "durationSec": _num((d.get("format") or {}).get("duration")) or _num(video.get("duration")) or 0.0,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "rotation": (rot or 0) % 360,
        "codec": video.get("codec_name"),
        "fps": _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")),
        "hasAudio": any(s.get("codec_type") == "audio" for s in streams),
        "sizeBytes": size_bytes,
    }


class LocalMedia:
    def __init__(self, ffprobe: str | None):
        self.ffprobe = ffprobe
        self._hashes: OrderedDict[tuple[str, int, int], str] = OrderedDict()

    def probe(self, raw_path: str) -> dict:
        p = resolve_media_file(raw_path)
        if not self.ffprobe:
            raise VvlogError("FFPROBE_UNAVAILABLE", "ffprobe が見つかりません")
        try:
            r = subprocess.run([self.ffprobe, "-v", "error", "-protocol_whitelist", "file", "-print_format", "json",
                                "-show_format", "-show_streams", str(p)], capture_output=True, timeout=PROBE_TIMEOUT_S,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as e:
            raise VvlogError("FFPROBE_UNAVAILABLE", f"ffprobe を実行できません: {e}") from e
        if r.returncode:
            raise VvlogError("MEDIA_UNSUPPORTED", "動画として読めません", {"ffprobe": r.stderr.decode("utf-8", "replace")[-500:]})
        try:
            return parse_probe(json.loads(r.stdout), p.stat().st_size)
        except json.JSONDecodeError as e:
            raise VvlogError("MEDIA_UNSUPPORTED", "ffprobe の出力を読めません") from e

    def sha256(self, raw_path: str) -> Iterator[float | str]:
        """Yields progress ratios (at least 1% apart), then the hex digest as the last item. Same file (path, size,
        mtime) -> the cached digest straight away."""
        p = resolve_media_file(raw_path)
        try:
            st = p.stat()
            key = (str(p), st.st_size, st.st_mtime_ns)
            if key in self._hashes:
                self._hashes.move_to_end(key)
                yield 1.0
                yield self._hashes[key]
                return
            h, done, last, total = hashlib.sha256(), 0, 0.0, max(st.st_size, 1)
            with open(p, "rb") as f:
                while block := f.read(BLOCK):
                    h.update(block)
                    done += len(block)
                    if done / total - last >= 0.01:
                        last = done / total
                        yield last
        except OSError as e:
            raise VvlogError("FS_FAILED", f"{e.strerror or e}", {"path": raw_path}) from e
        digest = h.hexdigest()
        self._hashes[key] = digest
        while len(self._hashes) > _CACHE_SIZE:
            self._hashes.popitem(last=False)
        yield 1.0
        yield digest
