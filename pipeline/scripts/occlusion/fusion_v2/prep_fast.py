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


def frame_meshes(run, i):
    """{pid: (mesh depth, keypoints, focal)} of the persons that have a mesh in frame i (prep's per-frame work).
    Reads mesh_4d_individual/<pid>/<stem>.ply directly, so it also works before the run's pid list is final."""
    out = {}
    stem = run.stems[i]
    for d in sorted(os.listdir(f"{run.pout}/mesh_4d_individual")) if os.path.isdir(f"{run.pout}/mesh_4d_individual") else []:
        if not os.path.exists(f"{run.pout}/mesh_4d_individual/{d}/{stem}.ply"):
            continue
        pid = int(d)
        m = run.mesh(pid, i)
        if m is None:
            continue
        f = run.focal(pid, i)
        out[pid] = (render_depth(m[0], m[1], f, run.W, run.H), m[0][KP2V], f)
    return out


class Ahead:
    """Frames prepared ahead (avatar_app --wait_mesh, while HMR still runs): the mesh depth goes to a scratch file
    as float16 (the dtype prep's D stores, so the same values), keypoints and focal lengths stay in RAM.
    pids: the tracked labels (a superset of the persons that get meshes)."""

    def __init__(self, cache, T, pids, H, W):
        os.makedirs(cache, exist_ok=True)
        self.path = f"{cache}/_prep_ahead_D.npy"
        self.D = disk_arrays.scratch(self.path, (T, max(len(pids), 1), H, W), np.float16)
        self.col = {p: k for k, p in enumerate(pids)}
        self.meta = {}   # frame -> {pid: (keypoints, focal)}

    def put(self, i, fm):
        self.meta[i] = {}
        for pid, (d, kp, f) in fm.items():
            if pid not in self.col:   # not a tracked label: leave the frame to prep
                del self.meta[i]
                return
            self.D[i, self.col[pid]] = d
            self.meta[i][pid] = (kp, f)

    def __contains__(self, i):
        return i in self.meta

    def __len__(self):
        return len(self.meta)

    def release(self):
        self.D = None
        disk_arrays.release(self.path)


def prep(run, cache, pre=None):
    """pre: an Ahead with frames computed while HMR still ran; the other frames are computed here. The same arrays
    either way."""
    os.makedirs(cache, exist_ok=True)
    p = f"{cache}/mesh.npz"
    if os.path.exists(p):
        if pre is not None:
            pre.release()
        return
    T, P = len(run), len(run.pids)
    tmp = {k: f"{cache}/_prep_{k}.npy" for k in ("D", "L", "I")}
    D = disk_arrays.scratch(tmp["D"], (T, P, run.H, run.W), np.float16)
    KP = np.full((T, P, 70, 3), np.nan, np.float32)
    has = np.zeros((T, P), bool)
    foc = np.zeros((T, P), np.float32)
    for i in range(T):
        if pre is not None and i in pre:
            for k, pid in enumerate(run.pids):
                if pid in pre.meta[i]:
                    D[i, k] = pre.D[i, pre.col[pid]]
                    KP[i, k], foc[i, k] = pre.meta[i][pid]
                    has[i, k] = True
            continue
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
    if pre is not None:
        pre.release()
