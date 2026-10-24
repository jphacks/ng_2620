"""Keyframe-tracked occluder candidates for fusion v2 (prototype, 2026-10-23).

gpu_stage_fast prompts SAM3 with ~180 points on every frame and links the per-frame segments afterwards (dev: median
track length 1 frame). Here SAM3's point prompts run only on keyframes, and only the occluders chosen there are
followed by a video tracker (EdgeTAM or the SAM3 tracker) until they stop touching the targets:

  per frame t (in order)
    1. the tracker's vision backbone runs once on frame t; every tracked object's state reads these features
    2. occlusion signal S_p(t) of every target p (CPU, from data the pipeline already has):
         - mesh pixels nobody's visible mask covers, beyond the misfit band of p's visible mask or with p's visible
           mask on both sides (evidence's E0 rule), and
         - mesh pixels whose MoGe depth lies clearly in front of p's mesh (fusion's PIX rule: r > max(3 sigma, 0.1 m)),
           inside p's visible mask too (held objects);
       its components of >= AMIN_FRAC of the mesh area (and >= AMIN_PX px) that no tracked object covers are
       "unexplained"
    3. the unexplained components are followed over time (a component continues a region of the last LOST frames
       that it touches). Keyframe if one is new, has grown to GROW x (and by >= the minimum area) since its last
       check, or was checked RECHECK frames ago; also on the first frame with a target and at least every PERIOD
       frames (held objects that never leave the visible mask). Regions nothing explains (a mesh spilling into
       letterbox rows) are thus not re-examined on every frame as they drift.
    4. keyframe: SAM3 c2f point prompts on this frame (gpu_stage_fast's code and filters). Seeds are
         - the segments fusion.decide would call front if this frame were the whole clip (evidence's unit quantities,
           decide's thresholds: front_candidates), largest first, and
         - per unexplained component, the segments that cover it best (greedy, at most MAX_PICKS),
       never a segment that is mostly a person's visible mask (OWNMAX / OTHERMAX), nor one that duplicates a tracked
       object or another seed (IoU > 0.5, or >= 80% inside it). With KF_REANCHOR=1 tracked objects are also restarted
       from their best-matching segment (did not help on dev: kept off).
    5. each seed gets its own tracker state (add_new_mask on frame t), propagated frame by frame with the shared
       features; it stops after STOP_AFTER frames without >= 15 px inside the targets' prompt region.
  output: cands.npz in gpu_stage's format plus 'track' (object id per row) and 'mode' = 'kf'; evidence links the rows
  by these ids (no IoU linking: kf_eval.py replaces evidence_fast.link), fusion decides and renders unchanged.

Backends (KF_BACKEND): 'edgetam' (EdgeTAM, C:/VScodeFiles/EdgeTAM via edgetam_load.py) or 'sam3' (SAM3 tracker).
Dev results (44 Penn GT clips, RTX 4070 SUPER; base = production c2f + compiled decoder): pass 34 -> 35 with either
backend; candidate stage 340 -> 100 ms/frame (EdgeTAM) or 269 (SAM3); thin bars drift with EdgeTAM (penn1791).
"""
import json
import os
import sys
import time

import cv2
import numpy as np
import torch

REPO = "C:/VScodeFiles/sam-body4d"
FV2 = f"{REPO}/scripts/occlusion/fusion_v2"
for p in (REPO, FV2):
    if p not in sys.path:
        sys.path.insert(0, p)
import gpu_stage_fast as gsf  # noqa: E402
from gpu_stage import DIL, IOU_T, MIN_PX, STRIDE_IN, STRIDE_OUT, STRIDE_V, grid_points  # noqa: E402,F401
from evidence import DV, DV_MIN, robust_scale, sandwiched  # noqa: E402
import fusion  # noqa: E402
import disk_arrays  # noqa: E402
from utils.skip_init import skip_random_init  # noqa: E402

