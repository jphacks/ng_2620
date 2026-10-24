"""occlusion: fusion v2 once (scripts/occlusion/run_occlusion_stage.py); publishes the occluder units and keeps
fusion_state.pkl in work/<analyzeJobId>/occ/ for render and export (designdocs/features/analysis-pipeline)."""
from __future__ import annotations

import asyncio
import shutil

from ...domain.errors import VvlogError
from . import progress as P
from .base import JobContext, raise_for_exit
from .ingest import zip_dir
from .pipeline_env import pipeline_cmd, pipeline_env

PREP, GPU_END = 0.10, 0.85        # progress shares: prep, then the GPU stage's per-frame lines, then the CPU stages


class OcclusionStage:
    names = ("occlusion",)

    async def run(self, ctx: JobContext) -> None:
        r = ctx.report
        occ, pub = ctx.work / "occ", ctx.work / "_publish"
        r.stage_started("occlusion")
        n = max(1, len(list((ctx.work / "proxy").glob("*.jpg"))))

        def on_line(line: str) -> None:
            m = P.FUSION_FRAME.match(line.strip())
            if m:
                i = int(m[1])
                r.progress("occlusion", PREP + (GPU_END - PREP) * (i + 1) / n, message=f"frame {i + 1}/{n}")
            elif "gpu stage" in line:
                r.progress("occlusion", GPU_END, force=True)
            elif P.FUSION_DONE.match(line.strip()):
                r.progress("occlusion", 0.97, force=True)

        cmd = pipeline_cmd(ctx.settings, "scripts/occlusion/run_occlusion_stage.py", "--pipeline_out",
                           ctx.work / "pout", "--out", occ, "--name", ctx.job.jobId)
        log = ctx.work / "occlusion.log"
        code = await ctx.runner.run(cmd, pipeline_env(ctx), ctx.settings.pipeline_root, on_line, log)
        raise_for_exit(code, log, "run_occlusion_stage.py")
        if not (occ / "units.json").is_file() or not (occ / "fusion_state.pkl").is_file():
            raise VvlogError("PIPELINE_FAILED", "遮蔽判定の出力がありません", {"dir": str(occ)})
        pub.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(occ / "units.json", pub / "units.json")
        r.artifact("units_index", pub / "units.json")
        r.artifact("units", await asyncio.to_thread(zip_dir, occ / "units", pub / "units.zip", "units", "*.png"))
        r.stage_finished("occlusion")
