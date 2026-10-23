"""How much is lost if the per-frame models ran on every k-th frame only and the frames in between were interpolated.

From one full run (e2e.py output), for strides k = 2, 3, 4:
  skeleton  joints (joint_coords + cam_t) of the in-between frames linearly interpolated from the neighbouring
            keyframes, against the frame's own HMR result: mean / p95 error in mm, camera space and root-relative.
            Also the same error after avatar_app's temporal smoothing is applied to both (what the avatar shows).
  masks     each person's tracking mask of an in-between frame warped from the previous keyframe by DIS optical flow
            (as fusion's kf_lite carries masks), against the frame's own mask: IoU.

usage: python scripts/eval/keyframe_interp_study.py --pipeline_out <dir> [--frames <dir of the input frames>]
"""
import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(REPO, "scripts"), REPO]

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402


def gauss_smooth(x, sigma_frames):
    """Gaussian smoothing along axis 0 (edge-normalized), like a temporal skeleton filter."""
    if sigma_frames <= 0:
        return x
    r = int(np.ceil(3 * sigma_frames))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma_frames) ** 2)
    out = np.zeros_like(x)
    n = len(x)
    for t in range(n):
        lo, hi = max(0, t - r), min(n, t + r + 1)
        w = k[lo - t + r:hi - t + r]
        out[t] = np.tensordot(w / w.sum(), x[lo:hi], axes=1)
    return out


def skeleton(pout, pid):
    files = sorted(glob.glob(f"{pout}/skeleton_4d_individual/{pid}/*.npz"))
    stems = [int(os.path.basename(f)[:-4]) for f in files]
    J = np.stack([(lambda z: z["joint_coords"] + z["cam_t"])(np.load(f)) for f in files])
    return np.array(stems), J


def interp(J, k):
    """Joints of every frame from the keyframes 0, k, 2k, ... (the last frame is a keyframe too)."""
    n = len(J)
    keys = sorted(set(range(0, n, k)) | {n - 1})
    out = J.copy()
    for a, b in zip(keys[:-1], keys[1:]):
        for t in range(a + 1, b):
            w = (t - a) / (b - a)
            out[t] = (1 - w) * J[a] + w * J[b]
    mid = np.array([t not in keys for t in range(n)])
    return out, mid


def main(a):
    pout = a.pipeline_out
    pids = sorted(int(os.path.basename(d)) for d in glob.glob(f"{pout}/skeleton_4d_individual/*"))
    sig = a.smooth * a.fps
    for k in (2, 3, 4):
        for p in pids:
            stems, J = skeleton(pout, p)
            if len(stems) < 8 or np.any(np.diff(stems) != 1):
                # gaps (the person left the frame): use the longest contiguous run
                br = np.nonzero(np.diff(stems) != 1)[0]
                cuts = np.split(np.arange(len(stems)), br + 1)
                run = max(cuts, key=len)
                J = J[run]
            I, mid = interp(J, k)
            e = np.linalg.norm(I - J, axis=-1)[mid] * 1000
            rr = np.linalg.norm((I - I[:, :1]) - (J - J[:, :1]), axis=-1)[mid] * 1000
            Is, Js = gauss_smooth(I, sig), gauss_smooth(J, sig)
            es = np.linalg.norm(Is - Js, axis=-1) * 1000
            print(f"k={k} person {p}: in-between joints error mean {e.mean():.1f} mm p95 {np.percentile(e, 95):.1f} mm "
                  f"(root-relative mean {rr.mean():.1f} mm); after smoothing sigma {a.smooth} s, all frames: mean "
                  f"{es.mean():.1f} mm p95 {np.percentile(es, 95):.1f} mm", flush=True)
    if a.frames:
        imgs = sorted(glob.glob(f"{pout}/images/*.jpg"))
        masks = sorted(glob.glob(f"{pout}/masks/*.png"))
        gray = [cv2.cvtColor(cv2.imread(f), cv2.COLOR_BGR2GRAY) for f in imgs]
        lab = [np.array(Image.open(f)) for f in masks]
        dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
        H, W = gray[0].shape
        gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
        for k in (2, 3, 4):
            ious = {p: [] for p in pids}
            for kf in range(0, len(gray), k):
                cur = {p: (lab[kf] == p).astype(np.float32) for p in pids}
                for t in range(kf + 1, min(kf + k, len(gray))):
                    f = dis.calc(gray[t], gray[t - 1], None)
                    cur = {p: cv2.remap(m, gx + f[..., 0], gy + f[..., 1], cv2.INTER_LINEAR, borderValue=0)
                           for p, m in cur.items()}
                    for p in pids:
                        x, r = cur[p] > 0.5, lab[t] == p
                        un = (x | r).sum()
                        if un:
                            ious[p].append((x & r).sum() / un)
            print(f"k={k}: in-between tracking masks carried by flow, IoU vs own: "
                  + ", ".join(f"person {p} mean {np.mean(v):.3f} p5 {np.percentile(v, 5):.3f}" for p, v in ious.items()),
                  flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline_out", required=True)
    ap.add_argument("--frames", default=None, help="any value: also study the masks")
    ap.add_argument("--smooth", type=float, default=0.05)
    ap.add_argument("--fps", type=float, default=30)
    main(ap.parse_args())