K3 = np.ones((3, 3), np.uint8)
KPIX, MPIX, PIXCOMP = fusion.P["KPIX"], fusion.P["MPIX"], fusion.P["PIXCOMP"]
OWNMAX, OTHERMAX = fusion.P["OWNMAX"], fusion.P["OTHERMAX"]
RECHECK = int(os.environ.get("KF_RECHECK", "30"))       # frames before an unexplained region is checked again
GROW = float(os.environ.get("KF_GROW", "1.5"))          # area growth since the last check that re-checks a region
AMIN_PX = int(os.environ.get("KF_AMIN_PX", "50"))       # smallest region that can trigger a keyframe: AMIN_PX px
AMIN_FRAC = float(os.environ.get("KF_AMIN_FRAC", "0.02"))  # ... and AMIN_FRAC of the target's mesh area
LOST = 5                                                # frames a region is remembered after it was last seen
DEBUG = os.environ.get("KF_DEBUG", "0") == "1"
STOP_AFTER = int(os.environ.get("KF_STOP_AFTER", "5"))  # frames off the targets before an object is dropped
MAX_PICKS = 3                                           # seeds per unexplained component
MAX_SEEDS = 12                                          # seeds per keyframe
PERIOD = int(os.environ.get("KF_PERIOD", "30"))       # a keyframe at least every PERIOD frames (held objects)
REANCHOR = os.environ.get("KF_REANCHOR", "0") == "1"    # restart tracked objects from SAM3 segments on keyframes
DUP_IOU = 0.5


def build_sam3_tracker(ckpt_path="C:/VScodeFiles/checkpoints/sam3/sam3.pt"):
    """offline_app_lite.build_sam3_tracker (tracker + vision backbone of the SAM3 video checkpoint)."""
    from models.sam3.sam3.model_builder import build_tracker
    with skip_random_init():
        tracker = build_tracker(apply_temporal_disambiguation=True, with_backbone=True)
    with open(ckpt_path, "rb") as f:
        use_mmap = f.read(4) == b"PK\x03\x04"
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True, mmap=use_mmap)
    if "model" in ckpt and isinstance(ckpt["model"], dict):
        ckpt = ckpt["model"]
    sd = {k[len("tracker."):]: v for k, v in ckpt.items() if k.startswith("tracker.")}
    pre = "detector.backbone.vision_backbone."
    sd.update({"backbone.vision_backbone." + k[len(pre):]: v for k, v in ckpt.items() if k.startswith(pre)})
    del ckpt
    tracker.load_state_dict(sd, strict=True)
    return tracker.to("cuda").eval()


def occlusion_signal(D, Zm, lab, pid):
    """-> S (bool H x W): pixels of p's mesh that something not-p seems to cover (see module docstring)."""
    M = D > 0
    if M.sum() < 50:
        return np.zeros(M.shape, bool), M
    V0 = lab == pid
    Vd = cv2.dilate((lab > 0).astype(np.uint8), K3) > 0
    dv = max(DV_MIN, int(round(DV * np.sqrt(M.sum()))))
    distV = cv2.distanceTransform((~V0).astype(np.uint8), cv2.DIST_L2, 3) if V0.any() \
        else np.full(M.shape, 1e9, np.float32)
    S = M & ~Vd & ((distV > dv) | sandwiched(V0, 2 * dv))
    sc = robust_scale(D, Zm, V0)
    if sc is not None:
        r = D - sc[0] * Zm
        S |= M & (Zm > 0) & (r > max(KPIX * sc[1], MPIX)) & (V0 | ~Vd)
    S = cv2.morphologyEx(S.astype(np.uint8), cv2.MORPH_OPEN, K3) > 0
    return S, M


def components(B, amin):
    n, cc, st, _ = cv2.connectedComponentsWithStats(B.astype(np.uint8), connectivity=8)
    return [cc == j for j in range(1, n) if st[j, 4] >= amin]


