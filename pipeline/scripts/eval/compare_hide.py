"""Fusion v2's hide masks of one clip under several point modes, against a reference run of the same clip.

Each run dir is a profile_stages.py --out (same pipeline_out, different --points). Per frame and person: IoU of the
hidden pixels with the reference's, and the ratio of hidden pixel counts; plus a side-by-side video of the hide
overlays (reference first), for looking at the frames where they differ.

usage: python scripts/eval/compare_hide.py <reference run dir> <run dir> ... [--video out.mp4]
"""
import argparse
import json
import os

import cv2
import numpy as np


def hide(run_dir, pid, stem):
    """the run's hide mask (occlusion/hide/<run name>/<pid>/<stem>.png; the run name is the one subfolder)."""
    (name,) = os.listdir(f"{run_dir}/occlusion/hide")
    f = f"{run_dir}/occlusion/hide/{name}/{pid}/{stem}.png"
    return cv2.imread(f, 0) > 127 if os.path.exists(f) else None


def main(a):
    ref = a.runs[0]
    pout = f"{ref}/pipeline_out"
    stems = sorted(os.path.basename(p)[:-4] for p in os.listdir(f"{pout}/images"))
    pids = sorted(os.listdir(f"{pout}/mesh_4d_individual"))
    res = {}
    writer = None
    for r in a.runs[1:]:
        res[os.path.basename(r.rstrip("/"))] = dict(iou=[], ratio=[])
    for k, s in enumerate(stems):
        img = cv2.imread(f"{pout}/images/{s}.jpg")
        tiles = []
        for j, r in enumerate(a.runs):
            u = np.zeros(img.shape[:2], bool)
            for p in pids:
                h = hide(r, p, s)
                if h is not None:
                    u |= h
            if j == 0:
                u0 = u
            else:
                d = res[os.path.basename(r.rstrip("/"))]
                un = (u | u0).sum()
                d["iou"].append(float((u & u0).sum() / un) if un else 1.0)
                d["ratio"].append(float(u.sum() / max(u0.sum(), 1)))
            t = img.copy()
            t[u] = (0.4 * t[u] + np.array([0, 0, 150])).astype(np.uint8)
            if j:
                t[u0 & ~u] = (0.4 * img[u0 & ~u] + np.array([150, 0, 0])).astype(np.uint8)   # blue: missed vs ref
            cv2.putText(t, f"{os.path.basename(r.rstrip('/'))} #{k}", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (255, 255, 255), 1, cv2.LINE_AA)
            tiles.append(t)
        if a.video:
            fr = np.hstack(tiles)
            if writer is None:
                writer = cv2.VideoWriter(a.video, cv2.VideoWriter_fourcc(*"mp4v"), 15, (fr.shape[1], fr.shape[0]))
            writer.write(fr)
    if writer is not None:
        writer.release()
    out = {k: dict(iou_mean=float(np.mean(v["iou"])), iou_p10=float(np.percentile(v["iou"], 10)),
                   hidden_ratio_median=float(np.median(v["ratio"])), iou=v["iou"]) for k, v in res.items()}
    for k, v in out.items():
        print(f"{k}: hide IoU vs {os.path.basename(ref.rstrip('/'))} mean {v['iou_mean']:.3f} (p10 {v['iou_p10']:.3f}), "
              f"hidden px ratio median {v['hidden_ratio_median']:.2f}")
    if a.json:
        json.dump(out, open(a.json, "w"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--video", default=None)
    ap.add_argument("--json", default=None)
    main(ap.parse_args())
