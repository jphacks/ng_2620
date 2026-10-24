r"""R3_fusion evidence stage (CPU): everything that the decision needs, computed once per run and cached.

Per person p and frame i:
  * scale s_p(i) and alignment noise sigma_p(i): s = mode of log(D_mesh / Z_moge) over p's eroded visible mask, then one
    re-estimate on the pixels that agree (|r| < 2.5 sigma). r = D_mesh - s * Z_moge is the per-pixel depth residual in
    p's metric frame (r > 0: the scene surface is in front of p's mesh).  sigma = 1.4826 * MAD(r) on those pixels.
  * body-part label image (10 parts) of p's silhouette.
Per candidate C (class-agnostic SAM3 segment) and person p, split by body part ("unit"):
  D-evidence  m_glob  = median r over the unit's core (C eroded 1 px, valid MoGe)
              m_loc   = m_glob - median r of p's agreeing visible pixels in a ring around C   (local anchoring)
              n_core  = number of core pixels (thin objects -> few/no core pixels -> D not usable)
  S-evidence  a = |unit \ V_p|, h = |unit \ V_p  that is far from V_p (> dv) or has V_p on both sides (< 2 dv)|
Candidates are linked into tracks (mutual-best IoU between frames t, t+1, t+2).
Reuses ideas/code from F_3d_proxies (scale anchoring, MoGe lifting) and C_object_layers (point-grid candidates,
distance / sandwich evidence, part split, linking).
"""
import os
import pickle
import sys

import cv2
import numpy as np
from scipy.spatial import cKDTree

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ME)
from r3common import Run  # noqa: E402
from prep import load  # noqa: E402
from gpu_stage import load_cands  # noqa: E402

VPARTS = np.load(f"{ME}/vertex_parts.npy")
DV, DV_MIN = 0.06, 3          # misfit band width, as a fraction of sqrt(mesh area) (C_object_layers)
LINK_IOU, LINK_GAP = 0.5, 2
K3 = np.ones((3, 3), np.uint8)


def part_image(V, f, depth):
    H, W = depth.shape
    out = np.full((H, W), 255, np.uint8)
    u = f * V[:, 0] / V[:, 2] + W / 2
    v = f * V[:, 1] / V[:, 2] + H / 2
    px, py = np.floor(u).astype(int), np.floor(v).astype(int)
    ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
    ok[ok] &= np.abs(V[ok, 2] - depth[py[ok], px[ok]]) < 0.03
    M = depth > 0
    if ok.sum() == 0 or M.sum() == 0:
        return out
    tree = cKDTree(np.stack([u[ok], v[ok]], 1))
    ys, xs = np.nonzero(M)
    _, k = tree.query(np.stack([xs + 0.5, ys + 0.5], 1))
    out[ys, xs] = VPARTS[ok][k]
    return out


def sandwiched(S, r):
    """Pixels with S on both sides along one of 4 directions within r px."""
    out = np.zeros(S.shape, bool)
    s8 = S.astype(np.uint8)
    for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
        k1 = np.zeros((2 * r + 1, 2 * r + 1), np.uint8)
        k2 = k1.copy()
        for t in range(1, r + 1):
            k1[r + dy * t, r + dx * t] = 1
            k2[r - dy * t, r - dx * t] = 1
        out |= (cv2.dilate(s8, k1) > 0) & (cv2.dilate(s8, k2) > 0)
    return out


