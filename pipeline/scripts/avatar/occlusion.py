"""Occlusion of the avatars by the real scene, from the R3_fusion v2 decisions.

fusion v2 (scripts/occlusion/fusion_v2) decides, per (object track, person, body part) over the clip, whether a real
object is in front of the person, using the SAM-3D-Body mesh as the person proxy, and renders hide masks inside the
mesh silhouette M. An avatar covers a different region A (bigger head, hair, skirt; thinner limbs), so the hide mask
is re-rendered over A with the same frozen decisions and rules (fusion.render, line for line), changing only:
  * region:  A instead of M. Inside A & M the residual uses the mesh depth (as validated); in A \\ M it uses the
             avatar depth, and the body part of a pixel is that of the nearest mesh pixel.
  * people:  the person-person rule (hide p where another target q's real body is in front) is dropped, and no
             hide is kept on another target's visible mask (an object segment may overlap it). The real target bodies
             are erased from the frame, so between targets only the avatars' z-buffer decides.
With A = M this reproduces fusion.render without the people rule (checked by check_equivalence()).
"""

import os
import sys

import cv2
import numpy as np

FUSION_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "occlusion", "fusion_v2")
if FUSION_DIR not in sys.path:
    sys.path.insert(0, FUSION_DIR)


def _nearest_parts(parts, M):
    """Part label of the nearest mesh pixel, for every pixel."""
    if not M.any():
        return parts
    from scipy.ndimage import distance_transform_edt
    # Only the nearest-pixel indices are used; do not compute the distance map.
    iy, ix = distance_transform_edt(~M, return_distances=False, return_indices=True)
    return parts[iy, ix]


def hide_frame(i, p, k, c, ev, byf, dec, masks, Zm, gnd, A, DA, P, people=False, real=(), override=None):
    """Hide mask over the avatar region A of target p (index k) in frame i. c: prep cache, DA: avatar depth (inf =
    empty). Mirrors fusion.render's per-(i, p) body. real: people who are not replaced (their bodies stay in the
    frame), for whom the person-person rule applies even with people=False. override(track, pid, i) -> "front" |
    "behind" | None: the editor's manual correction of the object decision (front hides the whole segment)."""
    from fusion import grasp_veto, unpack
    H, W = A.shape
    hide = np.zeros((H, W), np.float32)
    if not c["has"][i, k] or not A.any():
        return hide
    D = c["D"][i, k].astype(np.float32)
    M = D > 0
    info = ev["per"][(i, p)]
    V = unpack(info["V"], H, W)
    parts = _nearest_parts(info["parts"], M)
    # depth used for the residual: mesh inside its silhouette (validated), avatar outside
    Dx = np.where(M, D, np.where(np.isfinite(DA), DA, 0.0)).astype(np.float32)
    R = A                                   # region (fusion.render: M)
    r = Dx - info["s"] * Zm if np.isfinite(info["s"]) else None
    for u in byf.get((i, p), []):
        if u.get("person") is not None:
            if people or u["person"] in real:
                d = dec.get((u["tr"], u["pid"], u["part"], "p", i))
                if d is not None and d[0]:
                    C = unpack(ev["Vr"][(i, u["person"])], H, W)
                    hide[C & R & (parts == u["part"]) & ~V] = 1
            continue
        d = dec.get((u["tr"], u["pid"], u["part"], "o"))
        m = override(u["tr"], u["pid"], i) if override is not None else None
        if m is not None:
            d = (m == "front", "OVR") + (tuple(d[2:]) if d is not None else (np.nan, 0.0, 0))
        if d is not None and d[0]:
            reg = masks[u["ci"]] & R & (parts == u["part"]) & ~V
            if gnd is not None:
                reg &= ~gnd
            if P["PIXCHK"] and d[1] == "D+" and r is not None:
                anc = u["anchor"] if P["DMODE"] != "glob" else 0.0
                reg &= (r - anc > P["PIXCHK_K"] * info["sig"]) | (Zm <= 0)
            hide[reg] = 1
        d = dec.get((u["tr"], u["pid"], u["part"], "i"))
        if d is not None and d[0] and r is not None:
            reg = masks[u["ci"]] & R & (parts == u["part"])
            thr = u["anchor"] * (P["DMODE"] != "glob") + P["TIN"] * info["sig"]
            hide[reg & V & (r > thr)] = 1
    if P["PIX"] and r is not None:
        pm = R & ~V & (Zm > 0) & (Dx > 0) & (r > max(P["KPIX"] * info["sig"], P["MPIX"]))
        if P["PIXE0"]:
            distV = cv2.distanceTransform((~V).astype(np.uint8), cv2.DIST_L2, 3) if V.any() \
                else np.full(V.shape, 1e9, np.float32)
            pm &= distV > info["dv"]
        if gnd is not None:
            pm &= ~gnd
        if pm.any():
            nl, cc, st, _ = cv2.connectedComponentsWithStats(pm.astype(np.uint8), connectivity=8)
            amin = P["PIXCOMP"] * M.sum()           # same component threshold as for the mesh
            pm = (np.arange(nl) > 0)[cc] & (st[cc, 4] >= amin)
        hide[pm] = 1
    if P["CLOSE"]:
        hide = cv2.morphologyEx(hide, cv2.MORPH_CLOSE, np.ones((P["CLOSE"],) * 2, np.uint8))
    if P["GRASP"]:
        hide = grasp_veto(hide, c["KP"][i, k], float(c["foc"][i, k]), P, V, info["dv"])
    hide = cv2.dilate(hide, np.ones((3, 3), np.uint8))
    hide *= cv2.dilate(R.astype(np.uint8), np.ones((3, 3), np.uint8))
    return hide


