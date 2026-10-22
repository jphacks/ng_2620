"""HMR accuracy of SAM-Body4D runs on 3DPW clips against the 3DPW ground truth.

For each run (offline_app_lite.py --capture), the 12 limb joints shared by MHR-70 and SMPL-24 (shoulders, elbows,
wrists, hips, knees, ankles) are compared per frame and actor:
  PA-MPJPE  mm, after a similarity Procrustes fit (pose only)
  MPJPE     mm, after aligning the hip midpoints (pose + global orientation, no translation / scale)
  2D        px on the run's input frames, against the GT joints projected with the GT camera
Predicted people are matched to GT actors once per clip (mean 2D distance, Hungarian). Frames whose GT camera pose
is marked invalid for that actor are skipped.

usage (repo root):
  .venv/Scripts/python.exe scripts/eval/gt_3dpw_hmr.py <manifest.csv> <run_dir>[=label] ...
  run_dir holds captures/hmr_outputs.pkl; its basename (or a parent) must be the manifest's run name, or pass
  run_dir=<run name>.
"""
import csv
import glob
import os
import pickle
import sys

import numpy as np
from scipy.optimize import linear_sum_assignment

DATA = r"C:\VScodeFiles\datasets\3DPW"
# MHR-70 index -> SMPL-24 index (L/R shoulder, elbow, wrist, hip, knee, ankle)
MHR = [5, 6, 7, 8, 62, 41, 9, 10, 11, 12, 13, 14]
SMPL = [16, 17, 18, 19, 20, 21, 1, 2, 4, 5, 7, 8]
HIPS = [6, 7]  # positions within the 12


def procrustes(p, g):
    """p aligned to g with rotation, scale and translation (Umeyama)."""
    mp, mg = p.mean(0), g.mean(0)
    p0, g0 = p - mp, g - mg
    u, s, vt = np.linalg.svd(p0.T @ g0)
    d = np.sign(np.linalg.det(u @ vt))
    D = np.diag([1, 1, d])
    R = u @ D @ vt
    scale = (s * np.diag(D)).sum() / (p0 ** 2).sum()
    return scale * p0 @ R + mg


def load_gt(seq):
    path = glob.glob(os.path.join(DATA, "sequenceFiles", "*", seq + ".pkl"))[0]
    d = pickle.load(open(path, "rb"), encoding="latin1")
    return d


def evaluate(row, run_dir):
    cap = pickle.load(open(os.path.join(run_dir, "captures", "hmr_outputs.pkl"), "rb"))
    d = load_gt(row["seq"])
    start = int(row["start"])
    sw, sh = map(int, row["src_size"].split("x"))
    w, h = map(int, row["size"].split("x"))
    k_scale = np.array([w / sw, h / sh])
    n_img = len(d["cam_poses"])  # annotation i <-> image_{i:05d}.jpg (img_frame_ids counts 60 Hz IMU samples)
    K = np.asarray(d["cam_intrinsics"])
    n_act = len(d["jointPositions"])

    preds = {}  # pid -> {frame: output}
    for b in cap["hmr_batches"]:
        for ip, outs, ids in zip(b["image_path"], b["outputs"], b["id_batch"]):
            f = start + int(os.path.splitext(os.path.basename(ip))[0])
            for o, pid in zip(outs, ids):
                preds.setdefault(pid, {})[f] = o

    def gt_at(a, f):
        if not 0 <= f < n_img:
            return None
        i = f
        if not d["campose_valid"][a][i]:
            return None
        X = np.asarray(d["jointPositions"][a][i]).reshape(24, 3)[SMPL]
        T = np.asarray(d["cam_poses"][i])
        Xc = X @ T[:3, :3].T + T[:3, 3]
        uv = Xc @ K.T
        uv = uv[:, :2] / uv[:, 2:] * k_scale
        return Xc, uv

    pids = sorted(preds)
    cost = np.full((len(pids), n_act), 1e9)
    for r, pid in enumerate(pids):
        for a in range(n_act):
            ds = [np.linalg.norm(preds[pid][f]["pred_keypoints_2d"][MHR] - g[1], axis=-1).mean()
                  for f in preds[pid] if (g := gt_at(a, f)) is not None]
            if ds:
                cost[r, a] = np.mean(ds)
    rows, cols = linear_sum_assignment(cost)

    pa, mp, e2 = [], [], []
    for r, a in zip(rows, cols):
        if cost[r, a] >= 1e9:
            continue
        for f, o in preds[pids[r]].items():
            g = gt_at(a, f)
            if g is None:
                continue
            p3 = np.asarray(o["pred_keypoints_3d"])[MHR].astype(np.float64)
            g3 = g[0]
            pa.append(np.linalg.norm(procrustes(p3, g3) - g3, axis=-1).mean() * 1000)
            mp.append(np.linalg.norm((p3 - p3[HIPS].mean(0)) - (g3 - g3[HIPS].mean(0)), axis=-1).mean() * 1000)
            e2.append(np.linalg.norm(np.asarray(o["pred_keypoints_2d"])[MHR] - g[1], axis=-1).mean())
    return np.array(pa), np.array(mp), np.array(e2)


def main(manifest, runs):
    rows = {r["run"]: r for r in csv.DictReader(open(manifest, encoding="utf-8"))}
    for spec in runs:
        run_dir, _, name = spec.partition("=")
        if not name:
            parts = os.path.normpath(run_dir).split(os.sep)
            name = next(p for p in reversed(parts) if p in rows)
        pa, mp, e2 = evaluate(rows[name], run_dir)
        print(f"{run_dir}\t{name}\tn={len(pa)}\tPA-MPJPE {pa.mean():.1f}\tMPJPE {mp.mean():.1f}\t2D {e2.mean():.2f} px")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
