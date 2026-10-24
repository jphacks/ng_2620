"""Keyframe-tracked occluder candidates for fusion v2 (gpu_stage_fast.run_gpu(keyframe=True); opt-in).

The default candidate stage prompts SAM3 with ~180 points on every frame and links the per-frame segments afterwards by
IoU (median track length: 1 frame). Here SAM3's point prompts and the 'person' grounding run only on keyframes, and
only the occluders chosen there are followed by EdgeTAM (edgetam_tracker.py) until they stop touching the targets:

  per frame t (in order)
    1. EdgeTAM's backbone runs once on frame t; every tracked object's state reads these features
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
       frames (held objects that never leave the visible mask)
    4. keyframe: SAM3 c2f point prompts on this frame (gpu_stage_fast's prompts, filter and dedup; eager decoder) and
       the 'person' grounding (-> person.npz on keyframes only: evidence refines the visible masks there). Seeds are
         - the segments fusion.decide would call front if this frame were the whole clip (evidence's unit quantities,
           decide's thresholds: front_candidates), largest first, and
         - per unexplained component, the segments that cover it best (greedy, at most MAX_PICKS),
       never a segment that is mostly a person's visible mask (OWNMAX / OTHERMAX), nor one that duplicates a tracked
       object or another seed (IoU > 0.5, or >= 80% inside it)
    5. each seed gets its own EdgeTAM state, propagated frame by frame with the shared features; it stops after
       STOP_AFTER frames without >= 15 px inside the targets' prompt region
  output: cands.npz in gpu_stage's format plus 'track' (object id per row; evidence_fast links the rows by these ids
  instead of by IoU), person.npz (keyframes only) and kf_stats.json. fusion decides and renders unchanged.

Dev (44 Penn clips with GT, RTX 4070 SUPER), prototype scripts/occlusion/keyframe (2026-10-23): pass 34 -> 35, pooled
F1 .861 -> .852; candidate stage 340 -> 100 ms/frame; fusion stage 616 -> 417 ms/frame on Penn.

Thin objects (thin=True): EdgeTAM keeps losing thin parts between keyframes - a bar loses the segment beyond the
plate and slides onto the plate's hub (penn1791: thin pixels of the bar object 476 -> 227 -> 13 over frames 0 / 9 / 20,
F1 .911 -> .404), and 62 of the dev objects lose half of their thin pixels, most within 1-2 frames, so a "lost thin
parts" keyframe trigger would fire on nearly every object. SAM3's own tracker keeps them (penn1791 .980). So a seed
whose thin part (pixels an opening with a THIN_DISK disc removes) has >= THIN_PX px, >= THIN_ON px of them on a target's
mesh silhouette, and is >= THIN_SHARE of what the seed covers on the targets, is tracked by SAM3's tracker
(sam3_tracker.py; its backbone runs only on the frames such an object is alive), every other seed by EdgeTAM. Sizes
are for a 270 px short side and scale with the frame. Without the share test (dev: SAM3's backbone on 31% of the
frames) guitars and plates with a bar end went to SAM3's tracker too, and penn0376 / penn1959 fell below F1 .90; their
shares were .01-.13, the bars' and the golf club's .29-1.0. Dev 44 with thin objects: pass 36 (EdgeTAM only 35, base 34).

Held-out test7 (2026-10-23, testdata/2026-10-23/test7/PREREG.md): not adopted. Passes 6 -> 8 of 11, pooled F1 .914 ->
.946 and fusion 730 -> 453 s, but penn0365 fell .280 -> 0 (the pre-registered rule allows -0.05 per clip): dumbbells
held in the hands lie inside the person's visible mask, and seeds are never segments that are mostly a visible mask,
nor judged by fusion's inside-visible ('i') rule, so held objects there are never tracked. The thin-object routing
fired on 4 test7 clips without changing their scores (EdgeTAM only: 8 passes, 400 s).
"""
import gc
import json
import os
import sys
import time

import cv2
import numpy as np

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ME)
import disk_arrays  # noqa: E402
import fusion  # noqa: E402
from evidence import DV, DV_MIN, REFINE_DEPTH, part_image, refine_visible, robust_scale, sandwiched  # noqa: E402
from gpu_stage import DIL, MIN_PX, STRIDE_IN, STRIDE_OUT, STRIDE_V, grid_points  # noqa: E402

