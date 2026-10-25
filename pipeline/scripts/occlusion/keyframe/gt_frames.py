"""Per GT key frame: tp / fp / fn of several variants on one clip.
usage: python gt_frames.py <clip> <variant root> [<variant root> ...]"""
import glob
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))).replace("\\", "/")
GT = f"{REPO}/testdata/2026-10-11/occ_research/_gt/gt_audit"
clip, roots = sys.argv[1], sys.argv[2:]
for f in sorted(glob.glob(f"{GT}/{clip}/*/*.png")):
    pid = int(os.path.basename(os.path.dirname(f)))
    stem = os.path.basename(f)[:-4]
    G = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
    line = [f"pid {pid} f{int(stem):03d} pos {(G == 1).sum():4d}"]
    for r in roots:
        z = np.load(f"{r}/_r3cache/{clip}/mesh.npz")
        k = list(z["pids"]).index(pid)
        dom = (z["D"][int(stem), k] > 0) & (G != 255)
        hp = f"{r}/hide/{clip}/{pid}/{stem}.png"
        h = cv2.imread(hp, cv2.IMREAD_GRAYSCALE) >= 128 if os.path.exists(hp) else np.zeros(G.shape, bool)
        g1 = G == 1
        line.append(f"{os.path.basename(r)}: tp {int((dom & g1 & h).sum())} fp {int((dom & ~g1 & h).sum())} "
                    f"fn {int((dom & g1 & ~h).sum())}")
    print(" | ".join(line))
