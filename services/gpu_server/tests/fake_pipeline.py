"""FakePipelineRunner: plays a script's recorded output lines and writes its output files, without a GPU."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from vvlog_server.adapters.h264 import video_args


def _arg(cmd, name):
    return Path(cmd[cmd.index(name) + 1])


def fake_offline_app_lite(cmd, persons=(1, 2)):
    proxy, pout = _arg(cmd, "--input_video"), _arg(cmd, "--output_dir")
    frames = sorted(proxy.glob("*.jpg"))
    (pout / "masks").mkdir(parents=True, exist_ok=True)
    w, h = Image.open(frames[0]).size
    lines = ["[STAGE][detect] time=1.00 s, peak_alloc=1.00 GB", "[STAGE][sam3_load] time=1.00 s, peak_alloc=1.00 GB",
             "[STAGE][sam3_init_state] time=0.10 s, peak_alloc=1.00 GB"]
    for f in range(len(frames)):
        lab = np.zeros((h, w), np.uint8)
        for k, pid in enumerate(persons):
            x0 = 10 + k * (w // 2) + f % 5
            lab[h // 4: h // 4 + h // 2, x0: x0 + w // 4] = pid
        im = Image.fromarray(lab, mode="P")
        im.putpalette([0, 0, 0, 255, 0, 0, 0, 255, 0] + [0] * (256 * 3 - 9))
        im.save(pout / "masks" / f"{f:08d}.png")
        lines.append(f" {100 * (f + 1) // len(frames)}%|###| {f + 1}/{len(frames)} [00:01<00:02,  9.0it/s]")
    lines.append("[STAGE][sam3_propagate] time=3.00 s, peak_alloc=1.20 GB")
    lines += ["[STAGE][fov] time=1.00 s, peak_alloc=1.00 GB", "[STAGE][hmr_load] time=1.00 s, peak_alloc=1.00 GB"]
    lines += [f"{50 * i}%|##| {i}/2 [00:01<00:01, 1.0it/s]" for i in (1, 2)]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30:duration=1",
                    "-c:v", "mpeg4", str(pout / "4d_1.mp4")], check=True)
    lines.append("[STAGE][hmr_4d] time=5.00 s, peak_alloc=2.00 GB")
    return lines, 0


def fake_occlusion(cmd):
    pout, occ = _arg(cmd, "--pipeline_out"), _arg(cmd, "--out")
    n = len(list((pout / "masks").glob("*.png")))
    (occ / "units").mkdir(parents=True, exist_ok=True)
    lines = []
    for f in range(n):
        Image.fromarray(np.full((8, 8), 1 if f < n // 2 else 0, np.uint16)).save(occ / "units" / f"{f:05d}.png")
        lines.append(f"{_arg(cmd, '--name').name} {f} pts 12 raw 5 kept 3 t 0.01")
    (occ / "fusion_state.pkl").write_bytes(b"state")
    import json
    (occ / "units.json").write_text(json.dumps({"units": [{"unitId": 1, "frames": [0, n // 2 - 1], "label": "object",
                                                         "tracks": [3], "areaPx": 64, "perPerson": {}}]}))
    lines += ["x gpu stage 3.0 s", "[fusion_v2] x: 60 frames, pids [1, 2]; prep 1s, gpu 2s, cpu 1s, total 4s"]
    return lines, 0


def fake_avatar_app(cmd):
    import json
    pout, av, name = _arg(cmd, "--pipeline_out"), _arg(cmd, "--out"), cmd[cmd.index("--name") + 1]
    targets = [int(x) for x in cmd[cmd.index("--targets") + 1].split(",")]
    if "--overrides" in cmd:                     # export: one mp4, no layers
        n = len(list((pout / "masks").glob("*.png")))
        av.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=2",
                        *video_args(12), str(av / f"avatar_{name}.mp4")], check=True)
        return [f"[avatar] target {targets[0]}: x"] + [f"[avatar] frame {f + 1}/{n}" for f in range(n)], 0
    n = len(list((pout / "masks").glob("*.png")))
    lay = av / "layers"
    lines = ["[avatar] fusion v2 decisions from x", f"[avatar] target {targets[0]}: avatar scale 1.0"]
    for sub in ["clean"] + [f"avatar/{p}" for p in targets] + [f"hide/{p}" for p in targets]:
        (lay / sub).mkdir(parents=True, exist_ok=True)
    (av / "frames").mkdir(parents=True, exist_ok=True)
    for f in range(n):
        Image.new("RGB", (360, 640)).save(av / "frames" / f"{f:08d}.jpg")
        (lay / "clean" / f"{f:05d}.webp").write_bytes(b"w")
        for p in targets:
            (lay / "avatar" / str(p) / f"{f:05d}.webp").write_bytes(b"w")
            (lay / "hide" / str(p) / f"{f:05d}.png").write_bytes(b"p")
        lines.append(f"[avatar] frame {f + 1}/{n}")
    ids = dict(s.split("=") for s in cmd[cmd.index("--avatar_ids") + 1].split(","))
    (lay / "layers.json").write_text(json.dumps({"persons": targets, "order": {str(f): targets for f in range(n)},
                                                 "avatarIds": ids}))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30:duration=1",
                    "-c:v", "mpeg4", str(av / f"avatar_{name}.mp4")], check=True)
    (av / "stats.json").write_text(json.dumps({"frames": n}))
    return lines, 0


SCRIPTS = {"offline_app_lite.py": fake_offline_app_lite, "run_occlusion_stage.py": fake_occlusion,
           "avatar_app.py": fake_avatar_app}


class FakePipelineRunner:
    calls: list = []

    def __init__(self, scripts=None):
        self.scripts = dict(SCRIPTS, **(scripts or {}))
        self.terminated = False

    async def run(self, cmd, env, cwd, on_line, log_path):
        FakePipelineRunner.calls.append((list(map(str, cmd)), dict(env)))
        script = Path(cmd[2]).name
        lines, code = self.scripts[script](list(map(str, cmd)))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as log:
            for ln in lines:
                if self.terminated:
                    return -15
                log.write(ln + "\n")
                on_line(ln)
                await asyncio.sleep(0.001)
        return code

    async def terminate(self):
        self.terminated = True
