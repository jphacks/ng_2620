#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import urllib.request

from huggingface_hub import hf_hub_download, snapshot_download
from huggingface_hub.utils import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError


# -------------------------
# Defaults (edit if needed)
# -------------------------
DEFAULT_RELEASE_TAG = "v0.1.0"
DEFAULT_OWNER_REPO = "gaomingqi/sam-body4d"

TEMPLATE_ASSET_NAME = "config.template.yaml"
GENERATED_CONFIG_PATH = "configs/body4d.yaml"  # your final runtime config


# -------------------------
# Data specs
# -------------------------
@dataclass(frozen=True)
class URLFile:
    name: str
    url: str
    rel_out: str  # relative to ckpt_root


@dataclass(frozen=True)
class HFFile:
    name: str
    repo_id: str
    filename: str
    rel_out: str  # relative to ckpt_root
    gated: bool = False
    revision: str = "main"


@dataclass(frozen=True)
class HFRepoDir:
    name: str
    repo_id: str
    rel_out_dir: str  # relative to ckpt_root
    gated: bool = False
    revision: str = "main"


# -------------------------
# FS helpers
# -------------------------
def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def expand_abs(p: str) -> Path:
    return Path(p).expanduser().resolve()


def file_ok(p: Path) -> bool:
    return p.exists() and p.is_file() and p.stat().st_size > 0


def complete_marker(dir_path: Path) -> Path:
    return dir_path / ".complete"


def dir_complete(dir_path: Path) -> bool:
    # "exists" for repo dirs means: non-empty + has .complete marker
    return dir_path.exists() and dir_path.is_dir() and any(dir_path.iterdir()) and complete_marker(dir_path).exists()


# -------------------------
# GitHub Release template
# -------------------------
def release_template_url(owner_repo: str, tag: str) -> str:
    return f"https://github.com/{owner_repo}/releases/download/{tag}/{TEMPLATE_ASSET_NAME}"


def download_url_atomic(url: str, out_path: Path, name: str) -> None:
    """
    Atomic-ish URL download:
      - downloads to out_path + ".part"
      - renames to out_path on success
    This prevents half-downloaded files being treated as "exists".
    """
    ensure_dir(out_path.parent)

    if file_ok(out_path):
        print(f"[SKIP] {name}")
        return

    tmp = out_path.with_suffix(out_path.suffix + ".part")
    if tmp.exists():
        # leftover from an interrupted run
        try:
            tmp.unlink()
        except Exception:
            pass

    print(f"[DL]   {name}")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        f.write(r.read())

    if not file_ok(tmp):
        raise RuntimeError(f"Downloaded file is empty: {tmp}")

    tmp.replace(out_path)
    print(f"[OK]   {name}")


def generate_config_from_template(template_path: Path, out_path: Path, ckpt_root: Path, force: bool) -> None:
    ensure_dir(out_path.parent)
    if out_path.exists() and not force:
        print("[SKIP] Generated config exists (use --no-force to keep it).")
        return

    txt = template_path.read_text(encoding="utf-8")
    txt = txt.replace("${CKPT_ROOT}", str(ckpt_root))
    out_path.write_text(txt, encoding="utf-8")
    print(f"[OK]   Generated config: {out_path}")


# -------------------------
# HF auth helpers
# -------------------------
def get_cached_hf_token() -> Optional[str]:
    tok = os.getenv("HF_TOKEN")
    if tok:
        return tok
    try:
        from huggingface_hub import HfFolder
        tok2 = HfFolder.get_token()
        return tok2
    except Exception:
        return None


def maybe_prompt_token(allow_prompt: bool) -> Optional[str]:
    tok = get_cached_hf_token()
    if tok:
        return tok
    if not allow_prompt:
        return None
    print("Hugging Face login may be required for some checkpoints.")
    token = getpass.getpass("Paste your HF access token (hidden), or press Enter to skip: ").strip()
    return token or None


