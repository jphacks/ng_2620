"""Faster drop-in for evidence.compute with identical output (evidence.pkl compares equal, NaN == NaN; checked by
scripts/eval/check_fusion_fast.py).

The frozen evidence.py stays the reference. Changes, none of which alter a value:
  * the per-(segment, person, part) loop works on the segment's bounding box grown by the largest ring radius + 1
    px instead of on full frames. Every quantity there is a count, a median of a pixel set, or a morphology result
    whose support lies inside that box (dilation by ringR stays within ringR of the segment; the 1 px margin keeps
    the erosion's border behaviour identical), so cropping changes nothing but the work (full-frame boolean sums
    were 12 of 27 s on a 120-frame clip).
  * link(): the 0/1 intersection matrices between frames t and t+g are computed on the GPU in float32 (exact:
    integer partial sums below 2^24) with each frame's masks converted once per pass; the IoU expression and the
    order-dependent union-find are the frozen ones.
"""
import os
import pickle
import sys

import cv2
import numpy as np

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ME)
from evidence import (DV, DV_MIN, K3, LINK_GAP, LINK_IOU, REFINE_DEPTH, load_person_dets, part_image,  # noqa: E402
                      refine_visible, robust_scale, sandwiched)
from gpu_stage import load_cands  # noqa: E402
from prep import load  # noqa: E402
from shared_cache import moge_z  # noqa: E402


