"""Compare hide masks of two fusion variants (no GT): agreement with the reference variant, flicker, tracks.

usage: python kf_compare.py <out_root> <ref tag> <tag> [clip ...]   -> <out_root>/compare_<tag>.json
Per clip (pixels of all persons and frames pooled):
  hidden_ref / hidden: hidden pixel counts; agree_iou = |A & B| / |A | B|; recall = |A & B| / |A| (how much of the
  reference's hiding the variant keeps), precision = |A & B| / |B|
  flicker: pixels with h[t-1] == h[t+1] != h[t], over pixels hidden in any of t-1, t, t+1
  frames_diff: person-frames whose hide masks differ by more than 1% of the mesh area
"""
import glob
import json
import os
import sys

import cv2
import numpy as np


def load_hides(root, clip):
    out = {}
    for d in glob.glob(f"{root}/hide/{clip}/*"):
        pid = int(os.path.basename(d))
        for f in sorted(glob.glob(f"{d}/*.png")):
            out[(pid, os.path.basename(f)[:-4])] = cv2.imread(f, cv2.IMREAD_GRAYSCALE) >= 128
    return out


def flicker(h):
    keys = sorted(h)
    by = {}
    for pid, stem in keys:
        by.setdefault(pid, []).append(stem)
    flips = tot = 0
    for pid, stems in by.items():
        for a, b, c in zip(stems, stems[1:], stems[2:]):
            A, B, C = h[(pid, a)], h[(pid, b)], h[(pid, c)]
            flips += int(((A == C) & (B != A)).sum())
            tot += int((A | B | C).sum())
    return flips / max(tot, 1), flips, tot


def main():
    root, ref, tag = sys.argv[1], sys.argv[2], sys.argv[3]
    clips = sys.argv[4:] or sorted(os.path.basename(d) for d in glob.glob(f"{root}/{tag}/hide/*"))
    res = {}
    for clip in clips:
        A, B = load_hides(f"{root}/{ref}", clip), load_hides(f"{root}/{tag}", clip)
        keys = sorted(set(A) | set(B))
        z = None
        inter = ua = ub = un = 0
        nd = 0
        mesh = None
        mp = f"{root}/{ref}/_r3cache/{clip}/mesh.npz"
        for k in keys:
            a = A.get(k)
            b = B.get(k)
            if a is None:
                a = np.zeros_like(b)
            if b is None:
                b = np.zeros_like(a)
            inter += int((a & b).sum())
            ua += int(a.sum())
            ub += int(b.sum())
            un += int((a | b).sum())
            if int((a ^ b).sum()) > 0.01 * max(int(a.sum() + b.sum()), 1) and int((a ^ b).sum()) >= 30:
                nd += 1
        fa, fb = flicker(A), flicker(B)
        res[clip] = dict(person_frames=len(keys), hidden_ref=ua, hidden=ub, agree_iou=inter / max(un, 1),
                         recall=inter / max(ua, 1) if ua else None, precision=inter / max(ub, 1) if ub else None,
                         flicker_ref=fa[0], flicker=fb[0], frames_diff=nd)
        print(clip, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in res[clip].items()}, flush=True)
    json.dump(res, open(f"{root}/compare_{tag}.json", "w"), indent=1)


if __name__ == "__main__":
    main()
