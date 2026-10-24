"""Download the pipeline's weights (weights/manifest.json) and point the pipeline's config at them.

  HF_TOKEN=... python weights/fetch.py --dest $VVLOG_CKPT_ROOT [--write_config]

SAM3 and SAM-3D-Body are gated on Hugging Face: request access with the account of HF_TOKEN first (or switch the
manifest's repo to the team's private mirror, which must carry the SAM License text: designdocs/adr/0008).
--write_config sets paths.ckpt_root and sam_3d_body.detector_path of pipeline/configs/body4d.yaml (a per-machine
config, like SAM-Body4D's own scripts/setup.py generates it).
Files already present with the pinned sha256 are skipped; unpinned ones print their sha256 to pin in the manifest.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parent / "pipeline" / "configs" / "body4d.yaml"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def fetch(item: dict, dest_root: Path) -> Path:
    dest = dest_root / item["dest"]
    if dest.is_file() and (item.get("sha256") is None or sha256(dest) == item["sha256"]):
        print(f"ok      {item['dest']}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"fetch   {item['dest']}", flush=True)
    if item["source"] == "hf":
        from huggingface_hub import hf_hub_download
        got = hf_hub_download(item["repo"], item["filename"], token=os.environ.get("HF_TOKEN") or None)
        shutil.copyfile(got, dest)
    else:
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(item["url"]) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 22)
        os.replace(tmp, dest)
    digest = sha256(dest)
    if item.get("sha256") and digest != item["sha256"]:
        dest.unlink()
        sys.exit(f"sha256 mismatch for {item['dest']}: {digest} != {item['sha256']}")
    if not item.get("sha256"):
        print(f"        sha256 {digest} (not pinned)")
    return dest


def write_config(dest_root: Path) -> None:
    t = CONFIG.read_text(encoding="utf-8")
    root = dest_root.as_posix()
    t = re.sub(r'(?m)^(\s*ckpt_root:\s*).*$', lambda m: f'{m[1]}"{root}"', t, count=1)
    t = re.sub(r'(?m)^(\s*detector_path:\s*).*$', lambda m: f'{m[1]}"{root}/vitdet"', t, count=1)
    CONFIG.write_text(t, encoding="utf-8")
    print(f"config  {CONFIG}: ckpt_root = {root}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=os.environ.get("VVLOG_CKPT_ROOT", str(HERE)))
    ap.add_argument("--write_config", action="store_true")
    a = ap.parse_args()
    m = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    dest = Path(a.dest).resolve()
    for item in m["files"]:
        fetch(item, dest)
    from huggingface_hub import snapshot_download
    for c in m.get("hf_cache", []):
        print(f"cache   {c['repo']}", flush=True)
        snapshot_download(c["repo"], token=os.environ.get("HF_TOKEN") or None)
    if a.write_config:
        write_config(dest)


if __name__ == "__main__":
    main()
