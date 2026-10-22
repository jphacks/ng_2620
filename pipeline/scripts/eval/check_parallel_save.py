"""Check that saving with several threads (offline_app RUNTIME['save_workers']) writes the same files as one thread.

HMR is not bitwise repeatable, so two pipeline runs cannot be compared file by file. Instead the frames captured by
one run (offline_app_lite --capture) are saved twice from the same HMR outputs: serially, and with N threads.

usage (repo root): .venv/Scripts/python.exe scripts/eval/check_parallel_save.py <run_dir> [N]
  run_dir holds captures/hmr_outputs.pkl and pipeline_out/images; output goes to <run_dir>/check_parallel_save/
"""
import filecmp
import glob
import os
import pickle
import shutil
import sys
import types
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import offline_app as oa  # noqa: E402


def save_all(cap, out_dir, workers):
    app = oa.OfflineApp.__new__(oa.OfflineApp)
    app.OUTPUT_DIR = out_dir
    app.sam3_3d_body_model = types.SimpleNamespace(faces=cap["faces"])
    jobs = []
    for b in cap["hmr_batches"]:
        jobs += app._save_jobs(b["image_path"], b["outputs"], b["id_batch"], b["empty_frame_list"])
    ids = sorted({i for b in cap["hmr_batches"] for ids in b["id_batch"] for i in ids})
    os.makedirs(f"{out_dir}/rendered_frames", exist_ok=True)
    for i in ids:
        for sub in ("rendered_frames_individual", "mesh_4d_individual", "focal_4d_individual"):
            os.makedirs(f"{out_dir}/{sub}/{i}", exist_ok=True)
    if workers == 1:
        for j in jobs:
            app._save_frame(*j)
    else:
        with ThreadPoolExecutor(workers) as pool:
            for f in [pool.submit(app._save_frame, *j) for j in jobs]:
                f.result()


def main(run_dir, workers):
    cap = pickle.load(open(os.path.join(run_dir, "captures", "hmr_outputs.pkl"), "rb"))
    root = os.path.join(run_dir, "check_parallel_save")
    shutil.rmtree(root, ignore_errors=True)
    a, b = os.path.join(root, "serial"), os.path.join(root, f"threads{workers}")
    save_all(cap, a, 1)
    save_all(cap, b, workers)
    ok = True
    files = sorted(f for f in glob.glob(f"{a}/**/*", recursive=True) if os.path.isfile(f))
    diff_files, max_px = [], 0
    for x in files:
        y = b + x[len(a):]
        if not os.path.exists(y):
            diff_files.append(x)
        elif x.endswith(".jpg"):
            d = np.abs(cv2.imread(x).astype(int) - cv2.imread(y).astype(int)).max()
            max_px = max(max_px, d)
            if d:
                diff_files.append(x)
        elif not filecmp.cmp(x, y, shallow=False):
            diff_files.append(x)
    ok = not diff_files
    print(f"{len(files)} files; differing {len(diff_files)}; max jpg pixel diff {max_px}")
    for f in diff_files[:10]:
        print("  ", f)
    print("identical" if ok else "DIFFERENT")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3) else 1)
