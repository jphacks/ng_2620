"""Erase the target people from the frames (so that only their avatars remain): temporal clean plate.

Condensed from virtual-vlog S07 `TemporalCleanPlate` (github.com/Akatuki25/virtual-vlog @ a5650f3,
src/vlogvrm/stages/s07_removal.py):
  * the hole of frame t = the targets' visible masks, dilated
  * background motion between frames = homographies of background corners (people excluded): adjacent pairs by KLT,
    chained to give H(u -> t) as the initial guess, then refined by LK on u's corners + RANSAC (virtual-vlog's
    "chain_lk"; it keeps the chained H when LK fails instead of dropping the reference frame)
  * the hole is filled with the per-pixel median of the other frames' warped pixels where those frames show
    background (their own dilated person masks warped and excluded); median by uint16 sort with a sentinel
    (virtual-vlog _masked_median_u8, ~5x faster than np.nanmedian)
  * windows in seconds, not frames (virtual-vlog PITFALLS: a frame-count span changes meaning with the fps)
  * pixels no other frame ever shows (the person never moves away) fall back to cv2.inpaint (Telea)
"""

from __future__ import annotations

import cv2
import numpy as np


def _masked_median_u8(vals: np.ndarray, oks: np.ndarray):
    v = vals.astype(np.uint16)
    v[~oks] = 1000
    v.sort(axis=0)
    c = oks.sum(axis=0)
    lo = np.maximum((c - 1) // 2, 0)
    hi = c // 2
    a = np.take_along_axis(v, np.broadcast_to(lo[:, None], (len(lo), 3))[None], axis=0)[0]
    b = np.take_along_axis(v, np.broadcast_to(hi[:, None], (len(hi), 3))[None], axis=0)[0]
    return (a.astype(np.float32) + b.astype(np.float32)) / 2, c > 0


def _corners(gray, bg, n=600):
    c = cv2.goodFeaturesToTrack(gray, 2000, 0.01, 5, mask=bg, blockSize=7)
    if c is not None and len(c) > n:
        c = c[np.linspace(0, len(c) - 1, n).astype(int)]
    return c


LK = dict(winSize=(15, 15), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.01))


def _homography(ga, gb, pa, init=None, min_match=25):
    """H(a -> b) from a's corners tracked into b (forward-backward checked) or None."""
    if pa is None or len(pa) < min_match:
        return None
    if init is not None:
        pred = cv2.perspectiveTransform(pa, init)
        pb, st, _ = cv2.calcOpticalFlowPyrLK(ga, gb, pa, pred.copy(), flags=cv2.OPTFLOW_USE_INITIAL_FLOW, **LK)
    else:
        pb, st, _ = cv2.calcOpticalFlowPyrLK(ga, gb, pa, None, **LK)
    pa2, st2, _ = cv2.calcOpticalFlowPyrLK(gb, ga, pb, pa.copy(), flags=cv2.OPTFLOW_USE_INITIAL_FLOW, **LK)
    good = (st[:, 0] == 1) & (st2[:, 0] == 1) & (np.linalg.norm((pa - pa2)[:, 0], axis=1) < 1.0)
    if good.sum() < min_match:
        return None
    Hm, inl = cv2.findHomography(pa[good], pb[good], cv2.RANSAC, 3.0)
    if Hm is None or inl is None or int(inl.sum()) < min_match:
        return None
    return Hm


def clean_plates(frames, person_masks, fps=30.0, dilate=9, exclude_dilate=15, span_sec=7.5, stride_sec=8 / 30,
                 log=print):
    """frames: list of BGR uint8; person_masks: list of bool (H,W), the targets' visible masks.
    -> (list of BGR uint8 frames with the targets erased, list of bool hole masks, stats)."""
    n = len(frames)
    H, W = frames[0].shape[:2]
    k_hole = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate))
    k_ex = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (exclude_dilate, exclude_dilate))
    holes = [cv2.dilate(m.astype(np.uint8), k_hole) > 0 for m in person_masks]
    excl = [cv2.dilate(m.astype(np.uint8), k_ex) for m in person_masks]
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    pts = [_corners(g, ((1 - e) * 255).astype(np.uint8)) for g, e in zip(grays, excl)]

    # chained adjacent homographies: C[t] maps frame 0 -> frame t
    C = np.tile(np.eye(3), (n, 1, 1))
    for t in range(1, n):
        Ha = _homography(grays[t - 1], grays[t], pts[t - 1])
        C[t] = (Ha if Ha is not None else np.eye(3)) @ C[t - 1]
        C[t] /= C[t][2, 2]

    span = max(1, int(round(span_sec * fps)))
    stride = max(1, int(round(stride_sec * fps)))
    out, filled_by_plate, filled_by_inpaint = [], 0, 0
    for t in range(n):
        hole = holes[t]
        img = frames[t].copy()
        if not hole.any():
            out.append(img)
            continue
        ys, xs = np.nonzero(hole)
        cands = [u for u in range(max(0, t - span), min(n, t + span + 1), stride) if u != t]
        vals = np.zeros((len(cands), len(ys), 3), np.uint8)
        oks = np.zeros((len(cands), len(ys)), bool)
        for k, u in enumerate(cands):
            Hc = C[t] @ np.linalg.inv(C[u])
            Hc /= Hc[2, 2]
            Hm = _homography(grays[u], grays[t], pts[u], init=Hc)
            Hm = Hc if Hm is None else Hm
            wf = cv2.warpPerspective(frames[u], Hm, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            # valid where u shows background: not u's (dilated) people, and inside u's image
            bad = cv2.warpPerspective(excl[u], Hm, (W, H), flags=cv2.INTER_NEAREST, borderValue=1)
            vals[k] = wf[ys, xs]
            oks[k] = bad[ys, xs] == 0
        if len(cands):
            med, ok = _masked_median_u8(vals, oks)
            img[ys[ok], xs[ok]] = np.clip(med[ok] + 0.5, 0, 255).astype(np.uint8)
        else:
            ok = np.zeros(len(ys), bool)
        rest = np.zeros((H, W), np.uint8)
        rest[ys[~ok], xs[~ok]] = 255
        if rest.any():
            img = cv2.inpaint(img, rest, 5, cv2.INPAINT_TELEA)
        filled_by_plate += int(ok.sum())
        filled_by_inpaint += int((~ok).sum())
        out.append(img)
    tot = max(filled_by_plate + filled_by_inpaint, 1)
    stats = {"hole_px": tot, "clean_plate_%": round(100 * filled_by_plate / tot, 1),
             "inpaint_%": round(100 * filled_by_inpaint / tot, 1)}
    log(f"[removal] hole pixels filled from other frames {stats['clean_plate_%']}%, inpainted {stats['inpaint_%']}%")
    return out, holes, stats