K3 = np.ones((3, 3), np.uint8)
K9 = np.ones((9, 9), np.uint8)
KPIX, MPIX = fusion.P["KPIX"], fusion.P["MPIX"]
OWNMAX, OTHERMAX = fusion.P["OWNMAX"], fusion.P["OTHERMAX"]
RECHECK = 30        # frames before an unexplained region is checked again
GROW = 1.5          # area growth since the last check that re-checks a region
AMIN_PX = 50        # smallest region that can trigger a keyframe: AMIN_PX px ...
AMIN_FRAC = 0.02    # ... and AMIN_FRAC of the target's mesh area
LOST = 5            # frames a region is remembered after it was last seen
STOP_AFTER = 5      # frames off the targets before an object is dropped
MAX_PICKS = 3       # seeds per unexplained component
MAX_SEEDS = 12      # seeds per keyframe
PERIOD = 30         # a keyframe at least every PERIOD frames (held objects)
DUP_IOU = 0.5
THIN_DISK = 7       # opening disc (px at a 270 px short side) that removes an object's thin parts
THIN_PX = 150       # thin pixels that make an object thin (at 270 px; x scale^2) ...
THIN_ON = 50        # ... of which on a target's mesh silhouette
THIN_SHARE = 0.2    # ... and that make up this share of the seed's pixels on the silhouettes
THIN = os.environ.get("FUSION_KF_THIN", "1") == "1"   # run_keyframes' default (gpu_stage_fast.run_gpu(keyframe=True))


def occlusion_signal(D, Zm, lab, pid):
    """-> S (bool H x W): pixels of p's mesh that something not-p seems to cover (see module docstring), M."""
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


def thin_part(mask, sil, scale):
    """-> (thin pixels of the seed, of them on a target's silhouette, seed pixels on a silhouette)."""
    d = max(3, int(round(THIN_DISK * scale)) | 1)
    op = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d)))
    th = mask & (op == 0)
    return np.count_nonzero(th), np.count_nonzero(th & sil), np.count_nonzero(mask & sil)


def thin_object(tp, scale):
    """The seed has a thin part (bar, stick, stand) of >= THIN_PX px, >= THIN_ON px of it over a target, and the thin
    part is >= THIN_SHARE of what the seed covers on the targets (a guitar's neck or a plate's bar end beside a body
    that covers the target mostly does not count)."""
    return tp[0] >= THIN_PX * scale ** 2 and tp[1] >= THIN_ON * scale ** 2 and tp[1] >= THIN_SHARE * tp[2]


def components(B, amin):
    n, cc, st, _ = cv2.connectedComponentsWithStats(B.astype(np.uint8), connectivity=8)
    return [cc == j for j in range(1, n) if st[j, 4] >= amin]


def keyframe_segments(model, proc, person_prompt, img_rgb, lab, sil, H, W):
    """gpu_stage_fast._sam3_stage on one frame: c2f point prompts + filter + dedup (eager decoder) and the 'person'
    grounding. -> (segments [(score, bool mask, from_visible)] by score, number of points, person masks, scores)."""
    import torch
    import gpu_stage_fast as gsf
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
        proc.reset_all_prompts(st)
        r = person_prompt(st)
        gm, gs = r["masks"][:, 0].cpu().numpy().astype(bool), r["scores"].float().cpu().numpy()
    order = sorted(range(len(keep)), key=lambda j: -keep[j][0])
    keep = [keep[j] for j in order]
    half = [half[j] for j in order]
    sel = gsf._dedup_gpu(keep, half)
    n_pts = len(cp) + int(unc.sum())
    del st, r, half
    return [keep[j] for j in sel], n_pts, gm, gs


def duplicate(m, other):
    """m adds (almost) nothing to an object already tracked or seeded: IoU > DUP_IOU, or m lies mostly (>= 80%)
    inside it (SAM3's multimask outputs nest: a plate inside the barbell, the barbell inside barbell + rack)."""
    i = np.count_nonzero(m & other)
    return i > DUP_IOU * np.count_nonzero(m | other) or i >= 0.8 * np.count_nonzero(m)


