"""Hide masks of fusion v2 runs against a reference run (run_fusion_v2.py --out dirs of the same clip).

Clip IoU = sum of intersections / sum of unions over all frames (pixel-weighted). Per frame: IoU of the union of all persons' hidden pixels with the reference's (frames where neither hides anything
count as 1), and the hidden pixel totals. Optionally a side-by-side video: red = hidden, blue = hidden in the
reference only.

usage: python scripts/eval/compare_hide_dirs.py --pipeline_out <dir> <reference out> <out> ... [--video v.mp4]
"""
import argparse
import glob
import os

import cv2
import numpy as np


def load(out, stem):
    u = None
    for f in glob.glob(f"{out}/hide/*/*/{stem}.png"):
        h = cv2.imread(f, 0) > 127
        u = h if u is None else u | h
    return u


def main(a):
    stems = sorted(os.path.basename(p)[:-4] for p in glob.glob(f"{a.pipeline_out}/images/*.jpg"))
    ref, runs = a.runs[0], a.runs[1:]
    iou = {r: [] for r in runs}
    big = {r: [] for r in runs}    # IoU on frames where the union hides >= MIN_UNION px
    acc = {r: [0, 0] for r in runs}   # sum of intersections, sum of unions over the clip
    px = {r: 0 for r in a.runs}
    writer = None
    for s in stems:
        img = cv2.imread(f"{a.pipeline_out}/images/{s}.jpg")
        z = np.zeros(img.shape[:2], bool)
        u0 = load(ref, s)
        u0 = z if u0 is None else u0
        px[ref] += int(u0.sum())
        tiles = [img.copy()]
        tiles[0][u0] = (0.4 * img[u0] + [0, 0, 150]).astype(np.uint8)
        for r in runs:
            u = load(r, s)
            u = z if u is None else u
            px[r] += int(u.sum())
            un = (u | u0).sum()
            it = int((u & u0).sum())
            iou[r].append(float(it / un) if un else 1.0)
            acc[r][0] += it
            acc[r][1] += int(un)
            if un >= a.min_union:
                big[r].append(it / un)
            t = img.copy()
            t[u] = (0.4 * img[u] + [0, 0, 150]).astype(np.uint8)
            m = u0 & ~u
            t[m] = (0.4 * img[m] + [150, 0, 0]).astype(np.uint8)
            tiles.append(t)
        if a.video:
            fr = np.hstack(tiles)
            if writer is None:
                writer = cv2.VideoWriter(a.video, cv2.VideoWriter_fourcc(*"mp4v"), 15, (fr.shape[1], fr.shape[0]))
            writer.write(fr)
    if writer is not None:
        writer.release()
    for r in runs:
        v = np.array(iou[r])
        b = np.array(big[r]) if big[r] else np.ones(1)
        print(f"{r}: clip IoU {acc[r][0] / max(acc[r][1], 1):.3f}; per-frame IoU mean {v.mean():.3f}; "
              f"frames with >= {a.min_union} px: {len(big[r])}, IoU mean {b.mean():.3f} min {b.min():.3f}; "
              f"hidden px {px[r]} vs ref {px[ref]} ({px[r] / max(px[ref], 1):.3f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline_out", required=True)
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--video", default=None)
    ap.add_argument("--min_union", type=int, default=200)
    main(ap.parse_args())
