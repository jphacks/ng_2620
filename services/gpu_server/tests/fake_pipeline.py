"""FakePipelineRunner: plays a script's recorded output lines and writes its output files, without a GPU."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image


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


SCRIPTS = {"offline_app_lite.py": fake_offline_app_lite}


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
