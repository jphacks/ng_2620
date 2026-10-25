#!/usr/bin/env python3
"""配布物(Windows)の中身を dist/vvlog/ に並べ、ポータブル zip を作る(#34 #41)。インストーラーは installer/vvlog.iss がこの中身を詰める。

    python scripts/build_dist.py --version 0.1.1 [--spa apps/desktop/dist] [--uv uv.exe] [--ffmpeg-dir bin/] [--out dist-win]

薄い配布物の方針(ADR-0015): uv・ffmpeg/ffprobe・サーバーのコード・pipeline のコード・画面・ランチャーだけ。
Python・torch・重みは入れない(初回に vvlog.cmd / ブートストラップが取得する)。--uv / --ffmpeg-dir を省くとその分は入らない(中身の検査用)。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", ".venv", "tests", ".pytest_cache", "*.pt", "*.ckpt", "*.pth", ".git")


def build(version: str, out: Path, spa: Path | None, uv: Path | None, ffmpeg_dir: Path | None) -> Path:
    stage = out / "vvlog"
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "bin").mkdir(parents=True)
    shutil.copytree(ROOT / "services" / "gpu_server" / "vvlog_server", stage / "server" / "vvlog_server", ignore=IGNORE)
    for f in ("pyproject.toml", "bootstrap-spec.json"):
        shutil.copy2(ROOT / "services" / "gpu_server" / f, stage / "server" / f)
    shutil.copytree(ROOT / "pipeline", stage / "pipeline", ignore=IGNORE)
    (stage / "weights").mkdir()
    shutil.copy2(ROOT / "weights" / "manifest.json", stage / "weights" / "manifest.json")
    shutil.copy2(ROOT / "weights" / "fetch.py", stage / "weights" / "fetch.py")
    if spa:
        if not (spa / "index.html").is_file():
            sys.exit(f"画面のビルド成果物がありません: {spa}（pnpm --filter @vvlog/desktop build）")
        shutil.copytree(spa, stage / "spa")
    if uv:
        shutil.copy2(uv, stage / "bin" / "uv.exe")
    if ffmpeg_dir:
        for n in ("ffmpeg.exe", "ffprobe.exe"):
            shutil.copy2(ffmpeg_dir / n, stage / "bin" / n)
    shutil.copy2(ROOT / "installer" / "vvlog.cmd", stage / "vvlog.cmd")
    shutil.copy2(ROOT / "apps" / "desktop" / "assets" / "icons" / "icon.ico", stage / "vvlog.ico")
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
    (stage / "VERSION").write_text(version + "\n", encoding="utf-8")
    zpath = out / f"vvlog-{version}-windows-portable.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, Path("vvlog") / p.relative_to(stage))
    return zpath


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "dist-win")
    ap.add_argument("--spa", type=Path)
    ap.add_argument("--uv", type=Path)
    ap.add_argument("--ffmpeg-dir", type=Path)
    a = ap.parse_args()
    z = build(a.version, a.out.resolve(), a.spa, a.uv, a.ffmpeg_dir)
    print(f"stage: {a.out.resolve() / 'vvlog'}\nzip:   {z} ({z.stat().st_size // 1024 // 1024} MB)")
