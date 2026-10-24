"""Stages that replay instead of computing (designdocs/features/mock-server-fixtures).

With a fixture (fixtures/<name>/jobs/<type>/events.jsonl + artifacts/), the recorded progress is replayed at
VVLOG_MOCK_SPEED times the recorded pace and the recorded artifacts are published. Without one, a synthetic run:
progress over a nominal duration per stage and small placeholder artifacts of the right names and formats.

VVLOG_MOCK_FAIL=<stage>:<CODE>   fail that stage with that error code (e.g. track:NO_PERSON_FOUND)
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from vvlog_server.application.stages.base import JobContext
from vvlog_server.domain import media as M
from vvlog_server.domain.artifact import CATALOG
from vvlog_server.domain.errors import VvlogError
from vvlog_server.domain.job import STAGES

# nominal seconds of a 10 s clip on the GPU server (designdocs/architecture/pipeline-integration §1)
NOMINAL_S = {"ingest": 8, "track": 40, "mesh": 30, "occlusion": 90, "removal": 20, "avatar": 30, "layers": 10,
             "composite": 60, "encode": 10}


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


class ReplayStage:
    def __init__(self, job_type: str, fixture: Path | None, speed: float, fail: tuple[str, str] | None):
        self.names = STAGES[job_type]
        self.job_type, self.fixture, self.speed, self.fail = job_type, fixture, max(speed, 0.01), fail

    async def run(self, ctx: JobContext) -> None:
        d = self.fixture / "jobs" / self.job_type if self.fixture else None
        if d and (d / "events.jsonl").is_file():
            await self._replay(ctx, d)
        else:
            await self._synthetic(ctx)

    def _maybe_fail(self, stage: str) -> None:
        if self.fail and self.fail[0] == stage:
            raise VvlogError(self.fail[1], f"モックで {stage} を失敗させました（VVLOG_MOCK_FAIL）")

    async def _replay(self, ctx: JobContext, d: Path) -> None:
        events = [json.loads(l) for l in (d / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        prev = None
        for e in events:
            if prev is not None:
                await asyncio.sleep(max(0.0, (_ts(e["ts"]) - prev) / self.speed))
            prev = _ts(e["ts"])
            ev, data = e["event"], e["data"]
            if ev == "stage_started":
                ctx.report.stage_started(data["stage"])
                self._maybe_fail(data["stage"])
            elif ev == "progress":
                ctx.report.progress(data["stage"], data["progress"], data.get("etaSec"), data.get("message"),
                                    force=True)
            elif ev == "artifact_ready":
                ctx.report.artifact(data["name"], d / "artifacts" / CATALOG[data["name"]][2])
            elif ev == "stage_finished":
                ctx.report.stage_finished(data["stage"])

    async def _synthetic(self, ctx: JobContext) -> None:
        out = ctx.work / "_publish"
        out.mkdir(parents=True, exist_ok=True)
        meta = _meta(ctx)
        for stage in self.names:
            ctx.report.stage_started(stage)
            self._maybe_fail(stage)
            total = NOMINAL_S[stage] / self.speed
            steps = max(2, int(total / 0.5))
            n = meta["frameCount"]
            for i in range(1, steps + 1):
                await asyncio.sleep(total / steps)
                ctx.report.progress(stage, i / steps, eta=total * (1 - i / steps),
                                    message=f"frame {int(n * i / steps)}/{n}", force=True)
            for name, path in _placeholders(stage, meta, out, ctx):
                ctx.report.artifact(name, path)
            ctx.report.stage_finished(stage)


def _meta(ctx: JobContext) -> dict:
    analyze = ctx.job if ctx.job.type == "analyze" else ctx.deps["analyze"]
    media = ctx.project.get("media", {}).get(analyze.params.get("mediaId"), {})
    if media.get("meta"):
        return media["meta"]
    return M.MediaMeta(durationSec=10.0, frameCount=300, fps=30.0, full={"width": 1080, "height": 1920},
                       proxy={"width": 540, "height": 960}, proxyScale=2, hasAudio=True,
                       sourceCodec="h264").to_dict()


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for k, v in files.items():
            z.writestr(k, v)
    return path


def _json(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _placeholders(stage: str, meta: dict, out: Path, ctx: JobContext):
    """Right names and formats, minimal content (frames are tiny 1x1 images)."""
    n, w, h = meta["frameCount"], meta["proxy"]["width"], meta["proxy"]["height"]
    jpg, png = _tiny_jpeg(), _tiny_png()
    persons = [{"personId": p, "firstFrame": 0, "lastFrame": n - 1, "visibleRatio": 0.95, "bestFrame": n // 2,
                "bbox": [w * (0.15 + 0.4 * (p - 1)), h * 0.25, w * 0.3, h * 0.6],
                "thumb": f"person_thumbs/{p}.jpg"} for p in (1, 2)]
    if stage == "ingest":
        yield "meta", _json(out / "meta.json", meta)
        yield "proxy_frames", _zip(out / "proxy_frames.zip", {f"frames/{f:05d}.jpg": jpg for f in range(n)})
    elif stage == "track":
        yield "persons", _json(out / "persons.json", persons)
        yield "person_thumbs", _zip(out / "person_thumbs.zip", {f"person_thumbs/{p['personId']}.jpg": jpg
                                                                for p in persons})
        yield "track_masks", _zip(out / "track_masks.zip", {f"masks/{f:05d}.png": png for f in range(n)})
    elif stage == "mesh":
        yield "mesh_preview", _video(out / "mesh_preview.mp4", meta)
    elif stage == "occlusion":
        units = {"units": [{"unitId": 1, "frames": [0, n - 1], "label": "object", "tracks": [1], "areaPx": 5000,
                            "perPerson": {"1": {"auto": "front", "why": "D+", "confidence": 0.8,
                                                "overlapFrames": [0, n - 1]},
                                          "2": {"auto": "none", "why": "", "confidence": 0.0, "overlapFrames": []}}}]}
        yield "units_index", _json(out / "units.json", units)
        yield "units", _zip(out / "units.zip", {f"units/{f:05d}.png": png for f in range(n)})
    elif stage == "layers":
        cast = [c["personId"] for c in ctx.job.params.get("cast", [])]
        webp = _asset("webp")
        files = {f"clean/{f:05d}.webp": webp for f in range(n)}
        for p in cast:
            files.update({f"avatar/{p}/{f:05d}.webp": webp for f in range(n)})
            files.update({f"hide/{p}/{f:05d}.png": png for f in range(n)})
        yield "layers", _zip(out / "layers.zip", files)
        yield "layers_index", _json(out / "layers.json", {
            "persons": cast, "order": {str(f): cast for f in range(n)},
            "avatarIds": {str(c["personId"]): c["avatarId"] for c in ctx.job.params.get("cast", [])}})
        yield "preview_video", _video(out / "preview.mp4", meta)
    elif stage == "encode":
        yield "export_video", _video(out / "export.mp4", meta)


@functools.lru_cache(maxsize=None)
def _asset(kind: str, w: int = 64, h: int = 64) -> bytes:
    """A small gray image or a 1 s test video made with ffmpeg (b"" without ffmpeg)."""
    if not shutil.which("ffmpeg"):
        return b""
    d = Path(tempfile.mkdtemp(prefix="vvlog_mock_"))
    if kind == "mp4":
        out = d / "v.mp4"
        cmd = ["-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30:duration=1", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    else:
        out = d / f"i.{kind}"
        cmd = ["-f", "lavfi", "-i", f"color=c=gray:size={w}x{h}", "-frames:v", "1"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *cmd, str(out)], check=True)
    return out.read_bytes()


def _tiny_png() -> bytes:
    return _asset("png")


def _tiny_jpeg() -> bytes:
    return _asset("jpg")


def _video(path: Path, meta: dict) -> Path:
    path.write_bytes(_asset("mp4", meta["proxy"]["width"], meta["proxy"]["height"]))
    return path


def mock_planner(env: dict | None = None) -> "Planner":
    from vvlog_server.application.stages.planner import Planner
    e = os.environ if env is None else env
    fixture = e.get("VVLOG_MOCK_FIXTURE")
    fx = Path(fixture).resolve() if fixture else None
    speed = float(e.get("VVLOG_MOCK_SPEED", "10"))
    fail = e.get("VVLOG_MOCK_FAIL")
    f = tuple(fail.split(":", 1)) if fail and ":" in fail else None
    return Planner({t: [ReplayStage(t, fx, speed, f)] for t in STAGES})