class AvatarHider:
    """Per-frame hide masks over the avatars (streaming form of avatar_hides: raw masks of frame i need only frame i;
    the TSMOOTH=2 majority needs the raw masks of i-1, i, i+1)."""

    def __init__(self, fus, people=False, real=(), overrides=None):
        from fusion import P
        from gpu_stage import load_cands
        from prep import load
        from shared_cache import moge_z
        self.P, self.people, self.real = P, people, set(real)
        self.overrides = overrides     # Overrides or None
        self.ev, self.dec, cache = fus["ev"], fus["dec"], fus["cache"]
        self.c = load(cache)
        self.pids = list(self.c["pids"])
        self.Zall = moge_z(cache)   # memory-mapped under shared_cache.install()
        self.masks, _, _, _ = load_cands(cache)
        self.byf = {}
        for u in self.ev["units"]:
            self.byf.setdefault((u["i"], u["pid"]), []).append(u)

    def raw(self, i, alpha, depth):
        """alpha / depth: {pid: (H,W)} of frame i (alpha 0..1, depth inf where empty) -> {pid: (hide, region)}."""
        from evidence import ground_mask
        P, c = self.P, self.c
        Zm = self.Zall[i].astype(np.float32)
        gnd = None
        if P["GROUND"]:
            fi = c["foc"][i][c["foc"][i] > 0]
            if len(fi):
                gnd = ground_mask(Zm, float(fi[0]), c["L"][i])
        out = {}
        for k, p in enumerate(self.pids):
            a = alpha.get(p)
            if a is None:
                continue
            A = a > 0.02
            h = hide_frame(i, p, k, c, self.ev, self.byf, self.dec, self.masks, Zm, gnd, A, depth[p], P, self.people,
                           self.real, self.overrides.mode if self.overrides is not None else None)
            if not self.people:   # erased targets only: a real person's body in front keeps hiding the avatar
                others = np.isin(c["L"][i], [q for q in self.pids if q != p and q not in self.real])
                h[cv2.dilate(others.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0] = 0
            out[p] = (h, A)
        return out

    def smooth(self, p, prev, cur, nxt):
        """TSMOOTH=2 for person p: cur / prev / nxt are raw() results of frames i, i-1, i+1 (None if absent)."""
        h, R = cur[p]
        if self.P["TSMOOTH"] != 2:
            return h
        c0 = h >= 0.5
        votes, n = c0.astype(np.int32), 1
        for nb in (prev, nxt):
            if nb is not None and p in nb:
                votes += nb[p][0] >= 0.5
                n += 1
        sm = np.where(votes * 2 > n, 1.0, np.where(votes * 2 < n, 0.0, c0.astype(np.float32))).astype(np.float32)
        sm[~R] = h[~R]
        return sm


class Overrides:
    """The editor's unit corrections, expanded by the server to fusion tracks (designdocs/architecture/
    pipeline-integration §3.6): {"items": [{"personId", "tracks": [...], "range": [f0, f1], "mode": "front" |
    "behind"}, ...]} in the order they were made; the last one covering a frame wins."""

    def __init__(self, items):
        self.by = {}
        for it in items:
            for tr in it["tracks"]:
                self.by.setdefault((int(tr), int(it["personId"])), []).append(
                    (int(it["range"][0]), int(it["range"][1]), it["mode"]))

    @classmethod
    def load(cls, path):
        import json
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f)["items"])

    def mode(self, tr, pid, i):
        m = None
        for f0, f1, mode in self.by.get((int(tr), int(pid)), ()):
            if f0 <= i <= f1:
                m = mode
        return m


