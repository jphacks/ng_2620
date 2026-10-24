"""Side-by-side frames: base hide (red) | kf hide (blue) | kf tracked objects (colored outlines); mesh outline green.

usage: python kf_vis.py <out_root> <tag> <clip> <frame> [<frame> ...]   -> <out_root>/vis/<tag>_<clip>_<frame>.jpg
"""
import glob
import os
import sys

import cv2
import numpy as np


def hide_union(root, clip, stem):
    h = None
    for d in glob.glob(f"{root}/hide/{clip}/*"):
        f = f"{d}/{stem}.png"
        if os.path.exists(f):
            m = cv2.imread(f, cv2.IMREAD_GRAYSCALE) >= 128
            h = m if h is None else h | m
    return h


def main():
    root, tag, clip = sys.argv[1], sys.argv[2], sys.argv[3]
    frames = [int(x) for x in sys.argv[4:]]
    cache = f"{root}/{tag}/_r3cache/{clip}"
    z = np.load(f"{cache}/mesh.npz")
    I, D = z["I"], z["D"]
    c = np.load(f"{cache}/cands.npz")
    H, W = c["shape"]
    stems = sorted(os.path.basename(f)[:-4] for f in glob.glob(f"{root}/base/hide/{clip}/*/*.png"))
    stems = sorted(set(stems))
    os.makedirs(f"{root}/vis", exist_ok=True)
    rng = np.random.default_rng(0)
    colors = {int(t): tuple(int(v) for v in rng.integers(64, 256, 3)) for t in np.unique(c["track"])}
    for i in frames:
        img = I[i].copy()
        sil = (D[i] > 0).any(0).astype(np.uint8)
        cs, _ = cv2.findContours(sil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        panels = []
        for variant, col in (("base", (0, 0, 255)), (tag, (255, 0, 0))):
            p = img.copy()
            h = hide_union(f"{root}/{variant}", clip, stems[i])
            if h is not None:
                p[h] = (0.45 * p[h] + 0.55 * np.array(col)).astype(np.uint8)
            cv2.drawContours(p, cs, -1, (0, 255, 0), 1)
            cv2.putText(p, f"{variant} {i}", (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
            panels.append(p)
        p = img.copy()
        for r in np.nonzero(c["frame"] == i)[0]:
            m = np.unpackbits(c["masks"][r])[:H * W].reshape(H, W).astype(np.uint8)
            cc, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(p, cc, -1, colors[int(c["track"][r])], 2)
        cv2.drawContours(p, cs, -1, (0, 255, 0), 1)
        cv2.putText(p, "tracked objects", (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        panels.append(p)
        out = np.concatenate(panels, 1)
        cv2.imwrite(f"{root}/vis/{tag}_{clip}_{i:03d}.jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"{root}/vis/{tag}_{clip}_{i:03d}.jpg")


if __name__ == "__main__":
    main()
