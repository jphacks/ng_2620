"""Score hide masks against the audited occlusion GT (testdata/2026-10-11/occ_research/_gt/gt_audit).

Reconstruction of the study's scorer from BRIEF_COMMON.md / BRIEF_ROUND3.md (the original _orchestrator/score.py
was not restored):
  * GT: gt_audit/<clip>/<pid>/<frame:08d>.png, 1 = a real surface is in front of the person, 0 = nothing in front,
    255 = ignore (undecidable, boundary band, outside the labelled region = mesh dilated 10 px)
  * domain per key frame and pid: the pid's mesh-silhouette pixels (mesh.npz D > 0) with GT != 255
  * hidden = hide png >= 128 (missing png = nothing hidden)
  * per clip, pooled over key frames and pids: leak = fn / pos, overcut = fp / neg, F1 = 2tp / (2tp + fp + fn)
  * pass = overcut <= 0.05 and (F1 >= 0.90 or the clip has fewer than MIN_POS = 100 positive pixels)
  Checked against the original scorer's saved outputs (_gt/orig_scores: s_*.json on dev 51, t6_*.json on test6):
  pos / neg / F1 / overcut are identical where the meshes are the same, and this pass rule reproduces every saved
  pass count (43, 44, 44, 43, 43, 43 on dev 51; 7 x4 on test6). F1 is 0 on a clip without positives, as there.
usage: python score_gt.py <variant root (has hide/ and _r3cache/)> [clip ...]   -> prints, writes $SCORE_OUT when set
"""
import glob
import json
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))).replace("\\", "/")
GT = f"{REPO}/testdata/2026-10-11/occ_research/_gt/gt_audit"
MIN_POS = 100
# penn0363 pid 2 is the lifter's reflection in a wall mirror (GT tools/build_0363.py). The original scorer left it
# out: with it excluded, pos / neg match its saved output (86 / 84592); with it, they do not (1028 / 99166).
EXCLUDE = {("penn0363", 2)}


def score_clip(root, clip, mesh_npz=None):
    mesh_npz = mesh_npz or f"{root}/_r3cache/{clip}/mesh.npz"
    z = np.load(mesh_npz)
    pids = list(z["pids"])
    D = z["D"]
    tp = fp = fn = pos = neg = 0
    nk = 0
    for gd in sorted(glob.glob(f"{GT}/{clip}/*")):
        pid = int(os.path.basename(gd))
        if pid not in pids or (clip, pid) in EXCLUDE:
            continue
        k = pids.index(pid)
        for f in sorted(glob.glob(f"{gd}/*.png")):
            stem = os.path.basename(f)[:-4]
            i = int(stem)
            if i >= D.shape[0]:
                continue
            G = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
            dom = (D[i, k] > 0) & (G != 255)
            hp = f"{root}/hide/{clip}/{pid}/{stem}.png"
            h = cv2.imread(hp, cv2.IMREAD_GRAYSCALE) >= 128 if os.path.exists(hp) else np.zeros(G.shape, bool)
            g1 = G == 1
            tp += int((dom & g1 & h).sum())
            fp += int((dom & ~g1 & h).sum())
            fn += int((dom & g1 & ~h).sum())
            pos += int((dom & g1).sum())
            neg += int((dom & ~g1).sum())
            nk += 1
    f1 = 2 * tp / max(2 * tp + fp + fn, 1)
    oc = fp / max(neg, 1)
    return dict(F1=f1, leak=fn / max(pos, 1), overcut=oc, pos=pos, neg=neg, tp=tp, fp=fp, fn=fn, keys=nk,
                passed=bool(oc <= 0.05 and (f1 >= 0.90 or pos < MIN_POS)))


def main():
    root = sys.argv[1]
    clips = sys.argv[2:] or sorted(os.path.basename(d) for d in glob.glob(f"{root}/hide/*")
                                   if os.path.isdir(f"{GT}/{os.path.basename(d)}"))
    res = {}
    for c in clips:
        if not os.path.exists(f"{root}/_r3cache/{c}/mesh.npz"):
            continue
        res[c] = score_clip(root, c)
    for c, r in res.items():
        print(f"{c}: F1 {r['F1']:.3f} leak {r['leak']:.3f} overcut {r['overcut']:.4f} pos {r['pos']} "
              f"{'PASS' if r['passed'] else 'fail'}")
    print(f"passed {sum(r['passed'] for r in res.values())}/{len(res)}")
    if os.environ.get("SCORE_OUT"):
        json.dump(res, open(os.environ["SCORE_OUT"], "w"), indent=1)


if __name__ == "__main__":
    main()
