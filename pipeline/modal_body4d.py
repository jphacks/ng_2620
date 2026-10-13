"""SAM-Body4D on Modal (scripts/offline_app.py on a cloud GPU).

Volumes
  sam-body4d-ckpt  /ckpt  checkpoints, same layout as the local ckpt_root, plus
                          vitdet/model_final_f05665.pkl (sam_3d_body.detector_path)
  sam-body4d-data  /data  inputs: frame folders or .mp4 files
  sam-body4d-out   /out   one folder per run: pipeline_out/, captures/, logs/, mesh_overlay.mp4

Each function can be used on its own, and the results stay in the volume:
  modal volume put sam-body4d-data <local frames dir> PennAction/frames/0826
  modal run modal_body4d.py::check                                   # environment smoke test
  modal run modal_body4d.py::run --input PennAction/frames/0826 --run-name penn0826
  modal run modal_body4d.py::overlay --run-name penn0826             # final video only
  modal run modal_body4d.py --input PennAction/frames/0826 --run-name penn0826   # run + overlay
  modal volume get sam-body4d-out penn0826/mesh_overlay.mp4 <local dir>
"""
from pathlib import Path

import modal

REPO = Path(__file__).parent
R = "/root/sam-body4d"
GPU = "L40S"  # 48 GB; detectron2 below is built for sm_80/86/89/90, so A100/H100 also work

ckpt = modal.Volume.from_name("sam-body4d-ckpt")
data = modal.Volume.from_name("sam-body4d-data")
out = modal.Volume.from_name("sam-body4d-out")

# Same versions as the local .venv: torch/cu118 per README, detectron2@a1ce2f9 with
# CUDA ops (built against CUDA 11.8 from the devel image), MoGe@0744441 (pre-MoGe-3).
image = (
    modal.Image.from_registry("nvidia/cuda:11.8.0-devel-ubuntu22.04", add_python="3.12")
    .apt_install("git", "build-essential", "libgl1", "libegl1", "libgles2", "libglib2.0-0")
    .pip_install("torch==2.7.1", "torchvision==0.22.1", "torchaudio==2.7.1",
                 index_url="https://download.pytorch.org/whl/cu118")
    .pip_install_from_requirements(str(REPO / "requirements-modal.txt"))
    .pip_install(
        "git+https://github.com/microsoft/MoGe.git@07444410f1e33f402353b99d6ccd26bd31e469e8",
        "git+https://github.com/EasternJournalist/pipeline.git@1c511390d90226c00c101f34b84df26a0f8789b4",
        "git+https://github.com/EasternJournalist/utils3d.git@3fab839f0be9931dac7c8488eb0e1600c236e183",
        extra_options="--no-deps",
    )
    # add_python's interpreter was built with clang, so setuptools would call clang++ (not installed);
    # use the image's gcc/g++ 11, which nvcc 11.8 supports and torch was built with
    .env({"FORCE_CUDA": "1", "CUDA_HOME": "/usr/local/cuda", "TORCH_CUDA_ARCH_LIST": "8.0;8.6;8.9;9.0+PTX",
          "CC": "gcc", "CXX": "g++", "LDSHARED": "gcc -pthread -shared", "LDCXXSHARED": "g++ -pthread -shared"})
    .run_commands("pip install --no-build-isolation --no-deps "
                  "'git+https://github.com/facebookresearch/detectron2.git@a1ce2f956a1d2212ad672e3c47d53405c2fe4312'")
    # headless rendering: pyrender over EGL; pyglet's headless mode (EGL) instead of X11 for pyrender's Viewer import
    .env({"PYTHONPATH": f"{R}:{R}/models/sam3:{R}/models/sam_3d_body", "PYOPENGL_PLATFORM": "egl",
          "PYGLET_HEADLESS": "1"})
    .add_local_dir(REPO, R, ignore=[".git/**", ".venv/**", "outputs/**", "testdata/**", "gradio_tmp/**",
                                    "assets/**", "build/**", "*.egg-info/**", "**/__pycache__/**"])
)
app = modal.App("sam-body4d", image=image)
VOLS = {"/ckpt": ckpt, "/data": data, "/out": out}


def _write_config():
    """configs/body4d.yaml with ckpt_root and detector_path pointing at the ckpt volume."""
    from omegaconf import OmegaConf

    path = f"{R}/configs/body4d.yaml"
    cfg = OmegaConf.load(path)
    cfg.paths.ckpt_root = "/ckpt"
    cfg.sam_3d_body.detector_path = "/ckpt/vitdet"
    OmegaConf.save(cfg, path)  # interpolations like ${paths.ckpt_root} stay unresolved
    return OmegaConf.to_container(cfg, resolve=True)


