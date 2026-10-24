"""track + mesh: one offline_app_lite.py process (detect, SAM3 tracking, HMR). The persons are published as soon as
tracking ends ([STAGE][sam3_propagate]), without waiting for the meshes (designdocs/features/analysis-pipeline)."""
from __future__ import annotations

import asyncio

from ...domain.errors import VvlogError
from ..ports import PipelineOutputsPort
from . import progress as P
from .base import JobContext, raise_for_exit
from .pipeline_env import pipeline_cmd, pipeline_env

# offline_app_lite's own stages before tracking finishes, as shares of "track" (the rest is sam3_propagate's tqdm)
TRACK_PRE = {"detect": 0.10, "sam3_load": 0.15, "sam3_init_state": 0.20}
TRACK_PROPAGATE = (0.20, 1.0)


class TrackMeshStage:
    names = ("track", "mesh")

    def __init__(self, outputs: PipelineOutputsPort):
        self.outputs = outputs

    def command(self, ctx: JobContext) -> list[str]:
        s = ctx.settings
        return pipeline_cmd(s, "scripts/offline_app_lite.py", "--input_video", ctx.work / "proxy",
                            "--output_dir", ctx.work / "pout")

    async def run(self, ctx: JobContext) -> None:
        r = ctx.report
        pout, pub = ctx.work / "pout", ctx.work / "_publish"
        state = {"stage": "track", "persons": None}
        r.stage_started("track")
        loop = asyncio.get_running_loop()

        async def publish_persons():
            persons = await asyncio.to_thread(self.outputs.persons_from_masks, pout, ctx.work / "proxy", pub)
            state["persons"] = persons
            if not persons:
                await ctx.runner.terminate()
                return
            (ctx.work / "_published_persons.json").write_bytes((pub / "persons.json").read_bytes())
            for name, f in (("persons", "persons.json"), ("person_thumbs", "person_thumbs.zip"),
                            ("track_masks", "track_masks.zip")):
                r.artifact(name, pub / f)
            r.stage_finished("track")
            r.stage_started("mesh")
            state["stage"] = "mesh"

        def on_line(line: str) -> None:
            done = P.stage_done(line)
            if done in TRACK_PRE and state["stage"] == "track":
                r.progress("track", TRACK_PRE[done])
            elif done == "sam3_propagate":
                r.progress("track", 1.0, force=True)
                state["task"] = loop.create_task(publish_persons())
            elif done == "hmr_4d" and state["stage"] == "mesh":
                r.progress("mesh", 0.97, force=True)
            else:
                tq = P.tqdm_fraction(line)
                if tq:
                    frac, eta = tq
                    if state["stage"] == "track":
                        a, b = TRACK_PROPAGATE
                        r.progress("track", a + (b - a) * frac, eta)
                    else:
                        r.progress("mesh", 0.95 * frac, eta)

        code = await ctx.runner.run(self.command(ctx), pipeline_env(ctx), ctx.settings.pipeline_root, on_line,
                                    ctx.work / "track_mesh.log")
        if "task" in state:
            await state["task"]
        if state["persons"] == []:
            raise VvlogError("NO_PERSON_FOUND", "人物が見つかりませんでした")
        raise_for_exit(code, ctx.work / "track_mesh.log", "offline_app_lite.py")
        if state["persons"] is None:                  # no sam3_propagate line (older output): publish now
            await publish_persons()
            if state["persons"] == []:
                raise VvlogError("NO_PERSON_FOUND", "人物が見つかりませんでした")
        video = self.outputs.latest_mesh_video(pout)
        if video:
            r.artifact("mesh_preview", await asyncio.to_thread(self.outputs.to_h264, video, pub / "mesh_preview.mp4"))
        r.stage_finished("mesh")
