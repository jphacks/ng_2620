"""Two e2e.py runs of the same clip: how far apart their meshes / skeletons, masks, hide masks and avatar frames are.

  masks      per person: IoU of the tracking masks (pipeline_out/masks)
  skeleton   per person: joint distance in camera space (joint_coords + cam_t, metres; includes depth / scale drift)
             and root-relative (pelvis subtracted: the pose itself), mean over joints and frames, and the p95
  hide       clip IoU of fusion's hide decisions re-rendered on the avatar (union over persons)
  avatar     share of output pixels that differ by > 24/255

usage: python scripts/eval/compare_runs_e2e.py <run A> <run B>
"""
import glob
import os
import sys

import cv2
import numpy as np


def skel(run, pid):
    out = {}
    for f in sorted(glob.glob(f"{run}/pipeline_out/skeleton_4d_individual/{pid}/*.npz")):
        z = np.load(f)
        out[os.path.basename(f)] = z["joint_coords"] + z["cam_t"]
    return out


def main(a, b):
    pa = f"{a}/pipeline_out"
    stems = sorted(os.path.basename(p)[:-4] for p in glob.glob(f"{pa}/masks/*.png"))
    pids = sorted(int(os.path.basename(d)) for d in glob.glob(f"{pa}/skeleton_4d_individual/*"))
    from PIL import Image
    for p in pids:
        iou = []
        for s in stems:
            ma = np.array(Image.open(f"{a}/pipeline_out/masks/{s}.png")) == p
            mb = np.array(Image.open(f"{b}/pipeline_out/masks/{s}.png")) == p
            un = (ma | mb).sum()
            if un:
                iou.append((ma & mb).sum() / un)
        sa, sb = skel(a, p), skel(b, p)
        common = sorted(set(sa) & set(sb))
        d = np.stack([np.linalg.norm(sa[k] - sb[k], axis=-1) for k in common])
        rr = np.stack([np.linalg.norm((sa[k] - sa[k][:1]) - (sb[k] - sb[k][:1]), axis=-1) for k in common])
        print(f"person {p}: mask IoU mean {np.mean(iou):.4f} min {np.min(iou):.4f}; skeleton frames {len(common)} "
              f"(A {len(sa)}, B {len(sb)}): camera-space joint error mean {1000 * d.mean():.1f} mm p95 "
              f"{1000 * np.percentile(d, 95):.1f} mm; root-relative mean {1000 * rr.mean():.1f} mm p95 "
              f"{1000 * np.percentile(rr, 95):.1f} mm")
    fa = sorted(glob.glob(f"{a}/avatar/frames/*"))
    fb = sorted(glob.glob(f"{b}/avatar/frames/*"))
    diff = []
    for x, y in zip(fa, fb):
        ia, ib = cv2.imread(x), cv2.imread(y)
        diff.append((cv2.absdiff(ia, ib).max(2) > 24).mean() * 100)
    diff = np.array(diff)
    print(f"avatar frames: differing pixels mean {diff.mean():.2f}% max {diff.max():.2f}% (frame {diff.argmax()})")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
