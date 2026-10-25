"""Server settings from the environment (designdocs/architecture/overview §6)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _local_dirs(e) -> dict[str, Path]:
    """Where the local app keeps projects, app data and config (Windows first; the others follow XDG-ish defaults)."""
    home = Path(e.get("USERPROFILE") or e.get("HOME") or Path.home())
    if os.name == "nt":
        appdata = Path(e.get("LOCALAPPDATA") or home / "AppData" / "Local")
        config = Path(e.get("APPDATA") or home / "AppData" / "Roaming")
        base = {"projects": home / "Videos" / "vvlog", "appData": appdata / "vvlog", "config": config / "vvlog"}
    else:
        base = {"projects": home / "Videos" / "vvlog", "appData": home / ".local" / "share" / "vvlog",
                "config": home / ".config" / "vvlog"}
    for key, var in (("projects", "VVLOG_LOCAL_PROJECTS_DIR"), ("appData", "VVLOG_LOCAL_APPDATA_DIR"),
                     ("config", "VVLOG_LOCAL_CONFIG_DIR")):
        if e.get(var):
            base[key] = Path(e[var])
    return {k: v.resolve() for k, v in base.items()}


def _spa_dir(e) -> Path | None:
    given = e.get("VVLOG_SPA_DIR")
    if given:
        return Path(given).resolve()
    built = REPO / "apps" / "desktop" / "dist"
    return built if (built / "index.html").is_file() else None


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
    local: bool                   # local app server (ADR-0015): 127.0.0.1 only, serves the SPA, Host/Origin guard
    spa_dir: Path | None          # built SPA (apps/desktop/dist); local mode only
    local_dirs: dict                  # local mode: {projects, appData, config} folders (each is an allowed root)
    local_roots: tuple[Path, ...]     # local mode: further allowed roots (VVLOG_LOCAL_ROOTS, os.pathsep-separated)
    allowed_origins: tuple[str, ...]  # extra origins allowed to call a local server (the GitHub Pages origin)

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
            local=e.get("VVLOG_LOCAL", "").lower() in ("1", "true", "yes"),
            spa_dir=_spa_dir(e),
            local_dirs=_local_dirs(e),
            local_roots=tuple(Path(r).resolve() for r in e.get("VVLOG_LOCAL_ROOTS", "").split(os.pathsep) if r.strip()),
            allowed_origins=tuple(o.strip() for o in e.get("VVLOG_ALLOWED_ORIGINS", "").split(",") if o.strip()),
        )
        s.update(over)
        return cls(**s)
