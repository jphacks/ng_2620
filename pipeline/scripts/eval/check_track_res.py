"""SAM3 person tracking at a lower input resolution than the trained 1008: masks against 1008, and time.

Both runs use utils/sam3_track_fast (bit-identical to the plain tracker) and the same box prompts (the bounding boxes of
the pipeline_out's first-frame masks). Per frame and object: IoU of the two masks at the video resolution; also the
boundary F-score at 2 px (how far the outlines moved).

usage: python scripts/eval/check_track_res.py --frames <JPEG dir> --pipeline_out <dir> [--sizes 1008 672]
       [--save <dir>]   writes <dir>/<size>/masks.npy
"""
import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(REPO, "scripts"), REPO]

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402


def boundary_f(a, b, tol=2):
    k = np.ones((3, 3), np.uint8)
    ea = a & ~(cv2.erode(a.astype(np.uint8), k) > 0)
    eb = b & ~(cv2.erode(b.astype(np.uint8), k) > 0)
    if not ea.any() and not eb.any():
        return 1.0
    if not ea.any() or not eb.any():
        return 0.0
    da = cv2.distanceTransform((~ea).astype(np.uint8), cv2.DIST_L2, 3)
    db = cv2.distanceTransform((~eb).astype(np.uint8), cv2.DIST_L2, 3)
    p = (db[ea] <= tol).mean()
    r = (da[eb] <= tol).mean()
    return 2 * p * r / max(p + r, 1e-9)


def main(a):
    from check_track_fast import track
    from omegaconf import OmegaConf
    from utils import sam3_track_fast
    torch.set_autocast_cache_enabled(False)
    cfg = OmegaConf.load(os.path.join(REPO, "configs", "body4d.yaml"))
    imgs = sorted(glob.glob(os.path.join(a.frames, "*.jpg")))
    m0 = np.array(Image.open(sorted(glob.glob(os.path.join(a.pipeline_out, "masks", "*.png")))[0]))
    H, W = m0.shape
    boxes = []
    for k in sorted(set(np.unique(m0).tolist()) - {0}):
        ys, xs = np.nonzero(m0 == k)
        boxes.append((int(k), np.array([[xs.min() / W, ys.min() / H, xs.max() / W, ys.max() / H]], np.float32)))
    res = {}
    for size in a.sizes:
        tr = sam3_track_fast.build_tracker_at(cfg.sam3["ckpt_path"], size)
        sam3_track_fast.install(tr)
        track(tr, imgs, boxes, 8, True)   # warm-up
        m, dt = track(tr, imgs, boxes, len(imgs), True)
        res[size] = m
        print(f"{size}: {dt:.1f} s ({1000 * dt / len(m):.0f} ms/frame)", flush=True)
        if a.save:
            os.makedirs(f"{a.save}/{size}", exist_ok=True)
            np.save(f"{a.save}/{size}/masks.npy", m)
        del tr
        torch.cuda.empty_cache()
    ref = res[a.sizes[0]]
    for size in a.sizes[1:]:
        m = res[size]
        for o in range(ref.shape[1]):
            iou, bf = [], []
            for f in range(len(ref)):
                r, x = ref[f, o], m[f, o]
                un = (r | x).sum()
                iou.append((r & x).sum() / un if un else 1.0)
                bf.append(boundary_f(r, x))
            iou, bf = np.array(iou), np.array(bf)
            print(f"{size} vs {a.sizes[0]}, object {boxes[o][0]}: IoU mean {iou.mean():.4f} min {iou.min():.4f} "
                  f"(frame {iou.argmin()}), boundary F@2px mean {bf.mean():.4f} min {bf.min():.4f}; "
                  f"area ratio {m[:, o].sum() / max(ref[:, o].sum(), 1):.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True)
    ap.add_argument("--pipeline_out", required=True)
    ap.add_argument("--sizes", type=int, nargs="+", default=[1008, 672])
    ap.add_argument("--save", default=None)
    main(ap.parse_args())