def robust_scale(D, Zm, V):
    """mode-based scale + MAD noise on agreeing visible pixels. Returns s, sigma, agree mask (or None)."""
    B = (cv2.erode(V.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0) & (D > 0) & (Zm > 0)
    if B.sum() < 50:
        B = V & (D > 0) & (Zm > 0)
    if B.sum() < 20:
        return None
    lr = np.log(D[B] / Zm[B])
    med = np.median(lr)
    h, e = np.histogram(lr, bins=np.arange(med - 0.4, med + 0.4, 0.005))
    hs = np.convolve(h, np.ones(5) / 5, "same")
    s = float(np.exp(e[np.argmax(hs)] + 0.0025))
    r = D - s * Zm
    sig = 1.4826 * np.median(np.abs(r[B]))
    A = B & (np.abs(r) < 2.5 * sig)
    if A.sum() >= 20:
        s = float(np.median(D[A] / Zm[A]))
        r = D - s * Zm
        sig = 1.4826 * np.median(np.abs(r[A]))
        A = B & (np.abs(r) < 2.5 * sig)
    return s, float(max(sig, 0.01)), A


def link(masks, fr):
    """Mutual-best IoU linking (frames t -> t+g, g <= LINK_GAP), union-find -> track id per candidate."""
    n = len(masks)
    par = list(range(n))

    def f(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    flat = masks.reshape(n, -1)
    area = flat.sum(1).astype(np.float32)
    T = int(fr.max()) + 1 if n else 0
    linked = set()
    for g in range(1, LINK_GAP + 1):
        for t in range(T - g):
            A = np.nonzero(fr == t)[0]
            B = np.nonzero(fr == t + g)[0]
            if len(A) == 0 or len(B) == 0:
                continue
            inter = flat[A].astype(np.float32) @ flat[B].T.astype(np.float32)
            iou = inter / (area[A][:, None] + area[B][None, :] - inter)
            for ia, a in enumerate(A):
                ib = int(iou[ia].argmax())
                if iou[ia, ib] > LINK_IOU and int(iou[:, ib].argmax()) == ia and a not in linked:
                    par[f(a)] = f(B[ib])
                    linked.add(a)
    return np.array([f(x) for x in range(n)])


def load_person_dets(cache, T, H, W):
    out = [[] for _ in range(T)]
    if not os.path.exists(f"{cache}/person.npz"):
        return out
    d = np.load(f"{cache}/person.npz")
    for row, f, s in zip(d["masks"], d["frame"], d["score"]):
        out[int(f)].append(np.unpackbits(row)[:H * W].reshape(H, W).astype(bool))
    return out


def refine_visible(lab, pids, dets, min_iou=0.3):
    """SAM3's tracked visible mask can contain the occluder (guitar, plate, bar). A fresh per-frame SAM3 concept
    detection of 'person' usually excludes held objects. V_ref = V & dilate(best-matching detection), when the
    detection matches V (IoU >= min_iou); otherwise V."""
    out = {}
    for p in pids:
        V = lab == p
        best, bi = None, 0.0
        for d in dets:
            inter = np.count_nonzero(V & d)
            if inter == 0:
                continue
            iou = inter / np.count_nonzero(V | d)
            if iou > bi:
                best, bi = d, iou
        if best is not None and bi >= min_iou and REFINE:
            # the tracked mask can also MISS visible body parts (a head, an arm): detection pixels that carry no
            # other person's label are added
            out[p] = (V & (cv2.dilate(best.astype(np.uint8), K3) > 0)) | (best & ((lab == 0) | V))
        else:
            out[p] = V
    return out


REFINE = os.environ.get("R3_REFINE", "1") == "1"
REFINE_DEPTH = True


def compute(run, cache):
    p_out = f"{cache}/evidence.pkl"
    if os.path.exists(p_out):
        return pickle.load(open(p_out, "rb"))
    c = load(cache)
    D_all, L, pids, has, foc = c["D"], c["L"], list(c["pids"]), c["has"], c["foc"]
    Zall = np.load(f"{cache}/moge.npz")["Z"]
    masks, fr, score, inV = load_cands(cache)
    track = link(masks, fr) if len(masks) else np.zeros(0, int)
    T, H, W = L.shape
    dets = load_person_dets(cache, T, H, W)
    Vr_all = {}
    per = {}      # (i, pid) -> dict(s, sig, A, parts)
    units = []    # rows
    for i in range(T):
        Zm = Zall[i].astype(np.float32)
        lab = L[i]
        Vr = refine_visible(lab, pids, dets[i])
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
                # a pixel leaves the visible mask only if the scene there is clearly IN FRONT of p's mesh;
                # pixels the detector missed but whose depth agrees with the body (head, arms) stay visible
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
        # other people's (refined) visible masks are occluder candidates too: track id = -pid, ci = -1
        objs += [(-1, -int(q), Vr[q], False, 1.0, int(q)) for q in pids if Vr[q].sum() >= 15]
        for ci, tr, C, civ, csc, qperson in objs:
            Cc = cv2.erode(C.astype(np.uint8), K3) > 0
            # which person is this segment? (mostly one person's visible mask -> that person, not an object)
            ov_people = {p: int((C & Vr[p]).sum()) for p in pids}
            carea = int(C.sum())
            for p, D in Dz.items():
                if qperson == p:
                    continue
                info = per[(i, p)]
                M = D > 0
                ovl = C & M
                if ovl.sum() < 15:
                    continue
                V = Vr[p]
                if not np.isnan(info["s"]):
                    r = D - info["s"] * Zm
                    ringR = max(5, int(round(0.08 * np.sqrt(info["Marea"]))))
                    ring = (cv2.dilate(C.astype(np.uint8), np.ones((2 * ringR + 1,) * 2, np.uint8)) > 0) & ~C & info["A"]
                    anchor = float(np.median(r[ring])) if ring.sum() >= 20 else 0.0
                else:
                    r = None
                    anchor = 0.0
                mdq = np.nan
                if qperson is not None and qperson in Dz:
                    both = ovl & (Dz[qperson] > 0)
                    if both.sum() >= 10:
                        mdq = float(np.median(D[both] - Dz[qperson][both]))
                for part in np.unique(info["parts"][ovl]):
                    u = ovl & (info["parts"] == part)
                    if u.sum() < 15:
                        continue
                    core = u & Cc & (Zm > 0)
                    nV = int((u & V).sum())
                    nvalO = int((u & ~V & (Zm > 0)).sum())
                    a = int((u & ~V).sum())
                    h = int((u & info["E0"]).sum())
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
    out = dict(per={k: dict(s=v["s"], sig=v["sig"], dv=v["dv"], Marea=v["Marea"], Varea=v["Varea"],
                            parts=v["parts"], A=np.packbits(v["A"]), E0=np.packbits(v["E0"]), V=np.packbits(v["V"]))
                    for k, v in per.items()}, Vr=Vr_all, units=units, track=track, pids=pids, T=T, H=H, W=W)
    pickle.dump(out, open(p_out, "wb"))
    return out


if __name__ == "__main__":
    import time
    for name in sys.argv[1:]:
        t = time.time()
        ev = compute(Run(name), f"{ME}/cache/{name}")
        print(name, len(ev["units"]), "units", round(time.time() - t, 1), "s")


def ground_mask(Z, f, lab, cos_h=0.82, tol=0.06):
    """Pixels on the ground plane (the surface everybody stands/lies on), from MoGe depth alone.
    Back-project, normals from depth gradients; 'up' = median normal of the roughly upward-facing pixels (camera -y
    within 40 deg); among non-person pixels facing up (within ~35 deg), the ground height is the lowest well-populated
    height mode; ground = up-facing non-person pixels within tol (relative to depth) of that height."""
    H, W = Z.shape
    valid = Z > 0
    if valid.sum() < 100:
        return np.zeros((H, W), bool)
    Zs = cv2.GaussianBlur(np.where(valid, Z, 0).astype(np.float32), (0, 0), 1.5)
    u, v = np.meshgrid(np.arange(W) + 0.5 - W / 2, np.arange(H) + 0.5 - H / 2)
    P = np.stack([u * Zs / f, v * Zs / f, Zs], -1)
    dx = np.zeros_like(P)
    dy = np.zeros_like(P)
    dx[:, 1:-1] = P[:, 2:] - P[:, :-2]
    dy[1:-1] = P[2:] - P[:-2]
    n = np.cross(dx, dy)
    nn = np.linalg.norm(n, axis=-1, keepdims=True)
    n = n / np.maximum(nn, 1e-9)
    n[n[..., 1] > 0] *= -1                       # orient towards camera-up (-y)
    free = valid & (lab == 0)
    cand = free & (-n[..., 1] > 0.77)
    if cand.sum() < 200:
        return np.zeros((H, W), bool)
    up = np.median(n[cand], 0)
    up /= np.linalg.norm(up)
    hz = cand & ((n @ up) > cos_h)
    if hz.sum() < 200:
        return np.zeros((H, W), bool)
    h = (P @ up) / np.maximum(Zs, 1e-6)          # height relative to depth (scale-free per pixel)
    hh = (P @ up)[hz]
    lo, hi = np.percentile(hh, 1), np.percentile(hh, 99)
    hist, e = np.histogram(hh, bins=60, range=(lo, hi + 1e-6))
    thr = 0.05 * hist.max()
    j = int(np.nonzero(hist >= thr)[0][0])      # lowest well-populated height bin
    # refine: median of heights within that bin and its neighbours
    sel = (hh >= e[max(j - 1, 0)]) & (hh <= e[min(j + 2, 60)])
    g = float(np.median(hh[sel]))
    ht = P @ up
    gm = hz & (np.abs(ht - g) < tol + 0.02 * Zs)
    # the ground is a large coherent surface: keep big components only; no large ground -> no ground
    nl, cc, st, _ = cv2.connectedComponentsWithStats(gm.astype(np.uint8), connectivity=8)
    keep = np.zeros(nl, bool)
    keep[1:] = st[1:, 4] >= 0.005 * H * W
    gm = keep[cc]
    if gm.sum() < 0.03 * H * W:
        return np.zeros((H, W), bool)
    # fill small holes (lines, texture) inside the ground region, but never over people
    gm = cv2.morphologyEx(gm.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)) > 0
    return gm & (lab == 0)
