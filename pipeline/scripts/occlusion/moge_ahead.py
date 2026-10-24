"""MoGe-2 depth for fusion v2, computed while the mesh stage still runs its HMR (offline_app_lite --moge_ahead).

fusion v2's GPU stage starts with MoGe-2 on every frame (~15 s per 120 frames on an RTX 5060 Ti). MoGe reads only the
pipeline's images/ and its focal length, both known before HMR ends, so offline_app_lite can start this script in its
own process once the tracking has written images/. It waits for HMR's first focal_4d_individual file (the focal fusion
uses), runs gpu_stage_fast.moge_depth, the same loop as run_gpu's, and writes <pipeline_out>/moge_level<N>.npz;
run_gpu copies it into its cache when the FoV and the level match. A separate process because SAM3 switches TF32 on
for the whole mesh process and fusion's MoGe runs without it: here nothing imports SAM3, and the depth was the same
bytes as fusion's own on downtown_bar.

Opt-in: on an RTX 5060 Ti the two processes time-slice the GPU (HMR 17.7 -> 29.0 s) and the end-to-end time only
moved 111.9 -> 111.3 s. A GPU with more headroom may gain more.

usage: python scripts/occlusion/moge_ahead.py --pipeline_out <dir> [--wait_s 600]
"""
import argparse
import glob
import os
import sys
import time

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ME, "fusion_v2"))


def main(a):
    t0 = time.time()
    while not glob.glob(f"{a.pipeline_out}/focal_4d_individual/*/*.json"):
        if time.time() - t0 > a.wait_s:
            raise SystemExit(f"[moge_ahead] no focal_4d_individual/ after {a.wait_s} s")
        time.sleep(0.2)
    time.sleep(0.2)   # the first file may still be being written
    import numpy as np
    from r3common import Run
    import disk_arrays
    import gpu_stage_fast as gs
    run = Run(a.pipeline_out)
    fov_x = gs.moge_fov_x(run)
    out = gs.ahead_path(run)
    t1 = time.time()
    Z = disk_arrays.scratch(out + ".Z.npy", (len(run), run.H, run.W), np.float32)
    gs.moge_depth(run, Z, fov_x)
    gs._save_moge(out, Z, fov_x, gs.MOGE_LEVEL)
    del Z
    disk_arrays.release(out + ".Z.npy")
    print(f"[moge_ahead] {len(run)} frames in {time.time() - t1:.1f} s (waited {t1 - t0:.1f} s for the focal)",
          flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline_out", required=True)
    ap.add_argument("--wait_s", type=float, default=600)
    main(ap.parse_args())
