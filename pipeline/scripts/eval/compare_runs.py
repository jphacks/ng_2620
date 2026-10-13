"""Compare two SAM-Body4D runs of the same clip (e.g. offline_app.py vs offline_app_lite.py, or local vs Modal).

Each run dir holds pipeline_out/masks/*.png and captures/hmr_outputs.pkl (run_profiled.py / offline_app_lite.py).

usage: .venv/Scripts/python.exe scripts/eval/compare_runs.py <run_a> <run_b>
"""
import glob
import os
import pickle
import sys

import numpy as np
from PIL import Image


def per_frame(cap):
    """{(frame stem, person id): output dict} from the captured HMR batches."""
    out = {}
    for b in cap["hmr_batches"]:
        k = 0
        for fi, path in enumerate(b["image_path"]):
            if fi in b["empty_frame_list"]:
                continue
            stem = os.path.splitext(os.path.basename(path))[0]
            for pid, o in zip(b["id_batch"][k], b["outputs"][k]):
                out[(stem, pid)] = o
            k += 1
    return out


def main(a, b):
    ma = sorted(glob.glob(f"{a}/pipeline_out/masks/*.png"))
    mb = {os.path.basename(p): p for p in glob.glob(f"{b}/pipeline_out/masks/*.png")}
    ious, exact = [], 0
    for p in ma:
        A = np.array(Image.open(p))
        B = np.array(Image.open(mb[os.path.basename(p)]))
        exact += int((A == B).all())
        for pid in sorted(set(np.unique(A)) | set(np.unique(B)) - {0}):
            u = ((A == pid) | (B == pid)).sum()
            ious.append(((A == pid) & (B == pid)).sum() / u if u else 1.0)
    ious = np.array(ious)
    print(f"masks: {len(ma)} frames, identical {exact}/{len(ma)}, IoU mean {ious.mean():.5f} min {ious.min():.5f}")

    ca = pickle.load(open(f"{a}/captures/hmr_outputs.pkl", "rb"))
    cb = pickle.load(open(f"{b}/captures/hmr_outputs.pkl", "rb"))
    print("detection bbox a:", [np.round(o["bbox"], 3).tolist() for o in ca["detection"][-1]["outputs"]])
    print("detection bbox b:", [np.round(o["bbox"], 3).tolist() for o in cb["detection"][-1]["outputs"]])
    fa, fb = per_frame(ca), per_frame(cb)
    keys = sorted(set(fa) & set(fb))
    print(f"HMR: {len(keys)} common (frame, person) pairs; only in a {len(set(fa) - set(fb))}, only in b {len(set(fb) - set(fa))}")
    kp = np.array([np.linalg.norm(fa[k]["pred_keypoints_2d"] - fb[k]["pred_keypoints_2d"], axis=-1).mean() for k in keys])
    kpmax = np.array([np.linalg.norm(fa[k]["pred_keypoints_2d"] - fb[k]["pred_keypoints_2d"], axis=-1).max() for k in keys])
    va = lambda o: o["pred_vertices"] + o["pred_cam_t"]  # noqa: E731  camera-space vertices
    vd = np.array([np.linalg.norm(va(fa[k]) - va(fb[k]), axis=-1).mean() for k in keys]) * 1000
    vdmax = np.array([np.linalg.norm(va(fa[k]) - va(fb[k]), axis=-1).max() for k in keys]) * 1000
    foc = np.array([abs(float(fa[k]["focal_length"]) - float(fb[k]["focal_length"])) / float(fb[k]["focal_length"])
                    for k in keys])
    print(f"focal rel diff: max {foc.max():.2e}")
    print(f"2D keypoints: mean {kp.mean():.4f} px, max {kpmax.max():.3f} px")
    print(f"camera-space vertices: mean {vd.mean():.3f} mm, max {vdmax.max():.3f} mm")
    for name, cap in (("a", ca), ("b", cb)):
        t = cap.get("timing", {})
        print(f"timing {name}: total {t.get('total_s', float('nan')):.1f} s, "
              f"max reserved {t.get('max_memory_reserved_GB', float('nan')):.2f} GB")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
