"""avatar_app's person removal, the current fill against the reworked one (removal_gpu work_short / static_bg / LaMa),
standalone on one e2e run's frames and masks: wall and CPU time (the removal runs beside fusion v2, so its CPU use
slows fusion down), fill statistics and side-by-side images of the plates.

usage: python scripts/eval/compare_removal.py --run <e2e.py run dir> --hq <full-resolution frames dir>
       --out <image dir> [--frames 20 60 100] [--height 480]
"""
import argparse
import glob
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(REPO, "scripts"), REPO]

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402


def main(a):
    import avatar.removal_gpu as rg
    from avatar.inpaint_lama import inpaint as lama
    pout = f"{a.run}/pipeline_out"
    stems = sorted(os.path.basename(p)[:-4] for p in glob.glob(f"{pout}/images/*.jpg"))
    files = sorted(glob.glob(f"{a.hq}/*.jpg") + glob.glob(f"{a.hq}/*.png"))
    frames = [cv2.imread(f) for f in files[:len(stems)]]
    OH, OW = frames[0].shape[:2]
    pids = sorted(int(os.path.basename(d)) for d in glob.glob(f"{pout}/skeleton_4d_individual/*"))
    H0 = np.array(Image.open(f"{pout}/masks/{stems[0]}.png")).shape[0]
    masks = []
    for s in stems:
        m = np.isin(np.array(Image.open(f"{pout}/masks/{s}.png")), pids).astype(np.uint8)
        masks.append(cv2.resize(m, (OW, OH), interpolation=cv2.INTER_NEAREST) > 0)
    sc = OH / H0
    kw = dict(fps=30, workers=os.cpu_count(), dilate=max(1, round(9 * sc)), exclude_dilate=max(1, round(15 * sc)))
    res = {}
    for name, extra in [("current", {}), ("reworked", dict(work_short=480, static_bg=True, inpaint=lama))]:
        import psutil
        proc = psutil.Process()
        c0 = sum(proc.cpu_times()[:2])
        t0 = time.perf_counter()
        plates, holes, stats = rg.clean_plates_gpu(frames, masks, **kw, **extra)
        res[name] = plates
        print(f"{name}: {time.perf_counter() - t0:.1f} s wall, {sum(proc.cpu_times()[:2]) - c0:.1f} s CPU (all threads), "
              f"{stats}", flush=True)
    os.makedirs(a.out, exist_ok=True)
    clip = os.path.basename(os.path.normpath(a.run))
    for f in a.frames:
        tiles = []
        for lb, im in (("input", frames[f]), ("current removal", res["current"][f]), ("reworked removal", res["reworked"][f])):
            s = a.height / im.shape[0]
            t = cv2.resize(np.asarray(im), (int(round(im.shape[1] * s)) // 2 * 2, a.height), interpolation=cv2.INTER_AREA)
            cv2.putText(t, f"{lb} #{f}", (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(t, f"{lb} #{f}", (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
            tiles.append(t)
        cv2.imwrite(f"{a.out}/{clip}_{f:03d}.jpg", np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 85])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--hq", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, nargs="+", default=[20, 60, 100])
    ap.add_argument("--height", type=int, default=480)
    main(ap.parse_args())
