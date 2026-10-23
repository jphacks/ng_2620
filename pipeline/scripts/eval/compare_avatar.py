"""Side-by-side video of avatar_app.py outputs of the same clip, for checking quality by eye.

Tiles: the input frame, each run's avatar frame (labelled), and a difference tile (|run2 - run1| amplified, over a
dimmed copy of run1) that shows where the last two runs differ. Frames are read from <run>/frames/.

usage: python scripts/eval/compare_avatar.py --input <frames dir> --out cmp.mp4 <run dir> <run dir> [--labels a b]
       [--height 960] [--fps 30]
"""
import argparse
import glob
import os

import cv2
import numpy as np


def frames(d):
    return sorted(glob.glob(os.path.join(d, "frames", "*.png")) + glob.glob(os.path.join(d, "frames", "*.jpg")))


def label(im, text):
    cv2.putText(im, text, (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(im, text, (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    return im


def main(a):
    runs = [frames(r) for r in a.runs]
    labels = a.labels or [os.path.basename(os.path.dirname(r.rstrip("/\\"))) for r in a.runs]
    inp = sorted(glob.glob(os.path.join(a.input, "*.jpg")) + glob.glob(os.path.join(a.input, "*.png")))
    n = min(len(f) for f in runs)
    writer = None
    diff_px = []
    for i in range(n):
        ims = [cv2.imread(f[i]) for f in runs]
        H, W = ims[0].shape[:2]
        s = a.height / H
        size = (int(round(W * s)) // 2 * 2, a.height)
        tiles = []
        if i < len(inp):
            src = cv2.imread(inp[i])
            tiles.append(label(cv2.resize(src, size, interpolation=cv2.INTER_AREA), f"input #{i}"))
        for im, lb in zip(ims, labels):
            tiles.append(label(cv2.resize(im, size, interpolation=cv2.INTER_AREA), lb))
        if len(ims) >= 2:
            d = cv2.absdiff(ims[-1], ims[-2]).max(2)
            diff_px.append(int((d > 24).sum()))
            t = (ims[-2] * 0.35).astype(np.uint8)
            t[d > 24] = (0, 0, 255)
            tiles.append(label(cv2.resize(t, size, interpolation=cv2.INTER_AREA),
                               f"diff {100 * diff_px[-1] / d.size:.2f}%"))
        fr = np.hstack(tiles)
        if writer is None:   # H.264 (plays in browsers and the Windows player; OpenCV's mp4v does not everywhere)
            import imageio
            writer = imageio.get_writer(a.out, fps=a.fps, codec="libx264", quality=8, macro_block_size=2)
        writer.append_data(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
    writer.close()
    if diff_px:
        dp = np.array(diff_px) / (H * W) * 100
        print(f"{a.out}: {n} frames; pixels that differ (>24/255) between the last two runs: mean {dp.mean():.3f}%, "
              f"max {dp.max():.3f}% (frame {int(dp.argmax())})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--labels", nargs="*")
    ap.add_argument("--height", type=int, default=960)
    ap.add_argument("--fps", type=float, default=30)
    main(ap.parse_args())
