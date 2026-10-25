"""render: removal -> avatar -> layers in one avatar_app.py process, with the analyze job's fusion decisions
(designdocs/features/avatar-render). Outputs in work/<renderJobId>/av/: layers/, avatar_<jobId>.mp4, stats.json."""
from __future__ import annotations

import asyncio
import json

from ...domain.errors import VvlogError
from ..ports import AvatarStorePort, PipelineOutputsPort
from . import progress as P
from .base import JobContext, raise_for_exit
from .pipeline_env import pipeline_cmd, pipeline_env


def avatar_args(ctx: JobContext, avatars: AvatarStorePort) -> list[str]:
    """--targets / --vrm_map / --avatar_ids / finish flags of a render job's params (export reuses them)."""
    render = ctx.job if ctx.job.type == "render" else ctx.deps["render"]
    cast = render.params["cast"]
    vrms, ids = [], []
    for c in cast:
        path, _ = avatars.get(c["avatarId"])
        if not path.is_file():
            raise VvlogError("AVATAR_INVALID", "VRM ファイルがサーバーにありません", {"personId": c["personId"],
                                                                                   "avatarId": c["avatarId"]})
        vrms.append(f"{c['personId']}={path}")
        ids.append(f"{c['personId']}={c['avatarId']}")
    fin = render.params.get("finish") or {}
    args = ["--targets", ",".join(str(c["personId"]) for c in cast), "--vrm_map", ",".join(vrms),
            "--avatar_ids", ",".join(ids), "--smooth", str(fin.get("smooth", 0.05))]
    if fin.get("footLock") is False:
        args.append("--no_foot_lock")
    if fin.get("spring") is False:
        args.append("--no_spring")
    return args


def check_targets(ctx: JobContext) -> None:
    persons_file = ctx.work_of("analyze") / "_published_persons.json"
    if persons_file.is_file():
        known = {p["personId"] for p in json.loads(persons_file.read_text(encoding="utf-8"))}
        render = ctx.job if ctx.job.type == "render" else ctx.deps["render"]
        bad = [c["personId"] for c in render.params["cast"] if c["personId"] not in known]
        if bad:
            raise VvlogError("VALIDATION_FAILED", "解析で見つかっていない人物が指定されています", {"personIds": bad})


class RenderStage:
    names = ("removal", "avatar", "layers")

    def __init__(self, avatars: AvatarStorePort, outputs: PipelineOutputsPort):
        self.avatars, self.outputs = avatars, outputs

    async def run(self, ctx: JobContext) -> None:
        r = ctx.report
        an = ctx.work_of("analyze")
        av, pub = ctx.work / "av", ctx.work / "_publish"
        meta = json.loads((an / "meta.json").read_text(encoding="utf-8"))
        check_targets(ctx)
        r.stage_started("removal")
        state = {"stage": "removal"}

        def on_line(line: str) -> None:
            s = line.strip()
            m = P.AVATAR_FRAME.match(s)
            if m:
                if state["stage"] == "removal":
                    r.stage_finished("removal")
                    r.stage_started("avatar")
                    state["stage"] = "avatar"
                done, total = int(m[1]), int(m[2])
                r.progress("avatar", done / max(1, total), message=f"frame {done}/{total}")
            elif state["stage"] == "removal":
                if s.startswith("[avatar] fusion v2 decisions"):
                    r.progress("removal", 0.3)
                elif s.startswith("[avatar] target"):
                    r.progress("removal", 0.6)

        cmd = pipeline_cmd(ctx.settings, "scripts/avatar_app.py", "--pipeline_out", an / "pout", "--out", av,
                           "--name", ctx.job.jobId, "--gpu_removal", "--fps", meta["fps"],
                           "--fusion_state", an / "occ" / "fusion_state.pkl", "--export_layers", av / "layers",
                           "--no_compare", *avatar_args(ctx, self.avatars))
        log = ctx.work / "render.log"
        code = await ctx.runner.run(cmd, pipeline_env(ctx), ctx.settings.pipeline_root, on_line, log)
        raise_for_exit(code, log, "avatar_app.py")
        if state["stage"] == "removal":
            r.stage_finished("removal")
            r.stage_started("avatar")
        r.stage_finished("avatar")

        r.stage_started("layers")
        layers = av / "layers"
        if not (layers / "layers.json").is_file():
            raise VvlogError("PIPELINE_FAILED", "レイヤーが書き出されていません", {"dir": str(layers)})
        pub.mkdir(parents=True, exist_ok=True)
        r.artifact("layers_index", layers / "layers.json")
        zipped = await asyncio.to_thread(self.outputs.zip_layers, layers, pub / "layers.zip")
        r.progress("layers", 0.7, force=True)
        r.artifact("layers", zipped)
        preview = await asyncio.to_thread(self.outputs.frames_to_h264, av / "frames", meta["fps"], pub / "preview.mp4")
        if preview is not None:
            r.artifact("preview_video", preview)
        if (av / "stats.json").is_file():
            r.artifact("stats", av / "stats.json")
        r.stage_finished("layers")
