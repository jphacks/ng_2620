"""Check that offline_app_lite.share_mhr leaves the hand head's outputs bit-identical and frees the copy.

usage (repo root): .venv/Scripts/python.exe scripts/eval/check_share_mhr.py
"""
import os
import sys

import torch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import offline_app as oa  # noqa: E402
import offline_app_lite as L  # noqa: E402
from omegaconf import OmegaConf  # noqa: E402
from utils.skip_init import skip_random_init  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
cfg = OmegaConf.load(os.path.join(ROOT, "configs", "body4d.yaml"))
with skip_random_init():
    model, _ = oa.load_sam_3d_body(cfg.sam_3d_body["ckpt_path"], device=torch.device("cuda"),
                                   mhr_path=cfg.sam_3d_body["mhr_path"])
g = torch.Generator(device="cuda").manual_seed(0)
xs = [torch.randn(b, 1024, device="cuda", generator=g) * 0.1 for b in (1, 16)]  # pose tokens (DECODER.DIM)
with torch.no_grad():
    ref = [model.head_pose_hand(x) for x in xs]
    ref2 = [model.head_pose_hand(x) for x in xs]
    before = torch.cuda.memory_allocated()
    L.share_mhr(model)
    freed = (before - torch.cuda.memory_allocated()) / 2**30
    assert model.head_pose_hand.mhr is model.head_pose.mhr
    out = [model.head_pose_hand(x) for x in xs]


def diff(a, b):
    """max |a-b| per output key (the MHR itself is not bitwise repeatable, so compare against a plain repeat)"""
    return {k: (a[k] - b[k]).abs().max().item() for k in a
            if torch.is_tensor(a[k]) and a[k].is_floating_point() and a[k].numel()}


for i, b in enumerate((1, 16)):
    rep, shr = diff(ref[i], ref2[i]), diff(ref[i], out[i])
    print(f"B={b}: max diff  repeat {max(rep.values()):.3g} ({max(rep, key=rep.get)})  "
          f"shared {max(shr.values()):.3g} ({max(shr, key=shr.get)})")
print(f"freed {freed:.3f} GB")
