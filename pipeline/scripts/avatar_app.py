"""Replace the tracked people of a SAM-Body4D run with a VRM avatar.

Input: the output directory of scripts/offline_app_lite.py (or offline_app.py) made with skeleton export
(skeleton_4d_individual/), i.e. images/, masks/, mesh_4d_individual/, focal_4d_individual/, skeleton_4d_individual/.

  1. occlusion   R3_fusion v2 on the meshes (scripts/occlusion/run_fusion_v2.py; cached in <out>/occlusion)
  2. retarget    every target's MHR skeleton -> the VRM (scripts/avatar/retarget_mhr.py): rotation transfer with a
                 rest-pose correction, whole-height scale, placement at the hips-neck midpoint (a person close
                 to the camera: the head's scale and the neck's placement), 2-bone IK for wrists and soles,
                 VRM spring bones (hair, skirt)
  3. removal     the targets' real bodies erased by a temporal clean plate (scripts/avatar/removal.py)
  4. render      unlit VRM, supersampled RGBA + depth per target (scripts/avatar/raster.py)
  5. hide        fusion v2's decisions re-rendered over each avatar's own silhouette (scripts/avatar/occlusion.py)
  face          facial expressions (scripts/avatar/face.py: MediaPipe's landmark and blendshape models on crops placed
                 by the skeleton's head, CPU) in a thread beside 1 and 3, applied as VRM expressions in 4
  6. composite   clean plate, then the avatars back to front (z-buffer between avatars, at the supersampled
                 resolution so that intersecting avatars do not alias), each hidden where a real object is in front
                 of it; hidden pixels show the real frame (the occluder)
Steps 2, 4-6 stream over the frames in one pass: a hide mask is smoothed over frames t-1..t+1, so frame t is
composited once frame t+1 is rendered and its raw hide masks computed, which a worker thread does while frame t+2
renders (three frames of render buffers in memory, every frame skinned and rendered once).

usage (repo root):
  .venv/Scripts/python.exe scripts/avatar_app.py --pipeline_out <run>/pipeline_out --out <run>/avatar
      [--vrm assets/vrm_models/yuzuhako_Shuera.vrm] [--fps 30] [--no_occlusion] [--no_removal] [--no_spring]
      [--hq <input video or frames dir>] [--smooth 0.05] [--no_foot_lock] [--fusion_keyframe] [--no_face]
      [--targets 1,3] [--vrm_map 1=a.vrm,3=b.vrm] [--fusion_state <occ>/fusion_state.pkl] [--export_layers <dir>]
      [--overrides <overrides.json>]
--targets: only these people are erased and replaced; the others stay real, and where one of them is in front of an
avatar the avatar is hidden. --fusion_state: the decisions of scripts/occlusion/run_occlusion_stage.py instead of
running fusion v2 again. --export_layers: the editor's preview layers (scripts/avatar/layers_export.py).
writes <out>/frames/<stem>.jpg, <out>/avatar_<name>.mp4, <out>/compare_<name>.mp4 (input | mesh | avatar), stats.json
(--no_frames / --no_compare leave out the frames and the compare video: review aids, not product outputs)
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
from avatar.retarget_mhr import near_weights  # noqa: E402
from avatar.raster import Rasterizer, camera_mvp  # noqa: E402
from avatar.vrm import load_vrm  # noqa: E402
from utils.compile_cache import use_persistent_compile_cache  # noqa: E402

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


def source_size(src):
    """(H, W) of the original video / first frame of the frames folder."""
    if os.path.isfile(src):
        cap = cv2.VideoCapture(src)
        ok, img = cap.read()
        cap.release()
    else:
        img = cv2.imread(sorted(glob.glob(os.path.join(src, "*.jpg")))[0])
    return img.shape[:2]


def source_frames(src, stems, H, W):
    """-> (frame number, BGR) of the original video / frames folder for each stem (stems are frame numbers)."""
    want = {int(s): i for i, s in enumerate(stems)}
    if os.path.isfile(src):
        cap = cv2.VideoCapture(src)
        k = 0
        while want:
            ok, img = cap.read()
            if not ok:
                break
            if k in want:
                yield want.pop(k), _check(img, H, W, src)
            k += 1
        cap.release()
    else:   # decoded by worker threads, a bounded number ahead (a 5-minute 1080x1920 clip: 140 s -> ~20 s)
        from concurrent.futures import ThreadPoolExecutor
        files = sorted(glob.glob(os.path.join(src, "*.jpg")))
        todo = sorted(want.items())
        with ThreadPoolExecutor(8) as ex:
            futs = [ex.submit(cv2.imread, files[k]) for k, _ in todo[:32]]
            for j, (k, i) in enumerate(todo):
                img = futs[j].result()
                futs[j] = None
                if j + 32 < len(todo):
                    futs.append(ex.submit(cv2.imread, files[todo[j + 32][0]]))
                yield i, _check(img, H, W, files[k])
        want = {}
    if want:
        raise SystemExit(f"--source {src}: frames {sorted(want)[:5]}... not found")


def _check(img, H, W, src):
    if img.shape[:2] != (H, W):
        raise SystemExit(f"--hq {src}: frame size {img.shape[1]}x{img.shape[0]} != first frame {W}x{H}")
    return img


def composite_gpu(base, layers, s):
    """On the GPU, inside the union of the avatars' boxes only: the plate (nearest-upsampled to the supersampled
    grid), each avatar's alpha times (1 - hide, linearly upsampled), back to front by per-pixel depth (= composite),
    then the s x s area average and uint8 truncation (= cv2.resize INTER_AREA + astype). Outside the union the
    frame is the plate itself, as the full-frame path gives (alpha 0 there).
    layers: [(rgba uint8 box (h*s, w*s, 4), depth box, native box (x0, y0, x1, y1), hide (H, W) or None)]."""
    if not layers:
        return base
    import torch
    import torch.nn.functional as F
    dev = torch.device("cuda")
    ux0, uy0 = min(b[0] for _, _, b, _ in layers), min(b[1] for _, _, b, _ in layers)
    ux1, uy1 = max(b[2] for _, _, b, _ in layers), max(b[3] for _, _, b, _ in layers)
    hU, wU = (uy1 - uy0) * s, (ux1 - ux0) * s
    with torch.no_grad():
        out = torch.from_numpy(np.ascontiguousarray(base[uy0:uy1, ux0:ux1])).to(dev).float()
        out = out.repeat_interleave(s, 0).repeat_interleave(s, 1)
        cols, als, deps = [], [], []
        for im, d, (x0, y0, x1, y1), h in layers:
            oy, ox = (y0 - uy0) * s, (x0 - ux0) * s
            sl = (slice(oy, oy + im.shape[0]), slice(ox, ox + im.shape[1]))
            t = im if torch.is_tensor(im) else torch.from_numpy(im).to(dev)
            col = torch.zeros((hU, wU, 3), device=dev)
            col[sl] = t[..., [2, 1, 0]].float()
            al = torch.zeros((hU, wU), device=dev)
            al[sl] = t[..., 3].float() / 255.0
            dep = torch.full((hU, wU), float("inf"), device=dev)
            dep[sl] = d if torch.is_tensor(d) else torch.from_numpy(d).to(dev)
            if h is not None:   # = cv2 INTER_LINEAR of the whole mask to the supersampled size, then the crop
                hS = F.interpolate(torch.from_numpy(h).to(dev)[None, None], scale_factor=s, mode="bilinear",
                                   align_corners=False)[0, 0]
                al = al * (1.0 - hS[uy0 * s:uy1 * s, ux0 * s:ux1 * s])
            cols.append(col)
            als.append(al)
            deps.append(dep)
        # back to front: layer p's place in the farthest-first order of a stable argsort of -depth (the number of
        # layers farther, or as far and listed before it); an argsort along a 2-long axis took 37 of 44 ms per frame
        K = len(layers)
        rank = []
        for p in range(K):
            r = torch.zeros((hU, wU), dtype=torch.int64, device=dev)
            for q in range(K):
                if q != p:
                    r += (deps[q] > deps[p]) | ((deps[q] == deps[p]) & (q < p))
            rank.append(r)
        for k in range(K):
            col, al = cols[0], als[0]
            if K > 1:   # the layer at place k, per pixel (the other layers' blends were w = 0: out unchanged)
                col, al = torch.zeros_like(cols[0]), torch.zeros_like(als[0])
                for p in range(K):
                    m = rank[p] == k
                    col, al = torch.where(m[..., None], cols[p], col), torch.where(m, als[p], al)
            w = al[..., None]
            out = col * w + out * (1 - w)
        out = F.avg_pool2d(out.permute(2, 0, 1)[None], s)[0].permute(1, 2, 0)
        res = base.copy()
        res[uy0:uy1, ux0:ux1] = out.clamp(0, 255).to(torch.uint8).cpu().numpy()
    return res


class Sink:
    """Runs fn(*args) calls in order on one worker thread, at most `depth` waiting: the frames' PNG / JPEG writes and
    the two mp4 encodes (28 + 23 + 70 ms per 1080x1920 frame) leave the render loop and overlap with it. Same calls,
    same order, same files."""

    def __init__(self, depth=4):
        import queue
        import threading
        self.q = queue.Queue(depth)
        self.err = None
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        while True:
            job = self.q.get()
            if job is None:
                return
            if self.err is None:
                try:
                    job[0](*job[1:])
                except BaseException as e:   # re-raised by put / close on the main thread
                    self.err = e

    def put(self, fn, *args):
        if self.err is not None:
            raise self.err
        self.q.put((fn, *args))

    def close(self):
        self.q.put(None)
        self.t.join()
        if self.err is not None:
            raise self.err


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
    # --hq: the pipeline (tracking, meshes, masks, fusion's occlusion decisions) stays at its own resolution H x W;
    # the removal, the avatar render and the composite run at the original frames' OH x OW, the pipeline's masks
    # upscaled and its intrinsics scaled by OW / W
    OH, OW = source_size(a.source) if a.source else (H, W)
    sc = OW / W
    if abs(OH / H - sc) > 0.01:
        raise SystemExit(f"--hq {a.source}: {OW}x{OH} is not a rescale of the pipeline's {W}x{H}")
    stats["out_size"] = [OW, OH]
    sk = load_skeletons(run.pout, stems)
    pids = sorted({p for p, _ in sk})
    if not pids:
        raise SystemExit(f"no skeleton_4d_individual/ in {run.pout}: re-run the mesh pipeline (it now saves skeletons)")
    real_people = set()
    if a.targets:
        want = [int(x) for x in a.targets.split(",") if x.strip()]
        missing = [p for p in want if p not in pids]
        if missing:
            raise SystemExit(f"--targets {missing}: no skeleton for them (tracked: {pids})")
        real_people = set(pids) - set(want)
        pids = want
        sk = {k: v for k, v in sk.items() if k[0] in pids}
    vrm_of = {p: a.vrm for p in pids}
    for item in (a.vrm_map.split(",") if a.vrm_map else []):
        pid, _, path = item.partition("=")
        vrm_of[int(pid)] = path
    stats["vrm"] = {str(p): os.path.basename(v) for p, v in vrm_of.items()}
    # the face crops follow the unsmoothed head (the image's face), taken before smooth_skeletons changes cam_t
    heads = {k: z["keypoints_3d"][:5] + z["cam_t"] for k, z in sk.items() if "keypoints_3d" in z}
    if a.smooth > 0 or a.foot_lock:
        from avatar.smooth import smooth_skeletons
        sk, ss = smooth_skeletons(sk, pids, sigma_s=a.smooth, fps=a.fps, foot_lock=a.foot_lock)
        stats.update(ss)
        print(f"[avatar] smoothing sigma {a.smooth} s, foot lock {a.foot_lock}: {ss}", flush=True)
    focal = float(next(iter(sk.values()))["focal"])
    print(f"[avatar] {run.name}: {n} frames {W}x{H}, targets {pids}, focal {focal:.1f}", flush=True)

    # 3 (started first). erase the real targets: independent of the occlusion decisions, so it runs in a thread while
    # fusion v2 runs (same function, same inputs: same plates). --gpu_removal fills the plates on the GPU
    # (removal_gpu, byte-identical; opt-in: one 5-minute Modal run crashed with SIGSEGV in its prep, cause unknown)
    # the clip's frames and the removal arrays live in <out>/_scratch (memory-mapped .npy, deleted at the end): a
    # 5-minute clip's frames and plates alone take 3.5 GB each in RAM. Same values per frame.
    from disk_arrays import scratch as disk, release
    sdir = os.path.join(a.out, "_scratch")
    frames = disk(f"{sdir}/frames.npy", (n, OH, OW, 3), np.uint8)
    if a.source:   # --hq: the original frames (images/ went through SAM3's 1008x1008 resize and PIL's JPEG q75)
        for i, img in source_frames(a.source, stems, OH, OW):
            frames[i] = img
    else:
        for i in range(n):
            frames[i] = run.image(i)
    removal = {}
    if not a.no_removal:
        import threading
        extra = {}
        mode = getattr(a, "removal", "mosaic")
        if mode != "classic":   # removal_gpu work_short / static_bg / mosaic_sec / shadow + LaMa (its docstring)
            from avatar.removal_gpu import clean_plates_gpu as clean_plates
            from avatar.inpaint_lama import inpaint as lama
            extra = dict(work_short=480, inpaint=lama)
            extra.update({"v2": dict(static_bg=True), "mosaic": dict(mosaic_sec=0.5),
                          "mosaic_shadow": dict(mosaic_sec=0.5, shadow=True)}[mode])
            a.gpu_removal = True
        elif getattr(a, "gpu_removal", False):
            from avatar.removal_gpu import clean_plates_gpu as clean_plates
        else:
            from avatar.removal import clean_plates

        def _removal():
            t0 = time.perf_counter()
            pm = disk(f"{sdir}/person_masks.npy", (n, OH, OW), bool)
            for i in range(n):
                m = np.isin(run.mask(i), pids)
                pm[i] = m if sc == 1 else cv2.resize(m.astype(np.uint8), (OW, OH), interpolation=cv2.INTER_NEAREST) > 0
            try:   # the dilations are in pipeline pixels: scaled with the masks
                workers = getattr(a, "removal_workers", None) or (os.cpu_count() if getattr(a, "gpu_removal", False) else 4)
                removal["out"] = clean_plates(frames, pm, fps=a.fps, workers=workers,
                                              scratch=sdir, dilate=max(1, round(9 * sc)),
                                              exclude_dilate=max(1, round(15 * sc)), **extra)
            except BaseException as e:   # re-raised below on the main thread
                removal["err"] = e
            removal["s"] = time.perf_counter() - t0
        removal["thread"] = threading.Thread(target=_removal, daemon=True)
        removal["thread"].start()

    # facial expressions: CPU, ~4 ms per face, beside the removal and fusion v2 (face.py)
    face = None
    if getattr(a, "face", True) and heads:
        from avatar.face import FaceThread
        face = FaceThread(frames, heads, focal * sc, OW / 2, OH / 2, pids, n, sigma=a.face_sigma).start()

    # 1. occlusion decisions (fusion v2 on the meshes)
    fus = None
    if not a.no_occlusion and a.fusion_state:
        import pickle
        t0 = time.perf_counter()
        with open(a.fusion_state, "rb") as fh:
            st = pickle.load(fh)
        cache = os.path.join(os.path.dirname(os.path.abspath(a.fusion_state)), "_r3cache", st["name"])
        import shared_cache
        shared_cache.install()
        fus = dict(run=run, ev=st["ev"], dec=st["dec"], cache=cache)
        stats["fusion_s"] = round(time.perf_counter() - t0, 1)
        print(f"[avatar] fusion v2 decisions from {a.fusion_state}", flush=True)
    elif not a.no_occlusion:
        from run_fusion_v2 import run_fusion
        t0 = time.perf_counter()
        fus = run_fusion(run.pout, os.path.join(a.out, "occlusion"), run.name, save=False,
                         reference=getattr(a, "fusion_reference", False),
                         points=getattr(a, "fusion_points", "kf_lite"),
                         compile_decoder=getattr(a, "fusion_compile_decoder", True),
                         keyframe=getattr(a, "fusion_keyframe", False),
                         render=False)   # fusion.render's own hide masks: the avatar draws its own (AvatarHider)
        fus.pop("hide", None)
        stats["fusion_s"] = round(time.perf_counter() - t0, 1)

    # 2. avatars (retarget + render are done per frame below)
    t0 = time.perf_counter()
    models = {v: load_vrm(v) for v in sorted(set(vrm_of.values()))}
    rz = Rasterizer(OW, OH, ssaa=a.ssaa)
    K = np.array([[focal * sc, 0, OW / 2], [0, focal * sc, OH / 2], [0, 0, 1]])
    mvp = camera_mvp(K, OW, OH)
    actors, near = {}, {}
    for pid in pids:
        coords = [sk[(pid, i)]["joint_coords"] for i in range(n) if (pid, i) in sk]
        act = AvatarActor(models[vrm_of[pid]], rz, scale=1.0, fps=a.fps, spring=not a.no_spring)
        act.scale = act.rt.leg_scale(coords)
        fi = [i for i in range(n) if (pid, i) in sk]

        def crown_frames(idx):   # (rotations, joints, mesh) of these frames for crown_height; the unsmoothed skeleton
            out = []
            for i in idx:
                z = np.load(f"{run.pout}/skeleton_4d_individual/{pid}/{stems[i]}.npz")
                m = run.mesh(pid, i)
                if m is not None:
                    out.append((z["joint_rots"], z["joint_coords"] + z["cam_t"], m[0]))
            return out
        if getattr(a, "fit", "height") == "height":   # sole -> crown instead of the legs (retarget_mhr.height_scale)
            crown = act.rt.crown_height(crown_frames([fi[k] for k in np.linspace(0, len(fi) - 1, 8).astype(int)]))
            if crown is not None:
                act.scale = act.rt.height_scale(coords, crown)
        actors[pid] = act
        stats[f"scale_{pid}"] = round(act.scale, 4)
        print(f"[avatar] target {pid}: avatar scale {act.scale:.3f} (avatar height "
              f"{act.scale * act.rt.height_m:.2f} m), {len(coords)} frames", flush=True)
        # a person close to the camera: placement at the neck and the head's scale (retarget_mhr near_weights)
        w = near_weights([sk[(pid, i)]["joint_coords"][113, 2] + sk[(pid, i)]["cam_t"][2] for i in fi], a.fps)
        hs = None
        if w.max() > 0:
            hs = act.rt.head_scale(crown_frames([fi[k] for k in np.argsort(-w, kind="stable")[:8]]))
        if hs is None:
            w = np.zeros(len(fi))
        near[pid] = (act.scale, hs, {i: float(x) for i, x in zip(fi, w) if x > 0})
        if hs is not None:
            stats[f"scale_head_{pid}"] = round(hs, 4)
            stats[f"near_frames_{pid}"] = int((w > 0).sum())
            print(f"[avatar] target {pid}: close to the camera in {int((w > 0).sum())} frames (head scale {hs:.3f})",
                  flush=True)
    t_setup = time.perf_counter() - t0

    # 3. erase the real targets (started before fusion)
    if a.no_removal:
        plates = frames
    else:
        t0 = time.perf_counter()
        removal["thread"].join()
        if "err" in removal:
            raise removal["err"]
        plates, rs = removal["out"][0], removal["out"][2]   # (the hole masks are not used)
        stats.update({f"removal_{k}": v for k, v in rs.items()})
        stats["removal_s"] = round(removal["s"], 1)                    # its own run time (overlapped with fusion)
        stats["removal_wait_s"] = round(time.perf_counter() - t0, 1)   # what it still added after fusion

    expr, fmap = {}, {}          # fmap: per target (each may have its own VRM and expression names)
    if face is not None:
        from avatar.face import vrm_mapping
        t0 = time.perf_counter()
        try:
            fr = face.result()
        except (ImportError, OSError, ValueError) as e:   # no ai-edge-litert / checkpoint: neutral faces
            print(f"[avatar] WARNING: no facial expressions ({type(e).__name__}: {e}); "
                  f"pip install ai-edge-litert, scripts/setup.py downloads mediapipe/face_landmarker.task", flush=True)
            stats["face_error"] = f"{type(e).__name__}: {e}"
            fr = None
        if fr is not None:
            expr = fr["expr"]
            fmap = {pid: vrm_mapping(models[vrm_of[pid]].blendshapes) for pid in pids}
            stats["face_mapping"] = {str(pid): m[1] for pid, m in fmap.items()}
            fmap = {pid: m[0] for pid, m in fmap.items()}
            stats["face_s"] = round(fr["s"], 1)                            # its own run time (overlapped)
            stats["face_wait_s"] = round(time.perf_counter() - t0, 1)      # what it still added
            stats["face_read"] = len(fr["raw"])
            stats["face_confident"] = int(sum(r[1] > 0.5 for r in fr["raw"].values()))

    hider = hide_pool = None
    if fus is not None:
        from concurrent.futures import ThreadPoolExecutor
        from avatar.occlusion import AvatarHider
        overrides = None
        if a.overrides:
            from avatar.occlusion import Overrides
            overrides = Overrides.load(a.overrides)
        hider = AvatarHider(fus, real=real_people, overrides=overrides)
        hider.prefetch(range(n))
        hide_pool = ThreadPoolExecutor(1)   # raw hide masks of frame i beside the rendering of frame i + 1
    ik_err = {}
    head_px = {}   # avatar_heads.json: each avatar's head bone in output pixels (eval/face_video.py)

    ss = rz.ssaa
    dev = None
    if not a.cpu_avatar:
        import torch
        import torch.nn.functional as F
        dev = torch.device("cuda")
        if abs(sc - round(sc)) > 1e-6:
            raise SystemExit(f"GPU avatar path needs an integer --hq scale (got {sc:.3f}): use --cpu_avatar")

    def render_frame(i):
        """-> {pid: (rgba ssaa, depth ssaa, native box, alpha native)}, a future of frame i's raw hide masks (or None).
        The supersampled buffers cover only the avatar's box (Actor.screen_box: its projected vertices), which is
        all that is read back from the GPU; outside it the avatar has no pixel."""
        buf, al, dp = {}, {}, {}
        for pid in pids:
            z = sk.get((pid, i))
            act = actors[pid]
            if z is None:
                act.reset()             # not tracked here: restart the spring simulation when the person returns
                continue
            e = expr.get((pid, i))
            s_leg, s_head, nw = near[pid]
            wn = nw.get(i, 0.0)
            act.scale = (1 - wn) * s_leg + wn * s_head if wn > 0 else s_leg
            act.pose(z["joint_rots"], z["joint_coords"] + z["cam_t"], arm_ik=not a.no_ik, leg_ik=not a.no_ik,
                     gpu=dev, expr=fmap[pid](e) if e is not None and pid in fmap else None, near=wn,
                     anchor=getattr(a, "anchor", 0.5))
            for kk, v in act.ik_err.items():
                ik_err.setdefault(kk.split("_")[1], []).append(v)
            hb = act.bone_cam("head")
            if hb is not None and hb[2] > 0:
                head_px.setdefault(str(pid), {})[stems[i]] = [round(float(K[0, 0] * hb[0] / hb[2] + K[0, 2]), 1),
                                                            round(float(K[1, 1] * hb[1] / hb[2] + K[1, 2]), 1)]
            box = act.screen_box(K)
            if box is None:
                continue
            x0, y0, x1, y1 = box
            sbox = (x0 * ss, y0 * ss, x1 * ss, y1 * ss)
            if dev is None:
                im, d = rz.render_box(act.meshes, mvp, sbox)
                a_n = np.zeros((OH, OW), np.float32)
                d_n = np.full((OH, OW), np.inf, np.float32)
                # = rz.downsample of the full buffers: block area average / min, and the box is on the ssaa grid
                a_n[y0:y1, x0:x1] = cv2.resize(im[..., 3], (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA) / 255.0
                d_n[y0:y1, x0:x1] = d.reshape(y1 - y0, ss, x1 - x0, ss).min(axis=(1, 3))
                buf[pid] = (im, d, box, a_n[y0:y1, x0:x1])
                if sc == 1:
                    al[pid], dp[pid] = a_n, d_n
                else:   # fusion's decisions are made at the pipeline's resolution
                    al[pid] = cv2.resize(a_n, (W, H), interpolation=cv2.INTER_AREA)
                    dp[pid] = cv2.resize(d_n, (W, H), interpolation=cv2.INTER_NEAREST)
                continue
            # GPU: the same reductions on the box, the full frames only at the pipeline's resolution for fusion
            im, d = rz.render_box_gpu(act.meshes, mvp, sbox, dev)
            hb, wb = y1 - y0, x1 - x0
            a_box = (F.avg_pool2d(im[..., 3].float()[None, None], ss)[0, 0] / 255.0)
            d_box = -F.max_pool2d(-d[None, None], ss)[0, 0]
            a_full = torch.zeros((OH, OW), device=dev)
            d_full = torch.full((OH, OW), float("inf"), device=dev)
            a_full[y0:y1, x0:x1] = a_box
            d_full[y0:y1, x0:x1] = d_box
            if sc != 1:   # = cv2 INTER_AREA (block mean) / INTER_NEAREST (top-left sample) at an integer factor
                k = int(round(sc))
                a_full = F.avg_pool2d(a_full[None, None], k)[0, 0]
                d_full = d_full[::k, ::k]
            al[pid], dp[pid] = a_full.cpu().numpy(), d_full.contiguous().cpu().numpy()
            buf[pid] = (im, d, box, a_box.cpu().numpy())
        return buf, (hide_pool.submit(hider.raw, i, al, dp) if hider is not None else None)

    # 4-6. render, hide, composite: frame j is composited once frame j+1 has been rendered and its raw hide masks
    # computed (on hide_pool, while frame j+2 renders)
    fdir = os.path.join(a.out, "frames")
    write_frames, write_compare = not getattr(a, "no_frames", False), not getattr(a, "no_compare", False)
    if write_frames:
        os.makedirs(fdir, exist_ok=True)
    SW, SH = OW * rz.ssaa, OH * rz.ssaa
    # both videos are encoded as the frames come (utils.Mp4Writer: images_to_mp4's per-frame steps), not collected
    from utils import Mp4Writer
    crf = a.crf if a.crf is not None else (12 if a.source else None)
    outs = Mp4Writer(os.path.join(a.out, f"avatar_{run.name}.mp4"), fps=a.fps, crf=crf)
    comps = Mp4Writer(os.path.join(a.out, f"compare_{run.name}.mp4"), fps=a.fps, crf=crf) if write_compare else None
    sinks = [Sink() if on else None for on in (write_frames, True, write_compare)]   # frame files, avatar / compare mp4
    layer_out = None
    if a.export_layers:
        if sc != 1:
            raise SystemExit("--export_layers is written at the pipeline's resolution: not with --hq")
        from avatar.layers_export import LayerWriter
        sinks.append(Sink())
        ids = {int(p): v for p, _, v in (s.partition("=") for s in a.avatar_ids.split(","))} if a.avatar_ids else {}
        layer_out = LayerWriter(a.export_layers, (OW, OH), pids, ids, sinks[-1])

    def compare_row(j, out):
        mesh = cv2.imread(f"{run.pout}/rendered_frames/{stems[j]}.jpg")
        if mesh is not None and sc != 1 and mesh.shape[:2] == (H, W):
            mesh = cv2.resize(mesh, (OW, OH), interpolation=cv2.INTER_LINEAR)
        row = [frames[j]] + ([mesh] if mesh is not None and mesh.shape == out.shape else []) + [out]
        comps.append(cv2.cvtColor(np.hstack(row), cv2.COLOR_BGR2RGB))
    hidden_px = cov_px = 0
    t_render = t_hide = t_comp = t_gpu = 0.0
    bufs, raws, hides_j = {}, {}, {}
    raw = lambda k: raws[k].result() if raws.get(k) is not None else None
    for i in range(n + 2):
        if i < n:
            t0 = time.perf_counter()
            bufs[i], raws[i] = render_frame(i)
            t_render += time.perf_counter() - t0
        j = i - 2
        if j < 0:
            continue
        t0 = time.perf_counter()
        base = plates[j].copy()
        layers = []
        hide_any = np.zeros((OH, OW), bool)
        for pid, (im, d, box, al_n) in bufs[j].items():   # al_n: native alpha of the box
            bx0, by0, bx1, by1 = box
            cov_px += int((al_n > 0.02).sum())
            h = None
            if hider is not None:
                h = hider.smooth(pid, raw(j - 1), raw(j), raw(j + 1))
                h = np.clip(h, 0, 1).astype(np.float32)
                if sc != 1:   # pipeline resolution -> output (the linear upscale also softens the hide edge)
                    h = cv2.resize(h, (OW, OH), interpolation=cv2.INTER_LINEAR)
                hidden_px += int((h >= 0.5).sum())
                hide_any[by0:by1, bx0:bx1] |= (h[by0:by1, bx0:bx1] >= 0.5) & (al_n > 0.02)
            layers.append((im, d, box, h))
            if layer_out is not None:
                hides_j[pid] = h
        if layer_out is not None:
            layer_out.frame(j, plates[j], bufs[j], hides_j, ss)
            hides_j = {}
        t_hide += time.perf_counter() - t0
        t0 = time.perf_counter()
        base[hide_any] = frames[j][hide_any]          # what hides the avatar is the real occluder
        out = composite_gpu(base, layers, ss)
        t_gpu += time.perf_counter() - t0
        if not write_frames:
            pass
        elif a.source:
            sinks[0].put(cv2.imwrite, os.path.join(fdir, f"{stems[j]}.png"), out)
        else:
            sinks[0].put(cv2.imwrite, os.path.join(fdir, f"{stems[j]}.jpg"), out, [cv2.IMWRITE_JPEG_QUALITY, 95])
        sinks[1].put(lambda o: outs.append(cv2.cvtColor(o, cv2.COLOR_BGR2RGB)), out)
        if write_compare:
            sinks[2].put(compare_row, j, out)
        bufs.pop(j)
        raws.pop(j - 1, None)
        t_comp += time.perf_counter() - t0
        print(f"[avatar] frame {j + 1}/{n}", flush=True)
    if hide_pool is not None:
        hide_pool.shutdown()
    stats["render_s"] = round(t_setup + t_render, 1)
    stats["hide_s"] = round(t_hide, 1)
    t0 = time.perf_counter()
    for k in sinks:   # the writes / encodes still queued
        if k is not None:
            k.close()
    if layer_out is not None:
        layer_out.close()
    stats["write_wait_s"] = round(time.perf_counter() - t0, 1)
    stats["composite_s"] = round(t_comp, 1)          # incl. the GPU composite; the writes and encodes run in Sinks
    stats["composite_gpu_s"] = round(t_gpu, 1)
    if hider is not None:
        stats["hidden_avatar_%"] = round(100 * hidden_px / max(cov_px, 1), 2)
    for kind, v in ik_err.items():
        stats[f"ik_{kind}_error_before_cm_median"] = round(float(np.median(v)) * 100, 1)
    print(f"[avatar] retarget+render {stats['render_s']} s ({stats['render_s'] / max(n, 1):.2f} s/frame), hide "
          f"{stats['hide_s']} s, composite {stats['composite_s']} s; "
          + ", ".join(f"{k.split('_')[1]} IK correction (median) {v} cm" for k, v in stats.items()
                      if k.startswith("ik_")), flush=True)
    outs.close()
    if comps is not None:
        comps.close()
    del frames, plates, removal, base, hider, fus, face   # every view of the scratch arrays, so the files can go
    for f in ("frames", "person_masks", "removal_holes", "removal_excl", "removal_grays", "removal_plates"):
        release(f"{sdir}/{f}.npy")
    try:
        os.rmdir(sdir)
    except OSError:
        pass
    stats["total_s"] = round(time.perf_counter() - t_all, 1)
    json.dump(stats, open(os.path.join(a.out, "stats.json"), "w"), indent=1)
    json.dump(head_px, open(os.path.join(a.out, "avatar_heads.json"), "w"))
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
    ap.add_argument("--hq", metavar="SOURCE", dest="source", default=None,
                    help="high-quality output: composite over the original frames of SOURCE (the input video or "
                         "frames folder given to the pipeline) instead of images/, PNG frames, mp4 at --crf 12")
    ap.add_argument("--crf", type=int, default=None, help="x264 CRF of the mp4s (default: imageio's quality 5; 12 with --hq)")
    ap.add_argument("--smooth", type=float, default=0.05,
                    help="Gaussian sigma [s] of the temporal skeleton smoothing (scripts/avatar/smooth.py); 0 = off")
    ap.add_argument("--no_foot_lock", dest="foot_lock", action="store_false",
                    help="do not hold still, lower ankles in place (contact lock)")
    ap.add_argument("--removal_workers", type=int, default=None,
                    help="CPU workers for independent clean-plate frames (1 for sequential processing; default 4); "
                         "with --gpu_removal, the threads that compute its homographies (default: all logical CPUs)")
    ap.add_argument("--removal", choices=["classic", "v2", "mosaic", "mosaic_shadow"], default="mosaic",
                    help="mosaic (default) / v2 (judged better than classic by eye, reports/compare/2026-10-24/removal_rework*): the GPU removal with homographies on a 480 px grid, a window background for a static "
                         "camera and LaMa instead of Telea for the pixels no frame shows (scripts/avatar/removal_gpu.py, "
                         "inpaint_lama.py; implies --gpu_removal). mosaic: one background per 0.5 s window warped into "
                         "each frame; mosaic_shadow: also the targets' cast shadows (removal_gpu docstring)")
    ap.add_argument("--gpu_removal", action="store_true",
                    help="fill the clean plates on the GPU (scripts/avatar/removal_gpu.py: the same plates, ~5x faster "
                         "at 1080x1920; shares the GPU with fusion v2)")
    ap.add_argument("--no_occlusion", action="store_true")
    ap.add_argument("--targets", default=None, help="comma-separated pids to replace (default: every tracked person)")
    ap.add_argument("--vrm_map", default=None, help="pid=path.vrm,... per target (default: --vrm for all)")
    ap.add_argument("--avatar_ids", default=None, help="pid=avatarId,... recorded in layers.json")
    ap.add_argument("--fusion_state", default=None,
                    help="fusion_state.pkl of scripts/occlusion/run_occlusion_stage.py: reuse its decisions")
    ap.add_argument("--export_layers", default=None, help="write the editor's preview layers to this directory")
    ap.add_argument("--overrides", default=None,
                    help="manual occlusion corrections expanded to fusion tracks (avatar/occlusion.py: Overrides)")
    ap.add_argument("--no_face", dest="face", action="store_false",
                    help="neutral avatar faces (no facial expressions from scripts/avatar/face.py)")
    ap.add_argument("--face_sigma", type=float, default=1.0,
                    help="Gaussian sigma [frames] of the expression smoothing (face.py)")
    ap.add_argument("--cpu_avatar", action="store_true",
                    help="skin, read back and reduce the avatars on the CPU (the reference path of the GPU one)")
    ap.add_argument("--no_removal", action="store_true")
    ap.add_argument("--fit", choices=["legs", "height"], default="height",
                    help="avatar scale from the leg length (hips and soles land on the person's) or from the whole height "
                         "(sole .. crown; retarget_mhr.height_scale)")
    ap.add_argument("--anchor", type=float, default=0.5,
                    help="placement point: 0 = the hips on the person's hips .. 1 = the neck on the person's neck")
    ap.add_argument("--no_compare", action="store_true",
                    help="do not write compare_<name>.mp4 (input | mesh | avatar; a review aid, ~2 s of encoding)")
    ap.add_argument("--no_frames", action="store_true",
                    help="do not write <out>/frames/ (the avatar mp4 only)")
    ap.add_argument("--no_spring", action="store_true")
    ap.add_argument("--no_ik", action="store_true")
    ap.add_argument("--fusion_reference", action="store_true",
                    help="run fusion v2's frozen gpu_stage / evidence instead of the bit-identical fast ones")
    ap.add_argument("--fusion_points", choices=["full", "c2f", "kf_flow", "kf_lite", "kf_sam3"], default="kf_lite",
                    help="fusion v2 SAM3 point prompts: kf_lite (default) = coarse-to-fine on keyframes, no SAM3 call "
                         "in between (candidates and the 'person' refinement carried by optical flow); kf_flow = the "
                         "same with set_image and grounding on every frame; c2f = coarse-to-fine on every frame; full = "
                         "the frozen grid; kf_sam3 = keyframes carried by the SAM3 tracker (slower)")
    ap.add_argument("--no_fusion_compile_decoder", dest="fusion_compile_decoder", action="store_false",
                    help="eager SAM mask decoder in fusion v2. The default torch.compiles it: SAM3 -29%% on penn1765 with "
                         "a warm inductor cache, not bit-identical (dev 40: worst F1 -0.009, pass 35 -> 34; adopted "
                         "as an exception on 2026-10-15, LEDGER)")
    ap.add_argument("--fusion_keyframe", action="store_true",
                    help="fusion v2 occluder candidates from keyframes tracked by EdgeTAM (SAM3 prompts and person "
                         "grounding on keyframes only; scripts/occlusion/fusion_v2/keyframe_stage.py; opt-in, not adopted on the "
                         "held-out test7: misses objects held inside a person's visible mask)")
    use_persistent_compile_cache()
    main(ap.parse_args())