def avatar_hides(fus, avatar_alpha, avatar_depth, people=False):
    """fus: run_fusion_v2.run_fusion() result. avatar_alpha / avatar_depth: {(i, pid): (H,W)} (alpha 0..1, depth inf
    where empty). -> {(i, pid): hide 0..1}, temporally smoothed like fusion v2 (TSMOOTH=2: image-space majority over
    t-1, t, t+1 inside the region)."""
    hd = AvatarHider(fus, people)
    T = hd.c["L"].shape[0]
    raw = {}
    for i in range(T):
        al = {p: a for (j, p), a in avatar_alpha.items() if j == i}
        raw[i] = hd.raw(i, al, {p: avatar_depth[(i, p)] for p in al})
    return {(i, p): hd.smooth(p, raw.get(i - 1), raw[i], raw.get(i + 1)) for i in range(T) for p in raw[i]}


def check_equivalence(fus, frames=None):
    """With A = mesh silhouette and the people rule on, hide_frame must equal fusion.render (before smoothing)."""
    from evidence import ground_mask
    from fusion import P, render
    from gpu_stage import load_cands
    from prep import load
    run, ev, dec, cache = fus["run"], fus["ev"], fus["dec"], fus["cache"]
    P0 = dict(P, TSMOOTH=0)
    ref = render(run, ev, cache, dec, None, P0, save=False)
    c = load(cache)
    Zall = np.load(f"{cache}/moge.npz")["Z"]
    masks, _, _, _ = load_cands(cache)
    byf = {}
    for u in ev["units"]:
        byf.setdefault((u["i"], u["pid"]), []).append(u)
    worst = 0
    for i in (frames if frames is not None else range(len(run))):
        Zm = Zall[i].astype(np.float32)
        fi = c["foc"][i][c["foc"][i] > 0]
        gnd = ground_mask(Zm, float(fi[0]), c["L"][i]) if (P["GROUND"] and len(fi)) else None
        for k, p in enumerate(c["pids"]):
            M = c["D"][i, k] > 0
            DA = np.where(M, c["D"][i, k].astype(np.float32), np.inf)
            h = hide_frame(i, p, k, c, ev, byf, dec, masks, Zm, gnd, M, DA, P0, people=True)
            worst = max(worst, int(((h >= 0.5) != (ref[(i, p)] >= 0.5)).sum()))
    return worst