# -------------------------
# HF download helpers
# -------------------------
def hf_download_file(item: HFFile, ckpt_root: Path, token: Optional[str]) -> bool:
    out_path = ckpt_root / item.rel_out
    ensure_dir(out_path.parent)

    if file_ok(out_path):
        print(f"[SKIP] {item.name}")
        return True

    try:
        real_path = hf_hub_download(
            repo_id=item.repo_id,
            filename=item.filename,
            revision=item.revision,
            local_dir=str(out_path.parent),
            local_dir_use_symlinks=False,
            token=token,
        )
        real_path = Path(real_path)
        if real_path.resolve() != out_path.resolve():
            out_path.write_bytes(real_path.read_bytes())

        if not file_ok(out_path):
            print(f"[BLOCKED] {item.name}: downloaded but output is empty")
            return False

        print(f"[OK]   {item.name}")
        return True

    except GatedRepoError:
        print(f"[BLOCKED] {item.name}: requires prior access approval + login on Hugging Face")
        return False
    except (RepositoryNotFoundError, HfHubHTTPError, OSError) as e:
        print(f"[BLOCKED] {item.name}: download failed ({e})")
        return False


def hf_download_repo_dir(item: HFRepoDir, ckpt_root: Path, token: Optional[str]) -> bool:
    """
    Robust "exist" check for directories:
      - if dir exists but missing .complete => treat as partial and re-download/continue
      - after successful snapshot_download => write .complete
    """
    out_dir = ckpt_root / item.rel_out_dir
    ensure_dir(out_dir)

    if dir_complete(out_dir):
        print(f"[SKIP] {item.name}")
        return True

    # If directory exists but not complete, we keep it and let snapshot_download resume.
    try:
        print(f"[DL]   {item.name} (repo dir)")
        snapshot_download(
            repo_id=item.repo_id,
            revision=item.revision,
            local_dir=str(out_dir),
            local_dir_use_symlinks=False,
            token=token,
            resume_download=True,
        )
        # Mark completion
        complete_marker(out_dir).write_text("ok\n", encoding="utf-8")
        print(f"[OK]   {item.name}")
        return True

    except GatedRepoError:
        print(f"[BLOCKED] {item.name}: requires prior access approval + login on Hugging Face")
        return False
    except (RepositoryNotFoundError, HfHubHTTPError, OSError) as e:
        print(f"[BLOCKED] {item.name}: download failed ({e})")
        return False


def print_manual_hints(ckpt_root: Path) -> None:
    print("\nManual placement (if downloads are blocked):")
    print(f"  CKPT_ROOT = {ckpt_root}")
    print("  sam3/sam3.pt")
    print("  sam-3d-body-dinov3/model.ckpt")
    print("  sam-3d-body-dinov3/model_config.yaml")
    print("  sam-3d-body-dinov3/assets/mhr_model.pt")
    print("  diffusion-vas-amodal-segmentation/  (directory)")
    print("  diffusion-vas-content-completion/  (directory)")
    print("  moge-2-vitl-normal/model.pt")
    print("  depth_anything_v2_vitl.pth\n")
    print("Rerun the setup script after placing files; existing files will be skipped.\n")


