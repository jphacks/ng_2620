"""Temporal smoothing of the MHR skeletons that drive the avatars (offline: the whole track is known).

The per-frame HMR estimates jitter (joint rotations, joint positions, and above all cam_t depth). Before retargeting:
  1. zero-phase Gaussian smoothing over each contiguous run of tracked frames, never across a gap:
     joint_coords and cam_t linearly (the sum the retarget uses is then the smoothed sum), joint_rots as
     sign-aligned quaternions, weighted mean, renormalised (window truncated and renormalised at run ends)
  2. foot contact lock: where an ankle is (nearly) still in camera space and is the lower foot, its position is held
     at the mean of that contact run (blended in / out over `ramp` frames), so the leg IK keeps the sole planted.
     Detection is in camera space: under a moving camera a planted foot moves, is not detected and is left alone.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from .retarget_mhr import _SIDE


def _runs(idx):
    """sorted frame indices -> [[i0, i1, ...], ...] of consecutive frames."""
    out = []
    for i in idx:
        if out and i == out[-1][-1] + 1:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def _gauss(x, sigma):
    """Zero-phase Gaussian along axis 0, truncated window renormalised at the ends."""
    if sigma <= 0 or len(x) < 2:
        return x.copy()
    r = int(np.ceil(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    T = len(x)
    flat = x.reshape(T, -1).astype(np.float64)
    out = np.zeros_like(flat)
    wsum = np.zeros(T)
    for o, w in zip(range(-r, r + 1), k):
        a, b = max(0, -o), min(T, T - o)        # out[t] += w * x[t + o]
        if a < b:
            out[a:b] += w * flat[a + o:b + o]
            wsum[a:b] += w
    return (out / wsum[:, None]).reshape(x.shape)


def _smooth_rots(R, sigma):
    """R (T, J, 3, 3) -> smoothed rotations: per joint, quaternions sign-aligned along time, Gaussian mean."""
    T, J = R.shape[:2]
    q = Rotation.from_matrix(R.reshape(-1, 3, 3).astype(np.float64)).as_quat().reshape(T, J, 4)
    for t in range(1, T):
        q[t] *= np.where((q[t] * q[t - 1]).sum(-1, keepdims=True) < 0, -1.0, 1.0)
    q = _gauss(q, sigma)
    q /= np.linalg.norm(q, axis=-1, keepdims=True)
    return Rotation.from_quat(q.reshape(-1, 4)).as_matrix().reshape(T, J, 3, 3).astype(R.dtype)


def _contact_lock(P, fps, speed=0.25, below=0.05, min_len=3, ramp=2):
    """P (T, J, 3) camera-space joints (y down), modified in place at the ankles. -> frames locked per side."""
    ank = {s: _SIDE[s]["ankle"] for s in ("left", "right")}
    T = len(P)
    if T < min_len:
        return {s: 0 for s in ank}
    low = np.maximum(P[:, ank["left"], 1], P[:, ank["right"], 1])   # y of the lower ankle
    locked = {}
    for s, j in ank.items():
        p = P[:, j].copy()
        v = np.zeros(T)
        v[1:-1] = np.linalg.norm(p[2:] - p[:-2], axis=-1) * fps / 2
        v[0], v[-1] = v[1], v[-2]
        c = (v < speed) & (p[:, 1] >= low - below)
        w = np.zeros(T)
        n = 0
        for run in _runs(np.flatnonzero(c).tolist()):
            if len(run) < min_len:
                continue
            a, b = run[0], run[-1]
            m = p[a:b + 1].mean(0)
            P[a:b + 1, j] = m
            w[a:b + 1] = 1.0
            n += len(run)
            for k in range(1, ramp + 1):        # fade the hold in / out over the frames around the run
                f = 1.0 - k / (ramp + 1)
                for t in (a - k, b + k):
                    if 0 <= t < T and w[t] < f:
                        P[t, j] = p[t] + f * (m - p[t])
                        w[t] = f
        locked[s] = n
    return locked


def smooth_skeletons(sk, pids, sigma_s=0.05, fps=30.0, foot_lock=True):
    """sk {(pid, frame): npz dict} -> new dict with smoothed joint_rots / joint_coords / cam_t. sigma_s in seconds
    (0 disables the Gaussian). -> (sk', stats)."""
    out = dict(sk)
    sigma = sigma_s * fps
    stats = {}
    for pid in pids:
        frames = sorted(i for p, i in sk if p == pid)
        jit0 = jit1 = 0.0
        cnt = 0
        locked = {"left": 0, "right": 0}
        for run in _runs(frames):
            zs = [sk[(pid, i)] for i in run]
            R = np.stack([z["joint_rots"] for z in zs])
            C = np.stack([z["joint_coords"] for z in zs]).astype(np.float64)
            t = np.stack([z["cam_t"] for z in zs]).astype(np.float64)
            Rs, Cs, ts = _smooth_rots(R, sigma), _gauss(C, sigma), _gauss(t, sigma)
            P = Cs + ts[:, None]
            if foot_lock:
                for s, k in _contact_lock(P, fps).items():
                    locked[s] += k
            if len(run) >= 3:   # jitter = mean |second difference| of the camera-space joints [m / frame^2]
                P0 = C + t[:, None]
                jit0 += np.linalg.norm(P0[2:] - 2 * P0[1:-1] + P0[:-2], axis=-1).mean() * (len(run) - 2)
                jit1 += np.linalg.norm(P[2:] - 2 * P[1:-1] + P[:-2], axis=-1).mean() * (len(run) - 2)
                cnt += len(run) - 2
            for k, i in enumerate(run):
                z = dict(sk[(pid, i)])
                z["joint_rots"] = Rs[k].astype(z["joint_rots"].dtype)
                z["cam_t"] = ts[k].astype(z["cam_t"].dtype)
                z["joint_coords"] = (P[k] - ts[k]).astype(z["joint_coords"].dtype)
                out[(pid, i)] = z
        if cnt:
            stats[f"jitter_{pid}_mm"] = [round(1000 * jit0 / cnt, 2), round(1000 * jit1 / cnt, 2)]
        stats[f"foot_lock_{pid}_frames"] = locked
    return out, stats