class _Obj:
    def __init__(self, oid, track, t0, inV, tk, info):
        self.oid, self.track, self.t0, self.inV, self.tk, self.info = oid, track, t0, inV, tk, info
        self.miss = 0
        self.mask = None
        self.frames = 0


def frame_evidence(run, i, lab, Zm, D, pids, has, foc, dets_i):
    """evidence_fast.compute's per-(frame, person) quantities for one frame: refined visible masks and, per target,
    D (mesh depth), V, s, sig, A, parts, E0, Marea."""
    Vr = refine_visible(lab, pids, dets_i)
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
    """The segments that fusion.decide would call 'front' for some (target, body part) if this frame were the whole
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
    """`first` (front segments) go first, largest first; then, per unexplained component Q of target p, greedily the
    segments covering most of what is still uncovered (at most MAX_PICKS), never one that is mostly a person's visible
    mask (fusion's OWNMAX / OTHERMAX on the refined visible masks). Seeds that duplicate a tracked object or another
    seed are dropped."""
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


def run_keyframes(run, cache, model, proc, person_prompt, log=print, thin=None, moge_z=None):
    """Writes <cache>/cands.npz (mode 'kf', or 'kf+thin'), person.npz (keyframes only) and kf_stats.json. Needs
    mesh.npz (prep) and moge.npz. model / proc: gpu_stage_fast._sam3_build(False); person_prompt(state): its 'person'
    grounding. thin: track thin objects with SAM3's tracker (see the module docstring; None: THIN). moge_z: moge.npz's
    Z when the caller has it (gpu_stage_fast saves moge.npz in a thread while this stage runs)."""
    import torch
    thin = THIN if thin is None else thin
    from edgetam_tracker import EdgeTAMTracker
    T, H, W = len(run), run.H, run.W
    with np.load(f"{cache}/mesh.npz") as z:
        pids, has, foc = list(z["pids"]), z["has"], z["foc"]
    L = disk_arrays.npz_member(f"{cache}/mesh.npz", "L")
    D = disk_arrays.npz_member(f"{cache}/mesh.npz", "D")
    Z = moge_z if moge_z is not None else disk_arrays.npz_member(f"{cache}/moge.npz", "Z")
    stats = dict(backend="edgetam+sam3" if thin else "edgetam", keyframes=[], seeds=[], points=0, t_backbone=0.0,
                 t_track=0.0, t_keyframe=0.0, t_signal=0.0, obj_frames=0, objects=[], thin_objects=0, sam3_frames=0)
    t_all = t = time.time()
    paths = [f"{run.pout}/images/{s}.jpg" for s in run.stems]
    tk = EdgeTAMTracker(paths)
    tk3 = None                                     # SAM3's tracker, built when the first thin object appears
    scale = min(H, W) / 270
    stats["t_build"] = time.time() - t
    objs = []
    rows_path = f"{cache}/_cands_rows.bin"
    rows_f, n_rows = open(rows_path, "wb"), 0
    frame_idx, track, inV = [], [], []
    pm, pf, ps = [], [], []
    # unexplained regions followed over time, per target: [mask in the last frame, area when checked, frame checked,
    # frame last seen]
    regions = {p: [] for p in pids}
    next_oid = 0
    last_kf = None
    for i in range(T):
        # 1. shared backbone features of frame i (SAM3's tracker: only while one of its objects is alive)
        ts = time.time()
        feats = tk.features(i)
        feats3 = tk3.features(i) if any(o.tk is tk3 for o in objs) else None
        torch.cuda.synchronize()
        stats["t_backbone"] += time.time() - ts
        stats["sam3_frames"] += feats3 is not None
        # 2. advance every tracked object to frame i
        ts = time.time()
        for o in objs:
            o.mask = o.tk.step(o.track, i, feats3 if o.tk is tk3 else feats)
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
                    new = (area > GROW * rec[1] and area - rec[1] >= amin) or i - rec[2] >= RECHECK
                else:
                    rec = [Q, 0, -RECHECK, i]
                    new = True
                cur.append(rec)
                if new:
                    novel.append((p, Q, rec))
            # regions not seen in this frame are remembered for LOST frames (they flicker around the size threshold)
            regions[p] = cur + [r for _, r in prev if id(r) not in used and i - r[3] <= LOST]
        region = cv2.dilate(sil.astype(np.uint8), np.ones((2 * DIL + 1,) * 2, np.uint8)) > 0
        stats["t_signal"] += time.time() - ts
        # 4. keyframe: SAM3 point prompts and person grounding, seeds, one tracker state per seed
        due = has[i].any() and (last_kf is None or i - last_kf >= PERIOD)
        if novel or due:
            ts = time.time()
            img_rgb = cv2.cvtColor(run.image(i), cv2.COLOR_BGR2RGB)
            cands, n_pts, gm, gs = keyframe_segments(model, proc, person_prompt, img_rgb, lab, sil, H, W)
            for mk, sc in zip(gm, gs):
                pm.append(np.packbits(mk.reshape(-1)))
                pf.append(i)
                ps.append(float(sc))
            stats["points"] += n_pts
            Vr, per = frame_evidence(run, i, lab, Zm, D, pids, has, foc, list(gm))
            front = front_candidates(cands, Zm, Vr, per, pids)
            for p, Q, rec in novel:
                rec[1], rec[2] = np.count_nonzero(Q), i      # checked now
            last_kf = i
            torch.cuda.synchronize()
            stats["t_keyframe"] += time.time() - ts
            ts = time.time()
            uniq = select_seeds(cands, [(p, Q) for p, Q, _ in novel], Vr, pids, objs, first=front)
            for sc, mk, sv in uniq:
                otk = tk
                tp = thin_part(mk, sil, scale)
                if thin and thin_object(tp, scale):
                    t3 = time.time()
                    if tk3 is None:
                        from sam3_tracker import Sam3Tracker
                        tk3 = Sam3Tracker(paths, H, W)
                    if feats3 is None:
                        feats3 = tk3.features(i)
                        stats["sam3_frames"] += 1
                    otk = tk3
                    stats["thin_objects"] += 1
                    stats["t_build"] += time.time() - t3
                tr, m = otk.start(i, feats3 if otk is tk3 else feats, mk)
                o = _Obj(next_oid, tr, i, sv, otk, dict(tracker="sam3" if otk is tk3 else "edgetam", area=int(mk.sum()),
                                                        thin=tp[0], thin_on=tp[1], on=tp[2]))
                o.mask = m
                next_oid += 1
                objs.append(o)
            torch.cuda.synchronize()
            stats["t_track"] += time.time() - ts
            stats["keyframes"].append(i)
            stats["seeds"].append(len(uniq))
            log(f"{run.name} keyframe {i}: {len(novel)} new region(s){' (periodic)' if due and not novel else ''}, "
                f"{len(cands)} segments, {len(front)} front, {n_pts} points, {len(uniq)} new object(s), "
                f"{len(objs)} tracked")
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
                o.tk.stop(o.track)
                stats["objects"].append(dict(oid=o.oid, start=o.t0, end=i, frames=o.frames, **o.info))
            else:
                keep_objs.append(o)
        objs = keep_objs
        stats["obj_frames"] += len(objs)
        del feats, feats3
    for o in objs:
        o.tk.stop(o.track)
        stats["objects"].append(dict(oid=o.oid, start=o.t0, end=T - 1, frames=o.frames, **o.info))
    del objs, tk, tk3
    rows_f.close()
    nb = (H * W + 7) // 8
    allm = np.memmap(rows_path, np.uint8, "r", shape=(n_rows, nb)) if n_rows else np.zeros((0, nb), np.uint8)
    np.savez_compressed(f"{cache}/cands.npz", masks=allm, frame=np.array(frame_idx, int), score=np.ones(n_rows),
                        inV=np.array(inV, bool), shape=np.array([H, W]), mode=np.array("kf+thin" if thin else "kf"),
                        track=np.array(track, int))
    del allm
    disk_arrays.release(rows_path)
    np.savez_compressed(f"{cache}/person.npz", masks=np.stack(pm) if pm else np.zeros((0, nb), np.uint8),
                        frame=np.array(pf, int), score=np.array(ps), shape=np.array([H, W]))
    stats["t_total"] = time.time() - t_all
    stats.update(T=T, n_rows=n_rows, n_objects=next_oid)
    with open(f"{cache}/kf_stats.json", "w") as f:
        json.dump(stats, f, indent=1)
    gc.collect()
    torch.cuda.empty_cache()
    return stats
