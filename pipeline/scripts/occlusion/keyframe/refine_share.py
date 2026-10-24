"""How much of the base hiding depends on the per-frame 'person' grounding (refine_visible)?

Object hiding is drawn only outside the (refined) visible mask, so hidden pixels that lie inside the tracked visible
mask V0 = (L == pid) exist only because refine_visible removed them from V. Per clip:
  hide_px, hide_in_V0 (and its share), refine_removed_px (V0 & ~Vr), person-frames where the share > 10%.
usage: python refine_share.py <variant root> [clip ...]
"""
import glob
import os
import pickle
import sys

import cv2
import numpy as np


def main():
    root = sys.argv[1]
    clips = sys.argv[2:] or sorted(os.path.basename(d) for d in glob.glob(f"{root}/hide/*"))
    tot_h = tot_in = 0
    for clip in clips:
        cache = f"{root}/_r3cache/{clip}"
        if not os.path.exists(f"{cache}/evidence.pkl"):
            continue
        z = np.load(f"{cache}/mesh.npz")
        L, pids = z["L"], list(z["pids"])
        T, H, W = L.shape
        stems = sorted(os.path.basename(f)[:-4] for f in glob.glob(f"{root}/hide/{clip}/{pids[0]}/*.png"))
        ev = pickle.load(open(f"{cache}/evidence.pkl", "rb"))
        h_px = in_px = rem = add = vtot = 0
        frames_hi = 0
        for i in range(T):
            for p in pids:
                f = f"{root}/hide/{clip}/{p}/{stems[i]}.png" if i < len(stems) else None
                if f is None or not os.path.exists(f):
                    continue
                h = cv2.imread(f, cv2.IMREAD_GRAYSCALE) >= 128
                V0 = L[i] == p
                Vr = np.unpackbits(ev["Vr"][(i, p)])[:H * W].reshape(H, W).astype(bool)
                # render dilates the hide mask by 1 px: count only pixels 2 px inside the tracked mask
                hin = int((h & (cv2.erode(V0.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)).sum())
                h_px += int(h.sum())
                in_px += hin
                rem += int((V0 & ~Vr).sum())
                add += int((Vr & ~V0).sum())
                vtot += int(V0.sum())
                if h.sum() >= 30 and hin > 0.1 * h.sum():
                    frames_hi += 1
        tot_h += h_px
        tot_in += in_px
        print(f"{clip}: hide {h_px} px, inside tracked mask {in_px} ({in_px / max(h_px, 1):.1%}), "
              f"refine removed {rem} px / added {add} px of {vtot} visible px, "
              f"person-frames with >10% of hide inside: {frames_hi}", flush=True)
    print(f"ALL: hide {tot_h}, inside tracked mask {tot_in} ({tot_in / max(tot_h, 1):.1%})")


if __name__ == "__main__":
    main()
