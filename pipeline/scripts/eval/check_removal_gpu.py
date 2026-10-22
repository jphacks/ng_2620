"""removal_gpu.clean_plates_gpu == removal.clean_plates? Plates, holes and stats compared byte for byte, on the
frames and person masks that avatar_app builds (with --hq: the original frames and the masks upscaled).

usage (repo root):
  .venv/Scripts/python.exe scripts/eval/check_removal_gpu.py --pipeline_out <run>/pipeline_out [--hq <frames dir>]
      [--frames 120] [--start 0] [--workers 4] [--gpu_only] [--scratch <dir>]
exit 0 = IDENTICAL, 1 = differences
"""
import argparse
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, "scripts"), os.path.join(ROOT, "scripts", "occlusion", "fusion_v2"), ROOT]

from avatar_app import source_frames, source_size  # noqa: E402
from avatar.removal import clean_plates  # noqa: E402
from avatar.removal_gpu import clean_plates_gpu  # noqa: E402
from r3common import Run  # noqa: E402


def _sub(a, name):
    if not a.scratch:
        return None
    os.makedirs(f"{a.scratch}/{name}", exist_ok=True)
    return f"{a.scratch}/{name}"


def main(a):
    run = Run(a.pipeline_out)
    H, W = run.H, run.W
    sel = list(range(a.start, min(len(run), a.start + a.frames)))
    stems = [run.stems[i] for i in sel]
    OH, OW = source_size(a.hq) if a.hq else (H, W)
    sc = OW / W
    frames = [None] * len(sel)
    if a.scratch:   # avatar_app's layout: memory-mapped arrays (a long 1080x1920 window does not fit in RAM)
        from disk_arrays import scratch as disk
        frames = disk(f"{a.scratch}/frames.npy", (len(sel), OH, OW, 3), np.uint8)
    if a.hq:
        for i, img in source_frames(a.hq, stems, OH, OW):
            frames[i] = img
    else:
        for j, i in enumerate(sel):
            frames[j] = run.image(i)
    pids = run.pids
    pm = disk(f"{a.scratch}/person_masks.npy", (len(sel), OH, OW), bool) if a.scratch else [None] * len(sel)
    for j, i in enumerate(sel):
        m = np.isin(run.mask(i), pids)
        pm[j] = m if sc == 1 else cv2.resize(m.astype(np.uint8), (OW, OH), interpolation=cv2.INTER_NEAREST) > 0
    kw = dict(fps=30.0, dilate=max(1, round(9 * sc)), exclude_dilate=max(1, round(15 * sc)))
    print(f"{run.name}: frames {sel[0]}..{sel[-1]} at {OW}x{OH}, dilate {kw['dilate']}/{kw['exclude_dilate']}", flush=True)
    t0 = time.perf_counter()
    g_out, g_holes, g_stats = clean_plates_gpu(frames, pm, workers=a.workers, scratch=_sub(a, "gpu"), **kw)
    t_gpu = time.perf_counter() - t0
    print(f"gpu {t_gpu:.1f} s ({t_gpu / len(sel):.3f} s/frame)", flush=True)
    if a.gpu_only:
        return 0
    t0 = time.perf_counter()
    r_out, r_holes, r_stats = clean_plates(frames, pm, workers=4, scratch=_sub(a, "cpu"), **kw)
    t_ref = time.perf_counter() - t0
    print(f"cpu {t_ref:.1f} s ({t_ref / len(sel):.3f} s/frame)", flush=True)
    bad_p = [i for i in range(len(sel)) if not np.array_equal(g_out[i], r_out[i])]
    bad_h = [i for i in range(len(sel)) if not np.array_equal(g_holes[i], r_holes[i])]
    ok = not bad_p and not bad_h and g_stats == r_stats
    print(f"plates differ in {len(bad_p)} frames {bad_p[:5]}, holes in {len(bad_h)}, stats {g_stats} vs {r_stats}")
    for i in bad_p[:3]:
        d = np.abs(g_out[i].astype(int) - r_out[i].astype(int))
        print(f"  frame {sel[i]}: {int((d > 0).any(-1).sum())} px differ, max {d.max()}")
    print("IDENTICAL" if ok else "DIFFERENT")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline_out", required=True)
    ap.add_argument("--hq", default=None, help="original frames folder (avatar_app --hq)")
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--gpu_only", action="store_true", help="time the GPU fill only")
    ap.add_argument("--scratch", default=None, help="folder for memory-mapped frames, masks and removal arrays")
    sys.exit(main(ap.parse_args()))
