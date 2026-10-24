"""Side-by-side comparison images of avatar outputs (or any frame folders), small enough to commit for review on GitHub.

For each requested frame index: one row of tiles (input, then every run), each labelled, scaled to --height, plus an
optional difference tile between the last two runs (red: |diff| > 24/255). Writes <out>/<name>_<frame>.jpg.

usage: python scripts/eval/make_compare_sheet.py --out reports/compare/<date>/<topic> --name <clip>
       --frames 20 60 100 --input <frames dir> --run <label>=<frames dir> [--run ...] [--height 480] [--diff]
"""
import argparse
import glob
import os

import cv2
import numpy as np


def frames_of(d):
    return sorted(glob.glob(os.path.join(d, "*.png")) + glob.glob(os.path.join(d, "*.jpg")))


def label(im, text):
    s = max(0.5, im.shape[0] / 640)
    cv2.putText(im, text, (8, int(28 * s)), cv2.FONT_HERSHEY_SIMPLEX, 0.8 * s, (0, 0, 0), int(5 * s), cv2.LINE_AA)
    cv2.putText(im, text, (8, int(28 * s)), cv2.FONT_HERSHEY_SIMPLEX, 0.8 * s, (255, 255, 255), max(1, int(2 * s)),
                cv2.LINE_AA)
    return im


def main(a):
    os.makedirs(a.out, exist_ok=True)
    runs = [r.split("=", 1) for r in a.run]
    lists = [(lb, frames_of(d)) for lb, d in runs]
    inp = frames_of(a.input) if a.input else []
    for f in a.frames:
        tiles, ims = [], []
        for lb, fl in [("input", inp)] + lists if inp else lists:
            if f >= len(fl):
                continue
            im = cv2.imread(fl[f])
            if lb != "input":
                ims.append(im)
            s = a.height / im.shape[0]
            t = cv2.resize(im, (int(round(im.shape[1] * s)) // 2 * 2, a.height), interpolation=cv2.INTER_AREA)
            tiles.append(label(t, f"{lb} #{f}"))
        if a.diff and len(ims) >= 2 and ims[-1].shape == ims[-2].shape:
            d = cv2.absdiff(ims[-1], ims[-2]).max(2) > 24
            t = (ims[-2] * 0.35).astype(np.uint8)
            t[d] = (0, 0, 255)
            s = a.height / t.shape[0]
            t = cv2.resize(t, (int(round(t.shape[1] * s)) // 2 * 2, a.height), interpolation=cv2.INTER_AREA)
            tiles.append(label(t, f"diff {100 * d.mean():.2f}%"))
        path = os.path.join(a.out, f"{a.name}_{f:03d}.jpg")
        cv2.imwrite(path, np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, a.quality])
        print(path, os.path.getsize(path) // 1024, "KB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--frames", type=int, nargs="+", required=True)
    ap.add_argument("--input", default=None)
    ap.add_argument("--run", action="append", required=True, help="label=frames dir (repeatable)")
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("--quality", type=int, default=85)
    main(ap.parse_args())
