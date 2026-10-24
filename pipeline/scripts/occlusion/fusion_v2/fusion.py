"""R3_fusion decision + rendering (CPU).

Decision unit = (track, person, body part).  Over the clip:
  D-evidence: z = weighted median over frames of  m / sigma_p(i)   (m = depth margin of the segment core in p's frame,
              locally anchored or global, see P["DMODE"]); only frames whose segment core has >= NCORE pixels count.
  S-evidence: R = sum h / sum a  (structural evidence ratio, C_object_layers r2), H = sum h.
  Fusion:  D decides where it is confident:   z >= ZF -> front;  z <= -ZB -> behind (vetoes S);
           otherwise (|z| small, or no usable D core e.g. thin objects) S decides: R >= RMIN and H >= HMIN -> front.
Rendering of a front unit: the part of the unit outside p's visible mask is hidden; inside the visible mask only the
pixels whose own depth residual says "in front" (r - anchor > TIN * sigma) are hidden (SAM3's visible mask can
contain the occluder, but most of it is the body).
People: another person q's visible pixels inside p's mesh are hidden when q is in front of p (mesh depths; rule P of
J_figure_ground).
"""
import os
import sys

import cv2
import numpy as np

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ME)

P = dict(PIXE0=1, OWNMAX=0.3, OTHERMAX=0.5, ZS=0.5, DMODE="loc", NCORE=10, ZF=2.0, ZB=1.0, RMIN=0.35, HMIN=30, HMIN_P=10, INV=0, ZFI=3.0, COVMAX=0.5, TIN=1.0,
         PIX=1, KPIX=3.0, MPIX=0.10, PIXCOMP=0.01, CLOSE=5, PEOPLE=1, TSMOOTH=2, GRASP=1, GRASP_R=0.12, GROUND=1, PIXCHK=1, PIXCHK_K=1.0, BANDVETO=0, BAND_K=1.0, BAND_SW=0.2)


def decide(ev, P=P):
    """-> dict key -> (front, why, z, R, H).
    objects: key (track, pid, part, 'o'), decided once over the clip (track-level vote).
    people : key (-q, pid, part, 'p', frame), decided per frame (people move relative to each other)."""
    agg = {}
    loc = P["DMODE"] != "glob"
    for u in ev["units"]:
        sig = u["sig"]
        if u["a"] < 1 or (not np.isfinite(sig) and u.get("person") is None):
            continue
        anc = u["anchor"] if loc else 0.0
        if u.get("person") is not None:
            k = (u["tr"], u["pid"], u["part"], "p", u["i"])
        elif u["own"] <= P["OWNMAX"] and u["other"] <= P["OTHERMAX"]:
            k = (u["tr"], u["pid"], u["part"], "o")
        else:
            continue
        g = agg.setdefault(k, dict(z=[], w=[], h=0, a=0, mdq=[], nval=0))
        g["nval"] += u.get("nvalO", u["a"])
        if u["ncoreO"] >= P["NCORE"] and np.isfinite(u["mgo"]) and np.isfinite(sig):
            g["z"].append((u["mgo"] - anc) / sig)
            g["w"].append(u["ncoreO"])
        g["h"] += u["h"]
        g["a"] += u["a"]
        if np.isfinite(u.get("mdq", np.nan)):
            g["mdq"].append(u["mdq"])
    out = {}
    for k, g in agg.items():
        z = wmedian(np.array(g["z"]), np.array(g["w"])) if g["z"] else np.nan
        R = g["h"] / g["a"] if g["a"] else 0.0
        hmin = P["HMIN"] if k[3] == "o" else P["HMIN_P"]
        if g["a"] >= 15 and g["nval"] < 0.5 * g["a"]:
            res = (False, "Dinf")   # mostly no valid depth = sky / infinitely far: cannot be in front of anybody
        elif np.isfinite(z) and z >= P["ZF"]:
            res = (True, "D+")
        elif np.isfinite(z) and z <= -P["ZB"]:
            res = (False, "D-")
        elif R >= P["RMIN"] and g["h"] >= hmin and not (np.isfinite(z) and z < P["ZS"]):
            res = (True, "S+")
        elif k[3] == "p" and P["PEOPLE"] and g["mdq"] and np.median(g["mdq"]) > 0:
            res = (True, "M+")   # no usable depth or structure: fall back to the meshes' own order
        else:
            res = (False, "S-")
        out[k] = res + (z, R, g["h"])
    return out


