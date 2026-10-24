"""Check scripts/occlusion/fusion_v2/edgetam_tracker.py against EdgeTAM's own code paths, bit for bit:
  1. utils/lazy_frames.DiskFrames[i] == load_video_frames_from_jpg_images(...)[i] (EdgeTAM init_state's preloaded
     frames) for every frame, and == SAM3's load_video_frames (SAM3 tracker frames, sam3_tracker.py);
  2. masks with the output pruning == masks without it, for one object tracked over the whole clip.

usage: check_edgetam_tracker.py <pipeline_out> [--seed_cands <cands.npz> --track N]
  the object starts from the first row of track N in a cands.npz (default: a box in the middle of frame 0)
"""
import argparse
import os
import sys

import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "scripts", "occlusion", "fusion_v2"), ROOT]
import edgetam_tracker as et  # noqa: E402
from utils.lazy_frames import DiskFrames  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("pipeline_out")
ap.add_argument("--seed_cands", default=None)
ap.add_argument("--track", type=int, default=0)
a = ap.parse_args()
img_dir = os.path.join(a.pipeline_out, "images")
paths = [os.path.join(img_dir, p) for p in sorted(os.listdir(img_dir)) if p.endswith(".jpg")]
ok = True

et._import_sam2()
from sam2.utils.misc import load_video_frames_from_jpg_images  # noqa: E402
ref, H, W = load_video_frames_from_jpg_images(img_dir, 1024, offload_video_to_cpu=True)
df = DiskFrames(paths, 1024, et.IMG_MEAN, et.IMG_STD)
same = [torch.equal(ref[i], df[i]) and ref[i].dtype == df[i].dtype for i in range(len(ref))]
print(f"EdgeTAM frames: {len(ref)}, size {(H, W)} vs {(df.video_height, df.video_width)}, identical "
      f"{sum(same)}/{len(same)}")
ok &= all(same) and (H, W) == (df.video_height, df.video_width) and len(df) == len(ref)
del ref
sys.path.insert(0, os.path.join(ROOT, "models", "sam3"))
from sam3.model.utils import sam2_utils  # noqa: E402
ref, h3, w3 = sam2_utils.load_video_frames(video_path=img_dir, image_size=1008, offload_video_to_cpu=True)
d3 = DiskFrames(paths, 1008, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
same = [torch.equal(ref[i], d3[i]) and ref[i].dtype == d3[i].dtype for i in range(len(ref))]
print(f"SAM3 frames: {len(ref)}, identical {sum(same)}/{len(same)}")
ok &= all(same) and (h3, w3) == (H, W)
del ref

if a.seed_cands:
    with np.load(a.seed_cands) as c:
        r = int(np.nonzero(c["track"] == a.track)[0][0])
        i0 = int(c["frame"][r])
        seed = np.unpackbits(c["masks"][r])[:H * W].reshape(H, W).astype(bool)
else:
    i0, seed = 0, np.zeros((H, W), bool)
    seed[H // 3:2 * H // 3, W // 3:2 * W // 3] = True
masks = {}
for prune in (True, False):
    tk = et.EdgeTAMTracker(paths, prune=prune)
    tr, m = tk.start(i0, tk.features(i0), seed)
    out = [m]
    for i in range(i0 + 1, len(paths)):
        out.append(tk.step(tr, i, tk.features(i)))
    kept = len(tr.state["output_dict"]["non_cond_frame_outputs"])
    tk.stop(tr)
    masks[prune] = out
    print(f"prune={prune}: {len(out)} frames from frame {i0}, area first/last {out[0].sum()}/{out[-1].sum()}, "
          f"non-conditioning outputs held at the end: {kept}")
    del tk
    torch.cuda.empty_cache()
same = [np.array_equal(x, y) for x, y in zip(masks[True], masks[False])]
print(f"pruned vs unpruned masks: identical {sum(same)}/{len(same)}")
ok &= all(same)
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
