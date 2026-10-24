"""ingest: rotation, CFR, the full-resolution and proxy frames, the audio, meta.json (designdocs/features/
analysis-pipeline タスク 1). Its outputs live in the analyze job's work directory:

  work/<analyzeJobId>/full/%05d.jpg    full resolution (export's --hq)
  work/<analyzeJobId>/proxy/%05d.jpg   proxy resolution (the pipeline's --input_video)
  work/<analyzeJobId>/audio.m4a        when the source has audio
  work/<analyzeJobId>/meta.json        MediaMeta
"""
from __future__ import annotations

import asyncio
import json
import zipfile
from dataclasses import replace
from pathlib import Path

from ...domain import media as M
from ...domain.errors import VvlogError
from ..ports import MediaPort
from .base import JobContext


def zip_dir(src: Path, dest: Path, arc_prefix: str, pattern: str = "*") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as z:
        for f in sorted(src.glob(pattern)):
            z.write(f, f"{arc_prefix}/{f.name}")
    return dest


class IngestStage:
    names = ("ingest",)

    def __init__(self, media: MediaPort):
        self.media = media

    async def run(self, ctx: JobContext) -> None:
        r = ctx.report
        r.stage_started("ingest")
        m = ctx.project["media"][ctx.job.params["mediaId"]]
        src = ctx.project_root / m["path"]
        probe = await asyncio.to_thread(self.media.probe, src)
        M.validate(probe, ctx.settings.max_duration_sec, ctx.settings.max_long_side)
        meta = M.plan_meta(probe)
        full, proxy, audio = ctx.work / "full", ctx.work / "proxy", ctx.work / "audio.m4a"
        loop = asyncio.get_running_loop()

        def progress(p: float) -> None:
            loop.call_soon_threadsafe(r.progress, "ingest", 0.9 * p)

        await asyncio.to_thread(self.media.normalize, src, meta, full, proxy, audio, progress)
        n = len(list(proxy.glob("*.jpg")))
        if n == 0:
            raise VvlogError("MEDIA_UNSUPPORTED", "フレームを取り出せませんでした", {})
        meta = replace(meta, frameCount=n)              # what was actually decoded
        (ctx.work / "meta.json").write_text(json.dumps(meta.to_dict(), indent=1), encoding="utf-8")
        r.artifact("meta", ctx.work / "meta.json")
        pub = ctx.work / "_publish"
        r.artifact("proxy_frames", await asyncio.to_thread(zip_dir, proxy, pub / "proxy_frames.zip", "frames",
                                                           "*.jpg"))
        r.stage_finished("ingest")
