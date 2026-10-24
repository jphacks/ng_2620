"""How the server starts a pipeline script: interpreter, working directory and environment
(designdocs/architecture/pipeline-integration §3.7)."""
from __future__ import annotations

import sys
from pathlib import Path

from .base import JobContext


def pipeline_cmd(settings, script: str, *args) -> list[str]:
    return [settings.python, "-u", str(Path(settings.pipeline_root) / script), *map(str, args)]


def pipeline_env(ctx: JobContext) -> dict:
    s = ctx.settings
    env = {
        "USE_TF": "0", "USE_JAX": "0", "USE_FLAX": "0",          # TF / JAX imports break pyrender's EGL context
        "CUDA_VISIBLE_DEVICES": str(ctx.extra.get("gpu_id", "0")),
        "FUSION_DEPTH": s.fusion_depth,
        "PYTHONIOENCODING": "utf-8",
        "MPLBACKEND": "Agg",          # headless: never inherit a notebook / GUI backend (Colab sets matplotlib_inline)
    }
    if sys.platform.startswith("linux"):
        env.update({"PYOPENGL_PLATFORM": "egl", "MODERNGL_BACKEND": "egl"})
    return env
