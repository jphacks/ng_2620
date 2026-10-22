"""Check utils/lazy_frames.py: every frame must equal SAM3's preloading loader bit for bit.

usage: check_lazy_frames.py <jpg folder> [--limit N]   (the list path is checked on the first N frames)
"""
import argparse
import os
import sys

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [ROOT, os.path.join(ROOT, "models", "sam3")]
from sam3.model.utils import sam2_utils  # noqa: E402
from utils.lazy_frames import lazy_jpeg_frames  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("folder")
ap.add_argument("--limit", type=int, default=None, help="frames for the list-path check (default: all)")
a = ap.parse_args()
kw = dict(image_size=1008, offload_video_to_cpu=True)
paths = sorted(os.path.join(a.folder, p) for p in os.listdir(a.folder) if p.endswith(".jpg"))[:a.limit]

ok = True
for name, src in (("list", paths),) + ((("dir", a.folder),) if a.limit is None else ()):
    ref, h, w = sam2_utils.load_video_frames(video_path=src, **kw)
    with lazy_jpeg_frames():
        lazy, lh, lw = sam2_utils.load_video_frames(video_path=src, **kw)
    same = [torch.equal(ref[i], lazy[i]) and ref[i].dtype == lazy[i].dtype for i in range(len(ref))]
    print(f"{name}: {len(ref)} frames, size {(h, w)} vs {(lh, lw)}, len {len(lazy)}, identical {sum(same)}/{len(same)}")
    ok &= all(same) and (h, w) == (lh, lw) and len(ref) == len(lazy)
assert sam2_utils.load_video_frames_from_jpg_images.__module__ == sam2_utils.__name__, "loader not restored"
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
