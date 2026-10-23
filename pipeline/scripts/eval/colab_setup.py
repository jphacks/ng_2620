"""Set up a Colab GPU VM for scripts/eval/profile_stages.py (run on the VM: colab exec -s <session> -f this file).

Expects /content/code.tar (git ls-files of this repo) and /content/data.tar.part_* (frame folders) uploaded with
`colab upload`, and a Hugging Face token with access to facebook/sam3 and facebook/sam-3d-body-dinov3 on the VM
(huggingface-cli login, or $HF_TOKEN). Checkpoints go to /content/ckpt on the VM only (nothing is cached locally);
configs/body4d.yaml of the VM copy points there.
Colab ships Python 3.13 + a recent torch: the repo's pins that have no 3.13 wheels (numpy==1.26, decord) are skipped,
the packages are installed with --no-deps against Colab's torch.
"""
import glob
import os
import subprocess

REPO, DATA, CKPT = "/content/repo", "/content/data", "/content/ckpt"


def sh(cmd):
    print("$", cmd, flush=True)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print((r.stdout + r.stderr)[-3000:], flush=True)
    if r.returncode:
        raise SystemExit(f"failed ({r.returncode}): {cmd}")


if not os.path.isdir(REPO):
    sh(f"mkdir -p {REPO} && tar xf /content/code.tar -C {REPO}")
if not os.path.isdir(DATA):
    sh(f"mkdir -p {DATA} && cat /content/data.tar.part_* | tar xf - -C {DATA}")

PKGS = ("opencv-python~=4.12.0.88 einops~=0.8.1 pycocotools~=2.0.10 roma~=1.5.4 omegaconf~=2.3.0 pytorch_lightning "
        "yacs~=0.1.8 fvcore pyrender~=0.1.45 termcolor~=3.2.0 diffusers==0.29.1 transformers~=4.57.3 accelerate~=1.12.0 "
        "imageio[ffmpeg] timm ftfy regex iopath trimesh braceexpand~=0.1.7 cloudpickle~=3.1.2 psutil "
        "huggingface_hub torchmetrics webdataset")   # pyproject.toml's pins (minus gradio / decord), sam3's deps
sh(f"pip install -q {PKGS}")
# MoGe imports utils3d_moge first when present; the moge v2 code here calls utils3d.pt, which only upstream utils3d
# has. huggingface-hub goes back under 1.0 for transformers 4.57.
sh("pip install -q 'git+https://github.com/microsoft/MoGe.git' && pip uninstall -y -q utils3d_moge && "
   "pip install -q --no-deps 'git+https://github.com/EasternJournalist/utils3d.git' 'huggingface-hub<1.0'")
sh(f"pip install -q --no-deps -e {REPO}/models/sam3")
# sam3 imports decord at module level (its training datasets); only video files read through it. Frame folders are
# the profiling inputs, so a stub is enough where no 3.13 build installs.
sh("pip install -q eva-decord || python3 -c \"import site; open(site.getsitepackages()[0] + '/decord.py', 'w')"
   ".write('cpu = VideoReader = None')\"")
try:
    import detectron2  # noqa: F401
except ImportError:
    sh("pip install -q --no-build-isolation --no-deps 'git+https://github.com/facebookresearch/detectron2.git@a1ce2f9'")

# checkpoints (VM only)
from huggingface_hub import hf_hub_download  # noqa: E402
for repo, fn, out in [("facebook/sam3", "sam3.pt", "sam3"),
                      ("facebook/sam-3d-body-dinov3", "model.ckpt", "sam-3d-body-dinov3"),
                      ("facebook/sam-3d-body-dinov3", "model_config.yaml", "sam-3d-body-dinov3"),
                      ("facebook/sam-3d-body-dinov3", "assets/mhr_model.pt", "sam-3d-body-dinov3"),
                      ("Ruicheng/moge-2-vitl-normal", "model.pt", "moge-2-vitl-normal")]:
    hf_hub_download(repo, fn, local_dir=f"{CKPT}/{out}")
sh(f"sed -i 's|^  ckpt_root: .*|  ckpt_root: \"{CKPT}\"|' {REPO}/configs/body4d.yaml && grep ckpt_root {REPO}/configs/body4d.yaml")
sh(f"du -sh {CKPT}/*")
print("clips:", sorted(os.path.basename(d) for d in glob.glob(f"{DATA}/*")))
