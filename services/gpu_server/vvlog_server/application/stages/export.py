"""export: composite (avatar_app at full resolution with the corrections) -> encode (H.264 + the source audio)
(designdocs/features/export)."""
from __future__ import annotations

import asyncio
import json

from ...domain.errors import VvlogError
from ...domain.overrides import expand
from ..ports import AvatarStorePort, MediaPort
from . import progress as P
from .base import JobContext, raise_for_exit
from .pipeline_env import pipeline_cmd, pipeline_env
from .render import avatar_args


class CompositeStage:
    names = ("composite",)

    def __init__(self, avatars: AvatarStorePort):
        self.avatars = avatars

    async def run(self, ctx: JobContext) -> None:
        r = ctx.report
        an = ctx.work_of("analyze")
        ex = ctx.work / "ex"
        meta = json.loads((an / "meta.json").read_text(encoding="utf-8"))
        settings = ctx.job.params["settings"]
        r.stage_started("composite")
        units = json.loads((an / "occ" / "units.json").read_text(encoding="utf-8"))
        items = expand(units, ctx.job.params.get("overrides", []), meta["frameCount"])
        ex.mkdir(parents=True, exist_ok=True)
        (ex / "overrides.json").write_text(json.dumps({"items": items}, indent=1), encoding="utf-8")
        hq = ["--hq", an / "full"] if settings.get("resolution", "full") == "full" and meta["proxyScale"] != 1 else []

        def on_line(line: str) -> None:
            m = P.AVATAR_FRAME.match(line.strip())
            if m:
                done, total = int(m[1]), int(m[2])
                r.progress("composite", 0.1 + 0.9 * done / max(1, total), message=f"frame {done}/{total}")
            elif line.startswith("[avatar] target"):
                r.progress("composite", 0.05)

        cmd = pipeline_cmd(ctx.settings, "scripts/avatar_app.py", "--pipeline_out", an / "pout", "--out", ex,
                           "--name", ctx.job.jobId, "--gpu_removal", "--fps", meta["fps"],
                           "--fusion_state", an / "occ" / "fusion_state.pkl", "--overrides", ex / "overrides.json",
                           *hq, *avatar_args(ctx, self.avatars))
        log = ctx.work / "composite.log"
        code = await ctx.runner.run(cmd, pipeline_env(ctx), ctx.settings.pipeline_root, on_line, log)
        raise_for_exit(code, log, "avatar_app.py")
        if not (ex / f"avatar_{ctx.job.jobId}.mp4").is_file():
            raise VvlogError("PIPELINE_FAILED", "合成した動画がありません", {"dir": str(ex)})
        r.stage_finished("composite")


class EncodeStage:
    names = ("encode",)

    def __init__(self, media: MediaPort):
        self.media = media

    async def run(self, ctx: JobContext) -> None:
        r = ctx.report
        r.stage_started("encode")
        an = ctx.work_of("analyze")
        s = ctx.job.params["settings"]
        audio = an / "audio.m4a"
        out = ctx.work / "_publish" / "export.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self.media.mux, ctx.work / "ex" / f"avatar_{ctx.job.jobId}.mp4",
                                audio if s.get("audio", True) and audio.is_file() else None, out, int(s.get("crf", 18)))
        r.artifact("export_video", out)
        r.stage_finished("encode")
