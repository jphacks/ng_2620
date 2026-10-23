"""SAM 3D Body on every k-th frame only; the frames in between interpolated from their two keyframes.

offline_app_lite --hmr_stride k. HMR (17.5 s per 120 frames on an RTX 5060 Ti, the largest stage) runs on the
keyframes; every other frame gets the per-person outputs the save code reads (pred_vertices, pred_cam_t,
pred_joint_coords, pred_keypoints_3d: linear; pred_global_rots: quaternion slerp; focal_length: the keyframe's) and
is saved through the same OfflineApp._save_frame, so meshes, focal lengths, skeletons and overlays keep their format.

Keyframes: 0, k, 2k, ... and the last frame, plus every frame whose tracked persons (mask labels) differ from those of
either bracketing keyframe (a person entering or leaving is never interpolated). A frame that still cannot be
interpolated (HMR returned a different person set at a keyframe) is run through HMR afterwards.
Interpolated frames are written as soon as both of their keyframes are saved (keyframe outputs are dropped after).
scripts/eval/keyframe_interp_study.py estimated the cost: at k=2, joints after the avatar's smoothing 2.5-4.4 mm.
"""
import glob as _glob
import os
import threading
import types

import numpy as np
from PIL import Image

ROT_KEYS = ("pred_global_rots",)


def _labels(mask_path):
    return frozenset(int(v) for v in np.unique(np.array(Image.open(mask_path))) if v)


def keyframes(masks, k):
    """Indices of the frames HMR runs on (see module docstring)."""
    n = len(masks)
    labs = [_labels(m) for m in masks]
    key = set(range(0, n, k)) | {n - 1}
    changed = True
    while changed:
        changed = False
        ks = sorted(key)
        for a, b in zip(ks[:-1], ks[1:]):
            for t in range(a + 1, b):
                if labs[t] != labs[a] or labs[t] != labs[b]:
                    key.add(t)
                    changed = True
    return sorted(key), labs


def _slerp_rots(Ra, Rb, w):
    import roma
    import torch
    qa = roma.rotmat_to_unitquat(torch.from_numpy(np.asarray(Ra, np.float64)))
    qb = roma.rotmat_to_unitquat(torch.from_numpy(np.asarray(Rb, np.float64)))
    q = roma.unitquat_slerp(qa, qb, torch.tensor([w], dtype=torch.float64), shortest_arc=True)[0]
    return roma.unitquat_to_rotmat(q).numpy().astype(np.asarray(Ra).dtype)


def interpolate(pa, pb, w):
    """One person's output at weight w between keyframe outputs pa (w=0) and pb (w=1)."""
    out = {}
    for key, va in pa.items():
        vb = pb.get(key)
        if key in ROT_KEYS and vb is not None:
            out[key] = _slerp_rots(va, vb, w)
        elif (isinstance(va, np.ndarray) and isinstance(vb, np.ndarray) and va.shape == vb.shape
              and va.dtype.kind == "f" and key != "focal_length"):
            out[key] = ((1 - w) * va + w * vb).astype(va.dtype)
        else:
            out[key] = va
    return out


def run(app, stride):
    """app.on_4d_generation() with HMR on the keyframes only (see module docstring). -> stats dict."""
    import offline_app as oa
    img_dir, mask_dir = os.path.join(app.OUTPUT_DIR, "images"), os.path.join(app.OUTPUT_DIR, "masks")
    images = sorted(_glob.glob(os.path.join(img_dir, "*.jpg")))
    masks = sorted(_glob.glob(os.path.join(mask_dir, "*.png")))
    n = len(images)
    keys, labs = keyframes(masks, stride)
    idx = {os.path.normcase(os.path.abspath(p)): i for i, p in enumerate(images)}
    keyset = set(keys)
    nxt = {}
    for a, b in zip(keys[:-1], keys[1:]):
        for t in range(a + 1, b):
            nxt[t] = (a, b)
    done, pending, lock = {}, set(range(n)) - keyset, threading.Lock()
    retry = []
    save = app._save_frame

    def emit(t, ka, kb):
        """ka, kb: done[a], done[b] (taken under the lock: another thread may drop them afterwards)."""
        a, b = nxt[t]
        (oa_, ida), (ob, idb) = ka, kb
        ids = sorted(labs[t])
        if oa_ is None or ob is None or sorted(ida or []) != ids or sorted(idb or []) != ids:
            if ids or oa_ is not None or ob is not None:
                return False   # HMR's person sets differ from the masks': run HMR on t
            save(images[t], None, None)
            return True
        pa = dict(zip(ida, oa_))
        pb = dict(zip(idb, ob))
        w = (t - a) / (b - a)
        save(images[t], [interpolate(pa[i], pb[i], w) for i in ids], ids)
        return True

    def save_frame(image_path, mask_output, id_current):
        save(image_path, mask_output, id_current)
        i = idx.get(os.path.normcase(os.path.abspath(image_path)))
        if i is None or i not in keyset:
            return
        with lock:
            done[i] = (mask_output, list(id_current) if id_current is not None else None)
            ready = [(t, done[nxt[t][0]], done[nxt[t][1]]) for t in pending if all(x in done for x in nxt[t])]
            pending.difference_update(t for t, _, _ in ready)
        for t, ka, kb in ready:
            if not emit(t, ka, kb):
                with lock:
                    retry.append(t)
        with lock:   # drop the keyframe outputs no pending in-between frame needs
            need = {x for t in pending for x in nxt[t]}
            for j in [j for j in done if j not in need and j != i]:
                del done[j]

    def filtered_glob(frames):
        allowed = {os.path.normcase(os.path.abspath(images[i])) for i in frames}
        allowed |= {os.path.normcase(os.path.abspath(masks[i])) for i in frames}

        def g(pattern, *a, **k):
            res = _glob.glob(pattern, *a, **k)
            d = os.path.normcase(os.path.abspath(os.path.dirname(pattern)))
            if d in (os.path.normcase(os.path.abspath(img_dir)), os.path.normcase(os.path.abspath(mask_dir))):
                res = [p for p in res if os.path.normcase(os.path.abspath(p)) in allowed]
            return res
        return types.SimpleNamespace(**{**{k2: getattr(_glob, k2) for k2 in dir(_glob) if not k2.startswith("_")},
                                        "glob": g})

    real_glob = oa.glob
    app._save_frame = save_frame
    try:
        oa.glob = filtered_glob(keys)
        app.on_4d_generation()
        if retry:   # frames whose keyframes' person sets did not match: HMR on them
            keyset.update(retry)
            oa.glob = filtered_glob(sorted(retry))
            app._save_frame = save
            app.on_4d_generation()
    finally:
        oa.glob = real_glob
        app._save_frame = save
    return dict(frames=n, keyframes=len(keys), hmr_frames=len(keys) + len(retry), retried=len(retry))