HAND_KP = (41, 62, 32, 53)   # MHR70: right wrist, left wrist, right hand (middle MCP), left hand


def grasp_veto(hide, kp, f, P, V=None, dv=0):
    """Hand-object contact: where the person's own hand touches an object, the depth margin is a tie (the object is
    within a hand's thickness of the hand, and hand meshes are the least accurate part) and the structural cue fails
    (fingers make the visible mask concave, so 'body on both sides' fires). Evidence that lives ONLY inside the grasp
    zone (disk of GRASP_R metres around the person's wrist/hand keypoints) is therefore not trusted: a hide component
    that lies entirely inside the zone is dropped. Occluders that also cover the body beyond the hand (a plate over
    hand and forearm, a guitar under the strumming hand) form larger components and are kept, in-zone part included."""
    hb = hide >= 0.5
    if not hb.any():
        return hide
    H, W = hide.shape
    zone = np.zeros((H, W), np.uint8)
    for j in HAND_KP:
        X = kp[j]
        if not np.all(np.isfinite(X)) or X[2] <= 0:
            continue
        u, v = f * X[0] / X[2] + W / 2, f * X[1] / X[2] + H / 2
        r = int(round(P["GRASP_R"] * f / X[2]))
        if -r <= u < W + r and -r <= v < H + r:
            cv2.circle(zone, (int(round(u)), int(round(v))), max(r, 2), 1, -1)
    if not zone.any():
        return hide
    n, lab = cv2.connectedComponents(hb.astype(np.uint8), connectivity=8)
    out = hide.copy()
    inside = np.bincount(lab[(zone > 0) & hb], minlength=n)
    total = np.bincount(lab[hb], minlength=n)
    for j in range(1, n):
        if total[j] and inside[j] == total[j]:
            out[lab == j] = 0
    # inside the grasp zone, next to the VISIBLE hand (within the misfit band dv), the mesh hand overlapping the
    # gripped object is a fit error, not an occlusion (fingers on a fretboard, a hand around a bar)
    if P.get("GRASP_BAND", 0) and V is not None and V.any() and dv > 0:
        distV = cv2.distanceTransform((~V).astype(np.uint8), cv2.DIST_L2, 3)
        out[(zone > 0) & (distV <= dv)] = 0
    return out


def band_veto(hide, V, M, dv, P):
    """Misfit bands must not be hidden: drop every hide component that lies entirely within the misfit band of the
    visible body (max distance to the visible mask <= dv) and is not sandwiched by the visible body (body on both
    sides = the T-junction / continuation cue of thin occluders such as a bar across the neck). In the band, a depth
    margin is not trusted (MoGe edge bleeding, pose error, hand-object contact)."""
    from evidence import sandwiched
    hb = hide >= 0.5
    if not hb.any():
        return hide
    distV = cv2.distanceTransform((~V).astype(np.uint8), cv2.DIST_L2, 3)
    sw = sandwiched(V, 2 * dv) & ~V
    n, lab = cv2.connectedComponents(hb.astype(np.uint8), connectivity=8)
    out = hide.copy()
    for j in range(1, n):
        c = lab == j
        if distV[c].max() <= P["BAND_K"] * dv and sw[c].mean() < P["BAND_SW"]:
            out[c] = 0
    return out


def unpack(b, H, W):
    return np.unpackbits(b)[:H * W].reshape(H, W).astype(bool)


def wmedian(x, w):
    o = np.argsort(x)
    x, w = x[o], w[o]
    c = np.cumsum(w)
    return float(x[np.searchsorted(c, 0.5 * c[-1])])


