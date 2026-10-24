"""Server settings from the environment (designdocs/architecture/overview §6)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    api_token: str
    data_root: Path
    ckpt_root: Path
    pipeline_root: Path
    python: str
    port: int
    gpu_ids: tuple[str, ...]
    max_targets: int
    fusion_depth: str
    tracker_size: int
    hmr_stride: int
    max_duration_sec: float
    max_long_side: int
    backend: str                  # "pipeline" (real) | "replay" (mock server)
    record_fixture: Path | None

    @classmethod
    def from_env(cls, env: dict | None = None, **over) -> "Settings":
        e = os.environ if env is None else env
        rec = e.get("VVLOG_RECORD_FIXTURE")
        s = dict(
            api_token=e.get("VVLOG_API_TOKEN", ""),
            data_root=Path(e.get("VVLOG_DATA_ROOT", "./data")).resolve(),
            ckpt_root=Path(e.get("VVLOG_CKPT_ROOT", "./weights")).resolve(),
            pipeline_root=Path(e.get("VVLOG_PIPELINE_ROOT", str(REPO / "pipeline"))).resolve(),
            python=e.get("VVLOG_PYTHON", "python"),
            port=int(e.get("VVLOG_PORT", "8765")),
            gpu_ids=tuple(g.strip() for g in e.get("VVLOG_GPU_IDS", "0").split(",") if g.strip()),
            max_targets=int(e.get("VVLOG_MAX_TARGETS", "4")),
            fusion_depth=e.get("VVLOG_FUSION_DEPTH", "da2_indoor_s"),
            tracker_size=int(e.get("VVLOG_TRACKER_SIZE", "672")),
            hmr_stride=int(e.get("VVLOG_HMR_STRIDE", "3")),
            max_duration_sec=float(e.get("VVLOG_MAX_DURATION_SEC", "30")),
            max_long_side=int(e.get("VVLOG_MAX_LONG_SIDE", "1920")),
            backend=e.get("VVLOG_BACKEND", "pipeline"),
            record_fixture=Path(rec).resolve() if rec else None,
        )
        s.update(over)
        return cls(**s)
