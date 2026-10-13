"""Replace the tracked people of a SAM-Body4D run with a VRM avatar.

Input: the output directory of scripts/offline_app_lite.py (or offline_app.py) made with skeleton export
(skeleton_4d_individual/), i.e. images/, masks/, mesh_4d_individual/, focal_4d_individual/, skeleton_4d_individual/.

  1. occlusion   R3_fusion v2 on the meshes (scripts/occlusion/run_fusion_v2.py; cached in <out>/occlusion)
  2. retarget    every target's MHR skeleton -> the VRM (scripts/avatar/retarget_mhr.py): rotation transfer with a
                 rest-pose correction, leg-length scale, hips placement, 2-bone IK for wrists and soles,
                 VRM spring bones (hair, skirt)
  3. removal     the targets' real bodies erased by a temporal clean plate (scripts/avatar/removal.py)
  4. render      unlit VRM, supersampled RGBA + depth per target (scripts/avatar/raster.py)
  5. hide        fusion v2's decisions re-rendered over each avatar's own silhouette (scripts/avatar/occlusion.py)
  6. composite   clean plate, then the avatars back to front (z-buffer between avatars, at the supersampled
                 resolution so that intersecting avatars do not alias), each hidden where a real object is in front
                 of it; hidden pixels show the real frame (the occluder)
Steps 2, 4-6 stream over the frames in one pass: a hide mask is smoothed over frames t-1..t+1, so frame t is
composited once frame t+1 is rendered (two frames of render buffers in memory, every frame skinned and rendered once).

usage (repo root):
  .venv/Scripts/python.exe scripts/avatar_app.py --pipeline_out <run>/pipeline_out --out <run>/avatar
      [--vrm assets/vrm_models/yuzuhako_Shuera.vrm] [--fps 30] [--no_occlusion] [--no_removal] [--no_spring]
writes <out>/frames/<stem>.jpg, <out>/avatar_<name>.mp4, <out>/compare_<name>.mp4 (input | mesh | avatar), stats.json
"""
import argparse
import glob
import json
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(HERE, "occlusion"), os.path.join(HERE, "occlusion", "fusion_v2"), ROOT]

from avatar.actor import AvatarActor  # noqa: E402
from avatar.raster import Rasterizer, camera_mvp  # noqa: E402
from avatar.vrm import load_vrm  # noqa: E402

DEFAULT_VRM = os.path.join(ROOT, "assets", "vrm_models", "yuzuhako_Shuera.vrm")


def load_skeletons(pout, stems):
    sk = {}
    for d in sorted(glob.glob(f"{pout}/skeleton_4d_individual/*")):
        pid = int(os.path.basename(d))
        for i, s in enumerate(stems):
            p = f"{d}/{s}.npz"
            if os.path.exists(p):
                z = dict(np.load(p))
                if np.all(np.isfinite(z["joint_rots"])) and np.all(np.isfinite(z["joint_coords"])):
                    sk[(pid, i)] = z
    return sk


def composite(base, layers):
    """layers: [(bgr float32, alpha (H,W) 0..1, depth (H,W) inf=empty)] -> back-to-front over base.
    Only the bounding box of the layers' coverage is computed: outside it every alpha is 0, where the blend
    col * 0 + out * 1 returns out unchanged."""
    out = base.astype(np.float32)
    if not layers:
        return out
    cov = np.zeros(out.shape[:2], bool)
    for _, a, _ in layers:
        cov |= a > 0
    rows, cols = np.nonzero(cov.any(1))[0], np.nonzero(cov.any(0))[0]
    if len(rows) == 0:
        return out
    sl = (slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1))
    out[sl] = _composite(out[sl], [(c[sl], a[sl], d[sl]) for c, a, d in layers])
    return out


def _composite(out, layers):
    D = np.stack([d for _, _, d in layers])
    order = np.argsort(-D, axis=0)                     # farthest first
    for k in range(len(layers)):
        idx = order[k]
        for p, (col, a, _) in enumerate(layers):
            w = (a * (idx == p))[..., None]
            out = col * w + out * (1 - w)
    return out