def render(run, ev, cache, dec, out_dir, P=P, save=True):
    from prep import load
    from gpu_stage import load_cands
    from r3common import save_hide
    c = load(cache)
    D_all, L, pids, has = c["D"], c["L"], list(c["pids"]), c["has"]
    Zall = np.load(f"{cache}/moge.npz")["Z"]
    masks, fr, _, _ = load_cands(cache)
    T, H, W = L.shape
    byf = {}
    for u in ev["units"]:
        byf.setdefault((u["i"], u["pid"]), []).append(u)
    res = {}
    for i in range(T):
        lab = L[i]
        Zm = Zall[i].astype(np.float32)
        gnd = None
        if P["GROUND"]:
            from evidence import ground_mask
            fi = c["foc"][i][c["foc"][i] > 0]
            if len(fi):
                gnd = ground_mask(Zm, float(fi[0]), lab)
        for k, p in enumerate(pids):
            hide = np.zeros((H, W), np.float32)
            if has[i, k]:
                D = D_all[i, k].astype(np.float32)
                M = D > 0
                info = ev["per"][(i, p)]
                V = unpack(info["V"], H, W)
                parts = info["parts"]
                r = D - info["s"] * Zm if np.isfinite(info["s"]) else None
                for u in byf.get((i, p), []):
                    if u.get("person") is not None:
                        d = dec.get((u["tr"], u["pid"], u["part"], "p", i))
                        if d is not None and d[0]:
                            C = unpack(ev["Vr"][(i, u["person"])], H, W)
                            hide[C & M & (parts == u["part"]) & ~V] = 1
                        continue
                    d = dec.get((u["tr"], u["pid"], u["part"], "o"))
                    if d is not None and d[0]:
                        reg = masks[u["ci"]] & M & (parts == u["part"]) & ~V
                        if gnd is not None:
                            reg &= ~gnd
                        if P["PIXCHK"] and d[1] == "D+" and r is not None:
                            # a depth-decided segment hides only the pixels whose own depth agrees: a segment that
                            # also covers the (visible) body - head next to a chain - must not hide the body
                            anc = u["anchor"] if P["DMODE"] != "glob" else 0.0
                            reg &= (r - anc > P["PIXCHK_K"] * info["sig"]) | (Zm <= 0)
                        hide[reg] = 1
                    d = dec.get((u["tr"], u["pid"], u["part"], "i"))
                    if d is not None and d[0] and r is not None:
                        reg = masks[u["ci"]] & M & (parts == u["part"])
                        thr = u["anchor"] * (P["DMODE"] != "glob") + P["TIN"] * info["sig"]
                        hide[reg & V & (r > thr)] = 1
                if P["PIX"] and r is not None:
                    pm = M & ~V & (Zm > 0) & (r > max(P["KPIX"] * info["sig"], P["MPIX"]))
                    if P["PIXE0"]:
                        # weakest evidence (no segment): only outside the misfit band, never on the ground,
                        # and only as a spatially coherent region (>= PIXCOMP of the mesh area)
                        distV = cv2.distanceTransform((~V).astype(np.uint8), cv2.DIST_L2, 3) if V.any()                             else np.full(V.shape, 1e9, np.float32)
                        pm &= distV > info["dv"]
                    if gnd is not None:
                        pm &= ~gnd
                    if pm.any():
                        nl, cc, st, _ = cv2.connectedComponentsWithStats(pm.astype(np.uint8), connectivity=8)
                        amin = P["PIXCOMP"] * M.sum()
                        pm = (np.arange(nl) > 0)[cc] & (st[cc, 4] >= amin)
                    hide[pm] = 1
                if P["CLOSE"]:
                    hide = cv2.morphologyEx(hide, cv2.MORPH_CLOSE, np.ones((P["CLOSE"],) * 2, np.uint8))
                if P["GRASP"]:
                    hide = grasp_veto(hide, c["KP"][i, k], float(c["foc"][i, k]), P, V, info["dv"])
                if P["BANDVETO"] and V.any():
                    hide = band_veto(hide, V, M, ev["per"][(i, p)]["dv"], P)
                hide = cv2.dilate(hide, np.ones((3, 3), np.uint8))
                hide *= cv2.dilate(M.astype(np.uint8), np.ones((3, 3), np.uint8))
            res[(i, p)] = hide
    if P["TSMOOTH"] == 1:
        res = temporal_smooth(run, res, D_all, pids, has, c["foc"])
    elif P["TSMOOTH"] == 2:
        res = temporal_smooth_img(res, D_all, pids, has)
    if save:
        for (i, p), hide in res.items():
            save_hide(out_dir, run, p, i, hide)
    return res


