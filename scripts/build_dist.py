#!/usr/bin/env python3
"""配布物(Windows)の中身を dist/vvlog/ に並べ、ポータブル zip を作る(#34 #41)。インストーラーは installer/vvlog.iss がこの中身を詰める。

    python scripts/build_dist.py --version 0.1.1 [--spa apps/desktop/dist] [--uv uv.exe] [--ffmpeg-dir bin/] [--out dist-win]

薄い配布物の方針(ADR-0015): uv・ffmpeg/ffprobe・サーバーのコード・pipeline のコード・画面・ランチャーだけ。
Python・torch・重みは入れない(初回に vvlog.cmd / ブートストラップが取得する)。--uv / --ffmpeg-dir を省くとその分は入らない(中身の検査用)。
第三者のソフトウェアは NOTICE と licenses/。同梱した ffmpeg の版とソースの場所は、ここで NOTICE の末尾に書き足す。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", ".venv", "tests", ".pytest_cache", "*.pt", "*.ckpt", "*.pth", ".git")


def ffmpeg_notice(ffmpeg: Path) -> str:
    """The bundled build's version and source, appended to NOTICE. NOTICE states LGPL-3.0-or-later, so any other
    build (GPL, nonfree, LGPL-2.1) stops the build."""
    out = subprocess.run([str(ffmpeg), "-hide_banner", "-version"], capture_output=True, text=True, check=True).stdout
    version = out.split()[2]                                   # "ffmpeg version <version> Copyright ..."
    conf = next((line for line in out.splitlines() if line.startswith("configuration:")), "")
    if "--enable-gpl" in conf or "--enable-nonfree" in conf or "--enable-version3" not in conf:
        sys.exit(f"NOTICE と違うライセンスの ffmpeg です（LGPL-3.0-or-later のビルドを使う）: {version}")
    m = re.search(r"-g([0-9a-f]{7,40})", version)
    source = f"https://github.com/FFmpeg/FFmpeg/tree/{m[1]}" if m else "https://ffmpeg.org/download.html#get-sources"
    return f"\nFFmpeg build in bin/\n  Version: {version}\n  Source: {source}\n"


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
    shutil.copytree(ROOT / "licenses", stage / "licenses")
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
    shutil.copy2(ROOT / "NOTICE", stage / "NOTICE")
    if ffmpeg_dir:
        for n in ("ffmpeg.exe", "ffprobe.exe"):
            shutil.copy2(ffmpeg_dir / n, stage / "bin" / n)
        # BtbN's zip: <root>/bin/ffmpeg.exe and <root>/LICENSE.txt
        lic = next((p for p in (ffmpeg_dir / "LICENSE.txt", ffmpeg_dir.parent / "LICENSE.txt") if p.is_file()), None)
        if lic is None:
            sys.exit(f"ffmpeg のライセンス文（LICENSE.txt）がありません: {ffmpeg_dir} とその親")
        shutil.copy2(lic, stage / "licenses" / "ffmpeg-LICENSE.txt")
        with open(stage / "NOTICE", "a", encoding="utf-8", newline="\n") as f:
            f.write(ffmpeg_notice(ffmpeg_dir / "ffmpeg.exe"))
    shutil.copy2(ROOT / "installer" / "vvlog.cmd", stage / "vvlog.cmd")
    shutil.copy2(ROOT / "apps" / "desktop" / "assets" / "icons" / "icon.ico", stage / "vvlog.ico")
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