# -------------------------
# Specs (edit here)
# -------------------------
def build_specs() -> tuple[list[URLFile], list[HFRepoDir], list[HFFile]]:
    url_files = [
        URLFile(
            name="Depth Anything v2 (vitl)",
            url="https://huggingface.co/depth-anything/Depth-Anything-V2-Large/resolve/main/depth_anything_v2_vitl.pth?download=true",
            rel_out="depth_anything_v2_vitl.pth",
        ),
    ]

    repo_dirs = [
        HFRepoDir(
            name="Diffusion-VAS amodal segmentation",
            repo_id="kaihuac/diffusion-vas-amodal-segmentation",
            rel_out_dir="diffusion-vas-amodal-segmentation",
            gated=False,
        ),
        HFRepoDir(
            name="Diffusion-VAS content completion",
            repo_id="kaihuac/diffusion-vas-content-completion",
            rel_out_dir="diffusion-vas-content-completion",
            gated=False,
        ),
    ]

    hf_files = [
        HFFile(
            name="MoGe-2 ViTL Normal",
            repo_id="Ruicheng/moge-2-vitl-normal",
            filename="model.pt",
            rel_out="moge-2-vitl-normal/model.pt",
            gated=False,
        ),
        HFFile(
            name="SAM3",
            repo_id="facebook/sam3",
            filename="sam3.pt",
            rel_out="sam3/sam3.pt",
            gated=True,
        ),
        HFFile(
            name="SAM-3D-Body (model.ckpt)",
            repo_id="facebook/sam-3d-body-dinov3",
            filename="model.ckpt",
            rel_out="sam-3d-body-dinov3/model.ckpt",
            gated=True,
        ),
        HFFile(
            name="SAM-3D-Body (model_config.yaml)",
            repo_id="facebook/sam-3d-body-dinov3",
            filename="model_config.yaml",
            rel_out="sam-3d-body-dinov3/model_config.yaml",
            gated=True,
        ),
        HFFile(
            name="SAM-3D-Body (mhr_model.pt)",
            repo_id="facebook/sam-3d-body-dinov3",
            filename="assets/mhr_model.pt",
            rel_out="sam-3d-body-dinov3/assets/mhr_model.pt",
            gated=True,
        ),
    ]

    return url_files, repo_dirs, hf_files


# -------------------------
# Main
# -------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt-root", type=str, default=None, help="Checkpoint root (default: ./checkpoints)")
    ap.add_argument("--owner-repo", type=str, default=DEFAULT_OWNER_REPO, help="GitHub owner/repo for Releases")
    ap.add_argument("--release-tag", type=str, default=DEFAULT_RELEASE_TAG, help="Release tag (e.g., v0.1.0)")
    ap.add_argument("--no-force", dest="force", action="store_false",
                    help="Do not overwrite generated configs/body4d.yaml")
    ap.set_defaults(force=True)
    ap.add_argument("--prompt-hf-token", action="store_true", help="Prompt HF token during run if needed")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    ckpt_root = expand_abs(args.ckpt_root) if args.ckpt_root else expand_abs(str(repo_root / "checkpoints"))
    ensure_dir(ckpt_root)

    # 1) Download template config from Release
    tmpl_url = release_template_url(args.owner_repo, args.release_tag)
    local_tmpl = repo_root / "configs" / TEMPLATE_ASSET_NAME
    download_url_atomic(tmpl_url, local_tmpl, "Config template (release)")

    # 2) Generate configs/body4d.yaml (force by default)
    out_cfg = repo_root / GENERATED_CONFIG_PATH
    generate_config_from_template(local_tmpl, out_cfg, ckpt_root, force=args.force)

    # 3) Download all specs
    url_files, repo_dirs, hf_files = build_specs()

    # 3.1 URL files (atomic)
    ok = True
    for it in url_files:
        try:
            download_url_atomic(it.url, ckpt_root / it.rel_out, it.name)
        except Exception as e:
            ok = False
            print(f"[BLOCKED] {it.name}: download failed ({e})")

    # 3.2 Non-gated HF downloads (token optional)
    token_cached = get_cached_hf_token()

    for it in repo_dirs:
        ok &= hf_download_repo_dir(it, ckpt_root, token_cached)

    for it in hf_files:
        if not it.gated:
            ok &= hf_download_file(it, ckpt_root, token_cached)

    # 3.3 Gated HF downloads
    token = maybe_prompt_token(args.prompt_hf_token)
    ok_gated = True
    for it in hf_files:
        if it.gated:
            ok_gated &= hf_download_file(it, ckpt_root, token)

    if not ok_gated:
        print_manual_hints(ckpt_root)

    if ok and ok_gated:
        print("Setup finished. 🎉")
    else:
        print("Setup finished with some blocked downloads. See messages above.")

if __name__ == "__main__":
    main()