def link(masks, fr):
    """evidence.link, intersections on the GPU (exact) when available."""
    n = len(masks)
    par = list(range(n))

    def f(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    if hasattr(masks, "rows"):   # shared_cache.PackedMasks: unpack one frame's rows at a time, same values
        rows, area = masks.rows, masks.areas().astype(np.float32)
    else:
        flat = masks.reshape(n, -1)
        rows, area = flat.__getitem__, flat.sum(1).astype(np.float32)
    T = int(fr.max()) + 1 if n else 0
    try:
        import torch
        dev = "cuda" if torch.cuda.is_available() else None
    except ImportError:
        dev = None
    idx = [np.nonzero(fr == t)[0] for t in range(T)]
    linked = set()
    for g in range(1, LINK_GAP + 1):
        cache = {}

        def mat(t):
            if t not in cache:
                cache[t] = torch.from_numpy(rows(idx[t])).to(dev).float() if dev else rows(idx[t]).astype(np.float32)
            return cache[t]
        for t in range(T - g):
            A, B = idx[t], idx[t + g]
            if len(A) == 0 or len(B) == 0:
                continue
            if dev:
                with torch.autocast("cuda", enabled=False):
                    inter = (mat(t) @ mat(t + g).T).cpu().numpy()
            else:
                inter = mat(t) @ mat(t + g).T
            cache.pop(t - 1, None)
            iou = inter / (area[A][:, None] + area[B][None, :] - inter)
            for ia, a in enumerate(A):
                ib = int(iou[ia].argmax())
                if iou[ia, ib] > LINK_IOU and int(iou[:, ib].argmax()) == ia and a not in linked:
                    par[f(a)] = f(B[ib])
                    linked.add(a)
    return np.array([f(x) for x in range(n)])


def _out_entry(v):
    """evidence.compute's output form of one per[(i, p)] entry."""
    return dict(s=v["s"], sig=v["sig"], dv=v["dv"], Marea=v["Marea"], Varea=v["Varea"], parts=v["parts"],
                A=np.packbits(v["A"]), E0=np.packbits(v["E0"]), V=np.packbits(v["V"]))


class _PersonDets:
    """evidence.load_person_dets(cache, T, H, W) with each frame's masks unpacked when that frame is read (the
    frozen loader unpacks every 'person' mask up front: ~27,000 full-frame bool arrays for a 5-minute clip)."""

    def __init__(self, cache, T, H, W):
        self.H, self.W = H, W
        self.rows = [[] for _ in range(T)]
        if os.path.exists(f"{cache}/person.npz"):
            with np.load(f"{cache}/person.npz") as d:
                for row, f in zip(d["masks"], d["frame"]):
                    self.rows[int(f)].append(row)

    def __getitem__(self, i):
        return [np.unpackbits(row)[:self.H * self.W].reshape(self.H, self.W).astype(bool) for row in self.rows[i]]


def _bbox(C, margin, H, W):
    rows, cols = np.nonzero(C.any(1))[0], np.nonzero(C.any(0))[0]
    if len(rows) == 0:
        return None
    return (slice(max(0, rows[0] - margin), min(H, rows[-1] + 1 + margin)),
            slice(max(0, cols[0] - margin), min(W, cols[-1] + 1 + margin)))


def compute(run, cache):
    p_out = f"{cache}/evidence.pkl"
    if os.path.exists(p_out):
        return pickle.load(open(p_out, "rb"))
    c = load(cache)
    D_all, L, pids, has, foc = c["D"], c["L"], list(c["pids"]), c["has"], c["foc"]
    Zall = moge_z(cache)
    masks, fr, score, inV = load_cands(cache)
    track = link(masks, fr) if len(masks) else np.zeros(0, int)
    T, H, W = L.shape
    dets = _PersonDets(cache, T, H, W)
    Vr_all = {}
    per = {}
    units = []
    for i in range(T):
        Zm = Zall[i].astype(np.float32)
        lab = L[i]
        Vr = refine_visible(lab, pids, dets[i])
        done = [(i, p) for p in pids]   # packed into their output form at the end of this frame (see below)
        for q in pids:
            Vr_all[(i, q)] = np.packbits(Vr[q])
        Dz = {}
        for k, p in enumerate(pids):
            if not has[i, k]:
                continue
            D = D_all[i, k].astype(np.float32)
            V0 = lab == p
            V = Vr[p]
            if REFINE_DEPTH and (V0 & ~V).any():
                sc0 = robust_scale(D, Zm, V0)
                if sc0 is not None:
                    r0 = D - sc0[0] * Zm
                    V = V | (V0 & ~V & ((r0 <= 2.0 * sc0[1]) | (Zm <= 0) | (D <= 0)))
                    Vr[p] = V
                    Vr_all[(i, p)] = np.packbits(V)
            sc = robust_scale(D, Zm, V)
            m = run.mesh(p, i)
            parts = part_image(m[0], float(foc[i, k]), D)
            M = D > 0
            dv = max(DV_MIN, int(round(DV * np.sqrt(max(M.sum(), 1)))))
            distV = cv2.distanceTransform((~V).astype(np.uint8), cv2.DIST_L2, 3) if V.any() \
                else np.full(M.shape, 1e9, np.float32)
            E0 = M & ~V & ((distV > dv) | sandwiched(V, 2 * dv))
            per[(i, p)] = dict(V=V, s=sc[0] if sc else np.nan, sig=sc[1] if sc else np.nan,
                               A=sc[2] if sc else np.zeros_like(V), parts=parts, dv=dv, E0=E0,
                               Marea=int(M.sum()), Varea=int(V.sum()))
            Dz[p] = D
        objs = [(int(ci), int(track[ci]), masks[ci], bool(inV[ci]), float(score[ci]), None)
                for ci in np.nonzero(fr == i)[0]]
        objs += [(-1, -int(q), Vr[q], False, 1.0, int(q)) for q in pids if Vr[q].sum() >= 15]
        ring_max = max([max(5, int(round(0.08 * np.sqrt(per[(i, p)]["Marea"])))) for p in Dz], default=5)
        for ci, tr, Cf, civ, csc, qperson in objs:
            sl = _bbox(Cf, ring_max + 1, H, W)
            if sl is None:
                C = Cf[:1, :1]
                sl = (slice(0, 1), slice(0, 1))
            else:
                C = Cf[sl]
            Cc = cv2.erode(C.astype(np.uint8), K3) > 0
            ov_people = {p: int((C & Vr[p][sl]).sum()) for p in pids}
            carea = int(C.sum())
            Zc = Zm[sl]
            for p, Dfull in Dz.items():
                if qperson == p:
                    continue
                info = per[(i, p)]
                D = Dfull[sl]
                M = D > 0
                ovl = C & M
                if ovl.sum() < 15:
                    continue
                V = Vr[p][sl]
                partsC = info["parts"][sl]
                if not np.isnan(info["s"]):
                    r = D - info["s"] * Zc
                    ringR = max(5, int(round(0.08 * np.sqrt(info["Marea"]))))
                    ring = (cv2.dilate(C.astype(np.uint8), np.ones((2 * ringR + 1,) * 2, np.uint8)) > 0) & ~C \
                        & info["A"][sl]
                    anchor = float(np.median(r[ring])) if ring.sum() >= 20 else 0.0
                else:
                    r = None
                    anchor = 0.0
                mdq = np.nan
                if qperson is not None and qperson in Dz:
                    Dq = Dz[qperson][sl]
                    both = ovl & (Dq > 0)
                    if both.sum() >= 10:
                        mdq = float(np.median(D[both] - Dq[both]))
                E0 = info["E0"][sl]
                for part in np.unique(partsC[ovl]):
                    u = ovl & (partsC == part)
                    if u.sum() < 15:
                        continue
                    core = u & Cc & (Zc > 0)
                    nV = int((u & V).sum())
                    nvalO = int((u & ~V & (Zc > 0)).sum())
                    a = int((u & ~V).sum())
                    h = int((u & E0).sum())
                    if r is not None and core.sum() > 0:
                        mg = float(np.median(r[core]))
                        coreO = core & ~V
                        mgo = float(np.median(r[coreO])) if coreO.sum() >= 5 else np.nan
                        coreI = core & V
                        mgi = float(np.median(r[coreI])) if coreI.sum() >= 5 else np.nan
                    else:
                        mg = mgo = mgi = np.nan
                    units.append(dict(i=i, ci=ci, tr=tr, pid=p, part=int(part), n=int(u.sum()),
                                      nV=nV, a=a, h=h, ncore=int(core.sum()), mg=mg, mgo=mgo, mgi=mgi,
                                      anchor=anchor, sig=info["sig"], s=info["s"], carea=carea,
                                      own=ov_people[p] / carea, cov=ov_people[p] / max(info["Varea"], 1),
                                      ncoreO=int((core & ~V).sum()), ncoreI=int((core & V).sum()),
                                      other=max([ov_people[q] for q in pids if q != p], default=0) / carea,
                                      inV=civ, score=csc, person=qperson, mdq=mdq, nvalO=nvalO))
        # no later frame reads this frame's per entries: pack them now (the same dicts the frozen code builds after
        # the loop), so the full-frame V / A / E0 arrays of every frame are not all alive at once
        for k in done:
            if k in per:
                per[k] = _out_entry(per[k])
    out = dict(per=per, Vr=Vr_all, units=units, track=track, pids=pids, T=T, H=H, W=W)
    pickle.dump(out, open(p_out, "wb"))
    return out