def frame_candidates(model, proc, img_rgb, lab, sil, H, W):
    """gpu_stage_fast._sam3_stage's c2f prompts + filter + dedup for one frame (eager decoder).
    -> list of (score, bool mask, from_visible), sorted by score."""
    from PIL import Image
    region = cv2.dilate(sil.astype(np.uint8), np.ones((2 * DIL + 1,) * 2, np.uint8)) > 0
    V = lab > 0
    Vd = cv2.dilate(V.astype(np.uint8), K3) > 0
    inner = sil & ~Vd
    inv = sil & V & ~(cv2.dilate((~V).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
    pts_in, pts_o = grid_points(inner, STRIDE_IN), grid_points(region & ~sil & ~Vd, STRIDE_OUT)
    pts_v = grid_points(inv, STRIDE_V, off=2)
    pts = np.concatenate([pts_in, pts_o, pts_v])
    src = np.concatenate([np.zeros(len(pts_in) + len(pts_o), bool), np.ones(len(pts_v), bool)])
    keep, half = [], []
    with torch.autocast("cuda", dtype=torch.bfloat16):
        st = proc.set_image(Image.fromarray(img_rgb))
        c_in, c_o = grid_points(inner, gsf.C2F_STRIDE), grid_points(region & ~sil & ~Vd, gsf.C2F_STRIDE)
        c_v = grid_points(inv, gsf.C2F_STRIDE, off=2)
        cp = np.concatenate([c_in, c_o, c_v])
        csrc = np.concatenate([np.zeros(len(c_in) + len(c_o), bool), np.ones(len(c_v), bool)])
        cover = torch.zeros((H, W), dtype=torch.bool, device="cuda")
        gsf._prompt_points(model, st, cp, csrc, H, W, keep, half, cover=cover)
        pi = pts.astype(int)
        unc = ~cover.cpu().numpy()[pi[:, 1], pi[:, 0]] if len(pts) else np.zeros(0, bool)
        gsf._prompt_points(model, st, pts[unc], src[unc], H, W, keep, half)
    order = sorted(range(len(keep)), key=lambda j: -keep[j][0])
    keep = [keep[j] for j in order]
    half = [half[j] for j in order]
    sel = gsf._dedup_gpu(keep, half)
    n_pts = len(cp) + int(unc.sum())
    del st, half
    return [keep[j] for j in sel], n_pts


def iou(a, b):
    i = np.count_nonzero(a & b)
    return i / max(np.count_nonzero(a | b), 1)


def duplicate(m, other):
    """m adds (almost) nothing to an object already tracked or seeded: IoU > DUP_IOU, or m lies mostly (>= 80%)
    inside it (SAM3's multimask outputs nest: a plate inside the barbell, the barbell inside barbell + rack)."""
    i = np.count_nonzero(m & other)
    return i > DUP_IOU * np.count_nonzero(m | other) or i >= 0.8 * np.count_nonzero(m)


class _Obj:
    def __init__(self, oid, state, gen, t0, inV):
        self.oid, self.state, self.gen, self.t0, self.inV = oid, state, gen, t0, inV
        self.miss = 0
        self.mask = None
        self.frames = 0


class Sam3Backend:
    """SAM3 tracker (+ its vision backbone): one state per object, the backbone run once per frame for all."""
    name = "sam3"

    def __init__(self, run, T, H, W):
        from utils.lazy_frames import lazy_jpeg_frames
        self.tr = build_sam3_tracker()
        with lazy_jpeg_frames():
            base = self.tr.init_state(video_path=f"{run.pout}/images", offload_video_to_cpu=True)
        self.images, self.T, self.H, self.W = base["images"], T, H, W
        assert len(self.images) == T

    def features(self, i):
        with torch.inference_mode():
            img = self.images[i].cuda().float().unsqueeze(0)
            return img, self.tr.forward_image(img)

    def start(self, i, feats, mask):
        from utils.tracker_prune import prune_propagation
        st = self.tr.init_state(video_height=self.H, video_width=self.W, num_frames=self.T,
                                cached_features={i: feats}, offload_video_to_cpu=True)
        st["images"] = self.images
        self.tr.add_new_mask(inference_state=st, frame_idx=i, obj_id=1, mask=torch.from_numpy(mask))
        gen = prune_propagation(self.tr, st, self.tr.propagate_in_video(
            st, start_frame_idx=i, max_frame_num_to_track=self.T, reverse=False, tqdm_disable=True,
            propagate_preflight=True))
        return st, gen

    def step(self, st, gen, i, feats):
        st["cached_features"] = {i: feats}
        out = next(gen)
        assert out[0] == i, (out[0], i)
        return (out[3][0, 0] > 0).cpu().numpy()


class EdgeTAMBackend:
    """EdgeTAM (C:/VScodeFiles/EdgeTAM, a light SAM 2 variant) as the tracker; same per-object protocol."""
    name = "edgetam"

    def __init__(self, run, T, H, W):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import edgetam_load
        self.pred = edgetam_load.build("video")
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            self.base = self.pred.init_state(video_path=f"{run.pout}/images", offload_video_to_cpu=True)
        self.T = T
        assert self.base["num_frames"] == T

    def features(self, i):
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            img = self.base["images"][i].cuda().float().unsqueeze(0)
            return img, self.pred.forward_image(img)

    def _new_state(self):
        """init_state's dict without reloading the frames (shared with the base state)."""
        from collections import OrderedDict
        b = self.base
        st = {k: b[k] for k in ("images", "num_frames", "offload_video_to_cpu", "offload_state_to_cpu",
                                "video_height", "video_width", "device", "storage_device")}
        st.update(point_inputs_per_obj={}, mask_inputs_per_obj={}, cached_features={}, constants={},
                  obj_id_to_idx=OrderedDict(), obj_idx_to_id=OrderedDict(), obj_ids=[],
                  output_dict={"cond_frame_outputs": {}, "non_cond_frame_outputs": {}}, output_dict_per_obj={},
                  temp_output_dict_per_obj={},
                  consolidated_frame_inds={"cond_frame_outputs": set(), "non_cond_frame_outputs": set()},
                  tracking_has_started=False, frames_already_tracked={})
        return st

    def start(self, i, feats, mask):
        st = self._new_state()
        st["cached_features"] = {i: feats}
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            self.pred.add_new_mask(st, frame_idx=i, obj_id=1, mask=torch.from_numpy(mask))
        return st, self.pred.propagate_in_video(st, start_frame_idx=i, max_frame_num_to_track=self.T)

    def step(self, st, gen, i, feats):
        st["cached_features"] = {i: feats}
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            out = next(gen)
        assert out[0] == i, (out[0], i)
        return (out[2][0, 0] > 0).cpu().numpy()


def frame_evidence(run, i, lab, Zm, D, pids, has, foc, dets):
    """evidence_fast.compute's per-(frame, person) quantities for one frame: refined visible masks and, per target,
    D (mesh depth), V, s, sig, A, parts, dv, E0, Marea."""
    from evidence import refine_visible, part_image, REFINE_DEPTH
    Vr = refine_visible(lab, pids, dets[i])
    per = {}
    for k, p in enumerate(pids):
        if not has[i, k]:
            continue
        Dk = np.asarray(D[i, k], np.float32)
        V0, V = lab == p, Vr[p]
        if REFINE_DEPTH and (V0 & ~V).any():
            sc0 = robust_scale(Dk, Zm, V0)
            if sc0 is not None:
                r0 = Dk - sc0[0] * Zm
                V = V | (V0 & ~V & ((r0 <= 2.0 * sc0[1]) | (Zm <= 0) | (Dk <= 0)))
                Vr[p] = V
        sc = robust_scale(Dk, Zm, V)
        M = Dk > 0
        dv = max(DV_MIN, int(round(DV * np.sqrt(max(M.sum(), 1)))))
        distV = cv2.distanceTransform((~V).astype(np.uint8), cv2.DIST_L2, 3) if V.any() \
            else np.full(M.shape, 1e9, np.float32)
        per[p] = dict(D=Dk, V=V, s=sc[0] if sc else np.nan, sig=sc[1] if sc else np.nan,
                      A=sc[2] if sc else np.zeros_like(V), parts=part_image(run.mesh(p, i)[0], float(foc[i, k]), Dk),
                      E0=M & ~V & ((distV > dv) | sandwiched(V, 2 * dv)), Marea=int(M.sum()))
    return Vr, per


def front_candidates(cands, Zm, Vr, per, pids):
    """The candidates that fusion.decide would call 'front' for some (target, body part) if this frame were the whole
    clip: evidence's unit quantities (depth margin of the core outside the visible mask against the local ring
    anchor, structural ratio h / a on E0) and decide's thresholds (Dinf / D+ / D- / S+)."""
    P = fusion.P
    out = []
    for sc_, C, sv in cands:
        carea = np.count_nonzero(C)
        ov_people = {q: np.count_nonzero(C & Vr[q]) for q in pids}
        Cc = cv2.erode(C.astype(np.uint8), K3) > 0
        front = False
        for p, info in per.items():
            M = info["D"] > 0
            ovl = C & M
            if np.count_nonzero(ovl) < 15 or ov_people[p] / carea > OWNMAX or \
                    max([ov_people[q] for q in pids if q != p], default=0) / carea > OTHERMAX:
                continue
            V, sig = info["V"], info["sig"]
            if np.isnan(info["s"]):
                continue
            r = info["D"] - info["s"] * Zm
            ringR = max(5, int(round(0.08 * np.sqrt(info["Marea"]))))
            ring = (cv2.dilate(C.astype(np.uint8), np.ones((2 * ringR + 1,) * 2, np.uint8)) > 0) & ~C & info["A"]
            anchor = float(np.median(r[ring])) if ring.sum() >= 20 else 0.0
            for part in np.unique(info["parts"][ovl]):
                u = ovl & (info["parts"] == part)
                a = np.count_nonzero(u & ~V)
                if np.count_nonzero(u) < 15 or a < 1:
                    continue
                coreO = u & Cc & (Zm > 0) & ~V
                z = (float(np.median(r[coreO])) - anchor) / sig if coreO.sum() >= P["NCORE"] else np.nan
                h = np.count_nonzero(u & info["E0"])
                if a >= 15 and np.count_nonzero(u & ~V & (Zm > 0)) < 0.5 * a:
                    continue                                       # Dinf
                if np.isfinite(z) and z >= P["ZF"]:
                    front = True                                   # D+
                elif not (np.isfinite(z) and z <= -P["ZB"]) and h / a >= P["RMIN"] and h >= P["HMIN"] \
                        and not (np.isfinite(z) and z < P["ZS"]):
                    front = True                                   # S+
                if front:
                    break
            if front:
                break
        if front:
            out.append((sc_, C, sv))
    return out


def select_seeds(cands, novel, Vr, pids, objs, first=()):
    """`first` (front candidates) go first; then, per unexplained component Q of target p, greedily the segments
    covering most of what is still uncovered (at most MAX_PICKS), never one that is mostly a person's visible mask
    (fusion's OWNMAX / OTHERMAX on the refined visible masks). Seeds with IoU > DUP_IOU to a tracked object or to
    another seed are dropped."""
    seeds = sorted(first, key=lambda c: -np.count_nonzero(c[1]))   # largest first: its parts are duplicates
    for p, Q in novel:
        pool = []
        for sc, mk, sv in cands:
            area = np.count_nonzero(mk)
            own = np.count_nonzero(mk & Vr[p]) / area
            other = max([np.count_nonzero(mk & Vr[q]) / area for q in pids if q != p], default=0.0)
            if own <= OWNMAX and other <= OTHERMAX and np.count_nonzero(mk & Q) >= 15:
                pool.append((sc, mk, sv))
        rem = Q.copy()
        qa = np.count_nonzero(Q)
        for _ in range(MAX_PICKS):
            if not pool:
                break
            gain = [np.count_nonzero(mk & rem) for _, mk, _ in pool]
            j = int(np.argmax(gain))
            if gain[j] < max(15, 0.1 * qa):
                break
            seeds.append(pool.pop(j))
            rem &= ~seeds[-1][1]
            if np.count_nonzero(rem) < 0.2 * qa:
                break
    uniq = []
    for s in seeds:
        if any(duplicate(s[1], o.mask) for o in objs) or any(duplicate(s[1], u[1]) for u in uniq):
            continue
        uniq.append(s)
    return uniq[:MAX_SEEDS]


def run_kf(run, cache, out_name="cands.npz", log=print, backend=None):
    """Writes <cache>/<out_name> (+ kf_stats.json). Needs <cache>/mesh.npz (prep), moge.npz and, for the seeds'
    own/other test on refined visible masks, person.npz where available (else the tracked masks)."""
    from evidence_fast import _PersonDets
    backend = backend or os.environ.get("KF_BACKEND", "sam3")
    T, H, W = len(run), run.H, run.W
    z = np.load(f"{cache}/mesh.npz")
    pids, has, foc = list(z["pids"]), z["has"], z["foc"]
    L = z["L"]
    D = disk_arrays.npz_member(f"{cache}/mesh.npz", "D")
    Z = disk_arrays.npz_member(f"{cache}/moge.npz", "Z")
    dets = _PersonDets(cache, T, H, W)
    stats = dict(backend=backend, keyframes=[], seeds=[], points=0, t_backbone=0.0, t_track=0.0, t_keyframe=0.0,
                 t_signal=0.0, obj_frames=0, objects=[])
    t_all = time.time()
    t = time.time()
    model, proc, _ = gsf._sam3_build(False)
    model.backbone.language_backbone.to("cpu")      # no text prompts here (person.npz comes from elsewhere)
    torch.cuda.empty_cache()
    bk = (Sam3Backend if backend == "sam3" else EdgeTAMBackend)(run, T, H, W)
    stats["t_build"] = time.time() - t
    objs = []
    rows_path = f"{cache}/_kf_rows.bin"
    rows_f, n_rows = open(rows_path, "wb"), 0
    frame_idx, track, inV = [], [], []
    # unexplained regions followed over time, per target: [mask in the last frame, area when checked, frame checked]
    regions = {p: [] for p in pids}
    K9 = np.ones((9, 9), np.uint8)
    next_oid = 0
    last_kf = None
    for i in range(T):
        # 1. shared backbone features of frame i
        ts = time.time()
        feats = bk.features(i)
        torch.cuda.synchronize()
        stats["t_backbone"] += time.time() - ts
        # 2. advance every tracked object to frame i
        ts = time.time()
        for o in objs:
            o.mask = bk.step(o.state, o.gen, i, feats)
        torch.cuda.synchronize()
        stats["t_track"] += time.time() - ts
        # 3. occlusion signals and unexplained components
        ts = time.time()
        lab = L[i]
        Zm = np.asarray(Z[i], np.float32)
        cover = np.zeros((H, W), bool)
        for o in objs:
            cover |= o.mask
        cover_d = cv2.dilate(cover.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        sil = np.zeros((H, W), bool)
        novel = []                                    # (pid, component, its region record)
        for k, p in enumerate(pids):
            if not has[i, k]:
                regions[p] = []
                continue
            S, M = occlusion_signal(np.asarray(D[i, k], np.float32), Zm, lab, p)
            sil |= M
            amin = max(AMIN_PX, AMIN_FRAC * M.sum())
            prev = [(cv2.dilate(r[0].astype(np.uint8), K9) > 0, r) for r in regions[p]]
            cur, used = [], set()
            for Q in components(S & ~cover_d, amin):
                match = [r for d, r in prev if np.count_nonzero(Q & d)]
                used.update(id(r) for r in match)
                area = np.count_nonzero(Q)
                if match:   # continues earlier region(s): keep their last check
                    rec = [Q, max(r[1] for r in match), max(r[2] for r in match), i]
                    grown = area > GROW * rec[1] and area - rec[1] >= amin
                    new = grown or i - rec[2] >= RECHECK
                    why = "grown" if grown else "recheck"
                else:
                    rec = [Q, 0, -RECHECK, i]
                    new = True
                    why = "new"
                cur.append(rec)
                if new:
                    novel.append((p, Q, rec))
                    if DEBUG:
                        ys, xs = np.nonzero(Q)
                        log(f"   f{i} pid {p}: {why} region {area} px (last checked {rec[1]} px at f{rec[2]}), "
                            f"bbox x{xs.min()}-{xs.max()} y{ys.min()}-{ys.max()}")
            # regions not seen in this frame are remembered for LOST frames (they flicker around the size threshold)
            regions[p] = cur + [r for _, r in prev if id(r) not in used and i - r[3] <= LOST]
        region = cv2.dilate(sil.astype(np.uint8), np.ones((2 * DIL + 1,) * 2, np.uint8)) > 0
        stats["t_signal"] += time.time() - ts
        # 4. keyframe: SAM3 point prompts, seeds for the new components, one tracker state per seed
        due = has[i].any() and (last_kf is None or i - last_kf >= PERIOD)
        if novel or due:
            ts = time.time()
            img_rgb = cv2.cvtColor(run.image(i), cv2.COLOR_BGR2RGB)
            cands, n_pts = frame_candidates(model, proc, img_rgb, lab, sil, H, W)
            stats["points"] += n_pts
            Vr, per = frame_evidence(run, i, lab, Zm, D, pids, has, foc, dets)
            front = front_candidates(cands, Zm, Vr, per, pids)
            for p, Q, rec in novel:
                rec[1], rec[2] = np.count_nonzero(Q), i      # checked now
            last_kf = i
            torch.cuda.synchronize()
            stats["t_keyframe"] += time.time() - ts
            ts = time.time()
            n_re = 0
            if REANCHOR:
                # a tracked mask drifts (a light tracker drops thin parts such as a bar end): restart each object
                # from this keyframe's SAM3 segment of it - the best-IoU one (>= 0.5) among those that hold >= 80%
                # of the tracked mask, else the best-IoU one if >= 0.7; the object keeps its id. (The largest
                # holding segment was tried first: on penn1791 it switched a plate to 'plate + caption overlay'.)
                for o in objs:
                    om = np.count_nonzero(o.mask)
                    best, best_key = None, None
                    for sc, mk, sv in cands:
                        inter = np.count_nonzero(mk & o.mask)
                        if not inter:
                            continue
                        j = inter / np.count_nonzero(mk | o.mask)
                        holds = inter >= 0.8 * om
                        if j >= 0.5 and (holds or j >= 0.7):
                            key = (holds, j)
                            if best_key is None or key > best_key:
                                best, best_key = mk, key
                    if best is not None and iou(best, o.mask) < 0.95:
                        o.gen.close()
                        o.state, o.gen = bk.start(i, feats, best)
                        o.mask = bk.step(o.state, o.gen, i, feats)
                        n_re += 1
            uniq = select_seeds(cands, [(p, Q) for p, Q, _ in novel], Vr, pids, objs, first=front)
            for sc, mk, sv in uniq:
                st, gen = bk.start(i, feats, mk)
                o = _Obj(next_oid, st, gen, i, sv)
                next_oid += 1
                o.mask = bk.step(st, gen, i, feats)
                objs.append(o)
            torch.cuda.synchronize()
            stats["t_track"] += time.time() - ts
            stats["keyframes"].append(i)
            stats["seeds"].append(len(uniq))
            log(f"{run.name} keyframe {i}: {len(novel)} new region(s){' (periodic)' if due and not novel else ''}, {len(cands)} segments, {len(front)} front, {n_pts} points, "
                f"{len(uniq)} new object(s), {n_re} re-anchored, {len(objs)} tracked")
        # 5. rows of frame i, then drop objects that left the targets
        keep_objs = []
        for o in objs:
            a = np.count_nonzero(o.mask)
            if MIN_PX <= a <= 0.5 * H * W:
                rows_f.write(np.packbits(o.mask.reshape(-1)).tobytes())
                n_rows += 1
                frame_idx.append(i)
                track.append(o.oid)
                inV.append(o.inV)
            o.frames += 1
            o.miss = 0 if np.count_nonzero(o.mask & region) >= 15 else o.miss + 1
            if o.miss >= STOP_AFTER:
                o.gen.close()
                stats["objects"].append(dict(oid=o.oid, start=o.t0, end=i, frames=o.frames))
            else:
                keep_objs.append(o)
        objs = keep_objs
        stats["obj_frames"] += len(objs)
    for o in objs:
        o.gen.close()
        stats["objects"].append(dict(oid=o.oid, start=o.t0, end=T - 1, frames=o.frames))
    del objs, bk
    rows_f.close()
    nb = (H * W + 7) // 8
    allm = np.memmap(rows_path, np.uint8, "r", shape=(n_rows, nb)) if n_rows else np.zeros((0, nb), np.uint8)
    np.savez_compressed(f"{cache}/{out_name}", masks=allm, frame=np.array(frame_idx, int),
                        score=np.ones(n_rows), inV=np.array(inV, bool), shape=np.array([H, W]),
                        mode=np.array("kf"), track=np.array(track, int))
    del allm
    disk_arrays.release(rows_path)
    stats["t_total"] = time.time() - t_all
    stats.update(T=T, n_rows=n_rows, n_objects=next_oid)
    json.dump(stats, open(f"{cache}/kf_stats.json", "w"), indent=1)
    del model, proc
    import gc
    gc.collect()
    torch.clear_autocast_cache()
    torch.cuda.empty_cache()
    return stats