@app.function(gpu=GPU, volumes=VOLS, timeout=900)
def check():
    """Smoke test: versions, detectron2 CUDA ops, headless pyrender, checkpoint files."""
    import os
    import subprocess

    import numpy as np
    import torch

    res = {"torch": torch.__version__, "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name(0)}
    from detectron2.layers import nms
    boxes = torch.tensor([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=torch.float32, device="cuda")
    res["detectron2_cuda_nms"] = nms(boxes, torch.tensor([0.9, 0.8, 0.7], device="cuda"), 0.5).tolist()
    import moge, sam3, sam_3d_body  # noqa: F401,E401
    res["imports"] = "ok"
    import pyrender
    import trimesh
    import sam_3d_body.visualization.renderer  # noqa: F401  (same import path offline_app uses)
    scene = pyrender.Scene()
    scene.add(pyrender.Mesh.from_trimesh(trimesh.creation.box(extents=(1, 1, 1))))
    cam_pose = np.eye(4)
    cam_pose[2, 3] = 3.0
    scene.add(pyrender.IntrinsicsCamera(fx=300, fy=300, cx=160, cy=120), pose=cam_pose)
    r = pyrender.OffscreenRenderer(320, 240)
    _, depth = r.render(scene)
    r.delete()
    res["pyrender_egl_depth_pixels"] = int((depth > 0).sum())  # ~100x100 expected for a 1 m cube at 3 m
    res["config"] = _write_config()
    res["ckpt_files"] = {os.path.relpath(os.path.join(d, f), "/ckpt"): os.path.getsize(os.path.join(d, f))
                         for d, _, fs in os.walk("/ckpt") for f in fs}
    res["nvidia_smi"] = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                                        "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()
    import json
    print(json.dumps(res, indent=1))  # `modal run ...::check` does not print return values
    return res


@app.function(gpu=GPU, volumes=VOLS, timeout=3600)
def run(input: str, run_name: str):
    """offline_app on /data/<input> with stage timing + HMR capture (scripts/eval/run_profiled.py).
    Works on local disk, then copies the run folder to /out/<run_name>."""
    import os
    import re
    import shutil
    import subprocess
    import sys

    _write_config()
    work = f"/tmp/{run_name}"
    os.makedirs(f"{work}/logs", exist_ok=True)
    with open(f"{work}/logs/env.txt", "w") as f:
        f.write(subprocess.run(["nvidia-smi"], capture_output=True, text=True).stdout)
    with open(f"{work}/logs/run.log", "w") as log:
        proc = subprocess.run(
            [sys.executable, "-u", f"{R}/scripts/eval/run_profiled.py", "--input_video", f"/data/{input}",
             "--output_dir", f"{work}/pipeline_out", "--capture", f"{work}/captures/hmr_outputs.pkl"],
            cwd="/tmp", stdout=log, stderr=subprocess.STDOUT)
    shutil.copytree(work, f"/out/{run_name}", dirs_exist_ok=True)
    out.commit()
    text = open(f"{work}/logs/run.log", encoding="utf-8", errors="replace").read()
    lines = [ln for ln in re.split(r"[\r\n]+", text) if re.match(r"\[(PROF|TIME|CAPTURE)\]", ln)]
    if proc.returncode != 0:
        raise RuntimeError(f"offline_app failed (exit {proc.returncode}); see /out/{run_name}/logs/run.log\n"
                           + "\n".join(re.split(r"[\r\n]+", text)[-30:]))
    return lines


@app.function(gpu=GPU, volumes={"/out": out}, timeout=1800)
def overlay(run_name: str, fps: int = 25):
    """Final output only: mesh overlay video from /out/<run_name>/pipeline_out."""
    import subprocess
    import sys

    out.reload()
    dst = f"/out/{run_name}/mesh_overlay.mp4"
    subprocess.run([sys.executable, f"{R}/scripts/eval/render_overlay.py", "--pipeline_out",
                    f"/out/{run_name}/pipeline_out", "--out", dst, "--fps", str(fps)], check=True)
    out.commit()
    return dst


@app.local_entrypoint()
def main(input: str, run_name: str, fps: int = 25):
    for line in run.remote(input, run_name):
        print(line)
    print(overlay.remote(run_name, fps))
