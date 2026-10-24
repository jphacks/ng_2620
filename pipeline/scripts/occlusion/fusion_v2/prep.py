"""CPU precompute per run: mesh depth of every pid/frame, SAM3 label maps, images, 3D keypoints (MHR70 via nearest
vertex) and focal.  -> cache/<run>/mesh.npz"""
import os
import sys

import numpy as np

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ME)
from r3common import Run  # noqa: E402

KP2V = np.load(f"{ME}/kp2vert.npy")


def prep(run, cache):
    os.makedirs(cache, exist_ok=True)
    p = f"{cache}/mesh.npz"
    if os.path.exists(p):
        return
    T, P = len(run), len(run.pids)
    D = np.zeros((T, P, run.H, run.W), np.float16)
    KP = np.full((T, P, 70, 3), np.nan, np.float32)
    has = np.zeros((T, P), bool)
    foc = np.zeros((T, P), np.float32)
    for i in range(T):
        for k, pid in enumerate(run.pids):
            m = run.mesh(pid, i)
            if m is None:
                continue
            f = run.focal(pid, i)
            from r3common import render_depth
            D[i, k] = render_depth(m[0], m[1], f, run.W, run.H)
            KP[i, k] = m[0][KP2V]
            has[i, k] = True
            foc[i, k] = f
    L = np.stack([run.mask(i) for i in range(T)])
    I = np.stack([run.image(i) for i in range(T)])
    np.savez_compressed(p, D=D, KP=KP, has=has, foc=foc, L=L, I=I, pids=np.array(run.pids))


def load(cache):
    z = np.load(f"{cache}/mesh.npz")
    return {k: z[k] for k in z.files}


if __name__ == "__main__":
    import time
    for name in sys.argv[1:]:
        t = time.time()
        r = Run(name)
        prep(r, f"{ME}/cache/{name}")
        print(name, round(time.time() - t, 1))