def temporal_smooth_img(res, D_all, pids, has):
    """image-space majority over t-1, t, t+1 (ties keep frame t), inside the mesh silhouette of frame t."""
    T = D_all.shape[0]
    out = dict(res)
    for k, p in enumerate(pids):
        for i in range(T):
            if not has[i, k]:
                continue
            cur = res[(i, p)] >= 0.5
            votes = cur.astype(np.int32)
            n = 1
            for j in (i - 1, i + 1):
                if (j, p) in res and 0 <= j < T and has[j, k]:
                    votes += res[(j, p)] >= 0.5
                    n += 1
            M = D_all[i, k] > 0
            h = np.where(votes * 2 > n, 1.0, np.where(votes * 2 < n, 0.0, cur.astype(np.float32))).astype(np.float32)
            h[~M] = res[(i, p)][~M]
            out[(i, p)] = h
    return out


def _front_vertices(V, f, depth):
    """indices and pixel coords of the vertices that are visible (front-most) in this frame."""
    H, W = depth.shape
    u = f * V[:, 0] / V[:, 2] + W / 2
    v = f * V[:, 1] / V[:, 2] + H / 2
    px, py = np.floor(u).astype(int), np.floor(v).astype(int)
    ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
    ok[ok] &= np.abs(V[ok, 2] - depth[py[ok], px[ok]]) < 0.03
    return ok, px, py


def temporal_smooth(run, res, D_all, pids, has, foc):
    """Body-attached temporal majority over frames t-1, t, t+1: every mesh pixel of frame t is tied to its nearest
    visible mesh vertex; that vertex's hide value in the neighbouring frames is read at its projection there (only if
    the vertex is visible there too). hide_t = mean of the available samples (self included) >= 0.5.
    Removes single-frame flips of the decision while following the body's motion."""
    from scipy.spatial import cKDTree
    T = D_all.shape[0]
    out = dict(res)
    for k, p in enumerate(pids):
        info = {}
        for i in range(T):
            if not has[i, k]:
                continue
            m = run.mesh(p, i)
            D = D_all[i, k].astype(np.float32)
            info[i] = (_front_vertices(m[0], float(foc[i, k]), D), D > 0)
        for i in range(T):
            if i not in info or res[(i, p)].max() == 0 and all(
                    res.get((j, p), np.zeros(1)).max() == 0 for j in (i - 1, i + 1)):
                continue
            (ok, px, py), M = info[i]
            if ok.sum() == 0:
                continue
            idx = np.nonzero(ok)[0]
            tree = cKDTree(np.stack([px[idx], py[idx]], 1))
            ys, xs = np.nonzero(M)
            _, nn = tree.query(np.stack([xs, ys], 1))
            vid = idx[nn]
            acc = (res[(i, p)][ys, xs] >= 0.5).astype(np.float32)
            cnt = np.ones(len(ys), np.float32)
            for j in (i - 1, i + 1):
                if j not in info:
                    continue
                (okj, pxj, pyj), Mj = info[j]
                vis = okj[vid]
                hj = res[(j, p)]
                acc[vis] += hj[pyj[vid[vis]], pxj[vid[vis]]] >= 0.5
                cnt[vis] += 1
            h = np.zeros_like(res[(i, p)])
            fr_ = acc / cnt
            h[ys, xs] = np.where(fr_ > 0.5, 1.0, np.where(fr_ < 0.5, 0.0, res[(i, p)][ys, xs] >= 0.5)).astype(np.float32)
            # keep pixels outside the mesh silhouette (1 px dilation band) as they were
            h[~M] = res[(i, p)][~M]
            out[(i, p)] = h
    return out
