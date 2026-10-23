"""prep.prep with the whole-video arrays (D, L, I) built in scratch files instead of RAM: same loops, same dtypes,
same np.savez_compressed call, so mesh.npz holds the same arrays (scripts/eval/check_prep_fast.py). The frozen prep
holds D, L and I at once (a 5-minute 270x480 clip: 4.4 + 1.1 + 3.3 GB) while compressing them.
The meshes are drawn by r3common.render_depth_fast (no vertex normals: the same depth, 14.9 -> 2.0 ms per mesh)."""
import os
import sys

import numpy as np

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ME)
import disk_arrays  # noqa: E402
from prep import KP2V  # noqa: E402
from r3common import render_depth_fast as render_depth  # noqa: E402  (same depth, no vertex normals)


def prep(run, cache):
    os.makedirs(cache, exist_ok=True)
    p = f"{cache}/mesh.npz"
    if os.path.exists(p):
        return
    T, P = len(run), len(run.pids)
    tmp = {k: f"{cache}/_prep_{k}.npy" for k in ("D", "L", "I")}
    D = disk_arrays.scratch(tmp["D"], (T, P, run.H, run.W), np.float16)
    KP = np.full((T, P, 70, 3), np.nan, np.float32)
    has = np.zeros((T, P), bool)
    foc = np.zeros((T, P), np.float32)
    for i in range(T):
        for k, pid in enumerate(run.pids):
            m = run.mesh(pid, i)
            if m is None:
                continue
            f = run.focal(pid, i)
            D[i, k] = render_depth(m[0], m[1], f, run.W, run.H)
            KP[i, k] = m[0][KP2V]
            has[i, k] = True
            foc[i, k] = f
    # np.stack of the per-frame masks / images, filled in place
    m0, i0 = run.mask(0), run.image(0)
    L = disk_arrays.scratch(tmp["L"], (T,) + m0.shape, m0.dtype)
    I = disk_arrays.scratch(tmp["I"], (T,) + i0.shape, i0.dtype)
    for i in range(T):
        L[i] = run.mask(i)
        I[i] = run.image(i)
    part = p + ".part"
    with open(part, "wb") as f:
        np.savez_compressed(f, D=D, KP=KP, has=has, foc=foc, L=L, I=I, pids=np.array(run.pids))
    del D, L, I
    os.replace(part, p)
    for t in tmp.values():
        disk_arrays.release(t)