def main(a):
    t_all = time.perf_counter()
    from r3common import Run
    run = Run(a.pipeline_out, a.name)
    stems, H, W, n = run.stems, run.H, run.W, len(run)
    os.makedirs(a.out, exist_ok=True)
    stats = {"run": run.name, "frames": n, "size": [W, H], "vrm": os.path.basename(a.vrm)}
    sk = load_skeletons(run.pout, stems)
    pids = sorted({p for p, _ in sk})
    if not pids:
        raise SystemExit(f"no skeleton_4d_individual/ in {run.pout}: re-run the mesh pipeline (it now saves skeletons)")
    focal = float(next(iter(sk.values()))["focal"])
    print(f"[avatar] {run.name}: {n} frames {W}x{H}, targets {pids}, focal {focal:.1f}", flush=True)

    # 1. occlusion decisions (fusion v2 on the meshes)
    fus = None
    if not a.no_occlusion:
        from run_fusion_v2 import run_fusion
        t0 = time.perf_counter()
        fus = run_fusion(run.pout, os.path.join(a.out, "occlusion"), run.name, save=False,
                         reference=getattr(a, "fusion_reference", False))
        stats["fusion_s"] = round(time.perf_counter() - t0, 1)

    # 2. avatars (retarget + render are done per frame below)
    t0 = time.perf_counter()
    model = load_vrm(a.vrm)
    rz = Rasterizer(W, H, ssaa=a.ssaa)
    K = np.array([[focal, 0, W / 2], [0, focal, H / 2], [0, 0, 1]])
    mvp = camera_mvp(K, W, H)
    actors = {}
    for pid in pids:
        coords = [sk[(pid, i)]["joint_coords"] for i in range(n) if (pid, i) in sk]
        act = AvatarActor(model, rz, scale=1.0, fps=a.fps, spring=not a.no_spring)
        act.scale = act.rt.leg_scale(coords)
        actors[pid] = act
        stats[f"scale_{pid}"] = round(act.scale, 4)
        print(f"[avatar] target {pid}: avatar scale {act.scale:.3f} (avatar height "
              f"{act.scale * act.rt.height_m:.2f} m), {len(coords)} frames", flush=True)
    t_setup = time.perf_counter() - t0

    # 3. erase the real targets
    frames = [run.image(i) for i in range(n)]
    if a.no_removal:
        plates = frames
    else:
        from avatar.removal import clean_plates
        t0 = time.perf_counter()
        pm = [np.isin(run.mask(i), pids) for i in range(n)]
        plates, _, rs = clean_plates(frames, pm, fps=a.fps)
        stats.update({f"removal_{k}": v for k, v in rs.items()})
        stats["removal_s"] = round(time.perf_counter() - t0, 1)

    hider = None
    if fus is not None:
        from avatar.occlusion import AvatarHider
        hider = AvatarHider(fus)
    ik_err = {}

    def render_frame(i):
        """-> {pid: (rgba ssaa, depth ssaa, alpha native)}, raw hide masks of frame i (or None)."""
        buf, al, dp = {}, {}, {}
        for pid in pids:
            z = sk.get((pid, i))
            act = actors[pid]
            if z is None:
                act.reset()             # not tracked here: restart the spring simulation when the person returns
                continue
            act.pose(z["joint_rots"], z["joint_coords"] + z["cam_t"], arm_ik=not a.no_ik, leg_ik=not a.no_ik)
            for kk, v in act.ik_err.items():
                ik_err.setdefault(kk.split("_")[1], []).append(v)
            im, d = act.render(mvp, native=False)
            im_n, d_n = rz.downsample(im, d)
            al[pid] = im_n[..., 3].astype(np.float32) / 255.0
            dp[pid] = d_n
            buf[pid] = (im, d, al[pid])
        return buf, (hider.raw(i, al, dp) if hider is not None else None)

    # 4-6. render, hide, composite: frame j is composited once frame j+1 has been rendered
    fdir = os.path.join(a.out, "frames")
    os.makedirs(fdir, exist_ok=True)
    SW, SH = W * rz.ssaa, H * rz.ssaa
    outs, comps = [], []
    hidden_px = cov_px = 0
    t_render = t_hide = t_comp = 0.0
    bufs, raws = {}, {}
    for i in range(n + 1):
        if i < n:
            t0 = time.perf_counter()
            bufs[i], raws[i] = render_frame(i)
            t_render += time.perf_counter() - t0
        j = i - 1
        if j < 0:
            continue
        t0 = time.perf_counter()
        base = plates[j].copy()
        layers = []
        hide_any = np.zeros((H, W), bool)
        for pid, (im, d, al_n) in bufs[j].items():
            al = im[..., 3].astype(np.float32) / 255.0
            cov_px += int((al_n > 0.02).sum())
            if hider is not None:
                h = hider.smooth(pid, raws.get(j - 1), raws[j], raws.get(j + 1))
                hidden_px += int((h >= 0.5).sum())
                al = al * (1.0 - cv2.resize(np.clip(h, 0, 1).astype(np.float32), (SW, SH),
                                            interpolation=cv2.INTER_LINEAR))
                hide_any |= (h >= 0.5) & (al_n > 0.02)
            layers.append((im[..., [2, 1, 0]].astype(np.float32), al, d))
        t_hide += time.perf_counter() - t0
        t0 = time.perf_counter()
        base[hide_any] = frames[j][hide_any]          # what hides the avatar is the real occluder
        big = composite(cv2.resize(base, (SW, SH), interpolation=cv2.INTER_NEAREST), layers)
        out = np.clip(cv2.resize(big, (W, H), interpolation=cv2.INTER_AREA), 0, 255).astype(np.uint8)
        cv2.imwrite(os.path.join(fdir, f"{stems[j]}.jpg"), out, [cv2.IMWRITE_JPEG_QUALITY, 95])
        outs.append(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
        mesh = cv2.imread(f"{run.pout}/rendered_frames/{stems[j]}.jpg")
        row = [frames[j]] + ([mesh] if mesh is not None and mesh.shape == out.shape else []) + [out]
        comps.append(cv2.cvtColor(np.hstack(row), cv2.COLOR_BGR2RGB))
        bufs.pop(j)
        raws.pop(j - 1, None)
        t_comp += time.perf_counter() - t0
    stats["render_s"] = round(t_setup + t_render, 1)
    stats["hide_s"] = round(t_hide, 1)
    stats["composite_s"] = round(t_comp, 1)
    if hider is not None:
        stats["hidden_avatar_%"] = round(100 * hidden_px / max(cov_px, 1), 2)
    for kind, v in ik_err.items():
        stats[f"ik_{kind}_error_before_cm_median"] = round(float(np.median(v)) * 100, 1)
    print(f"[avatar] retarget+render {stats['render_s']} s ({stats['render_s'] / max(n, 1):.2f} s/frame), hide "
          f"{stats['hide_s']} s, composite {stats['composite_s']} s; "
          + ", ".join(f"{k.split('_')[1]} IK correction (median) {v} cm" for k, v in stats.items()
                      if k.startswith("ik_")), flush=True)
    from utils import images_to_mp4
    images_to_mp4(outs, os.path.join(a.out, f"avatar_{run.name}.mp4"), fps=a.fps)
    images_to_mp4(comps, os.path.join(a.out, f"compare_{run.name}.mp4"), fps=a.fps)
    stats["total_s"] = round(time.perf_counter() - t_all, 1)
    json.dump(stats, open(os.path.join(a.out, "stats.json"), "w"), indent=1)
    print(f"[avatar] done in {stats['total_s']} s -> {a.out}", flush=True)
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Replace SAM-Body4D targets with a VRM avatar (see module docstring)")
    ap.add_argument("--pipeline_out", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--vrm", default=DEFAULT_VRM)
    ap.add_argument("--name", default=None, help="run name (default: the parent folder of pipeline_out)")
    ap.add_argument("--fps", type=float, default=30.0, help="video frame rate (spring-bone time step, output mp4)")
    ap.add_argument("--ssaa", type=int, default=2)
    ap.add_argument("--no_occlusion", action="store_true")
    ap.add_argument("--no_removal", action="store_true")
    ap.add_argument("--no_spring", action="store_true")
    ap.add_argument("--no_ik", action="store_true")
    ap.add_argument("--fusion_reference", action="store_true",
                    help="run fusion v2's frozen gpu_stage / evidence instead of the bit-identical fast ones")
    main(ap.parse_args())
