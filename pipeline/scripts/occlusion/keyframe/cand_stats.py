"""Per-clip statistics of the fusion v2 SAM3 candidate stage, from existing caches (no GPU).

usage: python cand_stats.py <r3cache dir> <out.json> [clip ...]

Per clip:
  * prompt region (where gpu_stage puts points) vs image area and vs the targets' bbox union
  * frozen fine-grid point count per frame vs a whole-image grid
  * kept candidates per frame, tracks (evidence.link), track lengths
  * decisions (fusion.decide): which tracks are 'front' (used to hide), their share of all candidates
  * hide flicker before / after the 3-frame temporal majority (fusion.render with TSMOOTH 0 and 2)
  * new-occlusion events: frames where a hide component appears that does not touch the previous frame's hide
"""
import json
import os
import pickle
import sys
import time

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))).replace("\\", "/")
sys.path.insert(0, f"{REPO}/scripts/occlusion/fusion_v2")
import fusion  # noqa: E402
from gpu_stage import STRIDE_IN, STRIDE_V, STRIDE_OUT, DIL, grid_points  # noqa: E402


def bbox_union_frac(sil_list, H, W):
    m = np.zeros((H, W), bool)
    for s in sil_list:
        ys, xs = np.nonzero(s)
        if len(ys):
            m[ys.min():ys.max() + 1, xs.min():xs.max() + 1] = True
    return m.mean()


def flicker(res, pids, T):
    """isolated single-frame flips: pixels with h[t-1] == h[t+1] != h[t], over pixels hidden at t-1, t or t+1."""
    flips = tot = 0
    for p in pids:
        for t in range(1, T - 1):
            a, b, c = (res.get((t + d, p)) for d in (-1, 0, 1))
            if a is None or b is None or c is None:
                continue
            a, b, c = a >= 0.5, b >= 0.5, c >= 0.5
            flips += int(((a == c) & (b != a)).sum())
            tot += int((a | b | c).sum())
    return flips, tot


def events(res, pids, T, min_px=30):
    """frames (per person) where a hide component of >= min_px appears that does not touch dilate(hide[t-1], 5)."""
    out = 0
    frames = set()
    k5 = np.ones((11, 11), np.uint8)
    for p in pids:
        prev = None
        for t in range(T):
            h = res.get((t, p))
            if h is None:
                prev = None
                continue
            hb = (h >= 0.5).astype(np.uint8)
            if prev is not None:
                n, lab, st, _ = cv2.connectedComponentsWithStats(hb, connectivity=8)
                pd = cv2.dilate(prev, k5) > 0
                for j in range(1, n):
                    if st[j, 4] >= min_px and not pd[lab == j].any():
                        out += 1
                        frames.add(t)
                        break
            elif hb.sum() >= min_px:
                out += 1
                frames.add(t)
            prev = hb
    return out, len(frames)


def clip_stats(cache):
    z = np.load(f"{cache}/mesh.npz")
    D, L, pids, has = z["D"], z["L"], list(z["pids"]), z["has"]
    T, P, H, W = D.shape
    region_frac, bbox_frac, npts, nwhole, inner_frac = [], [], [], [], []
    for i in range(T):
        sil_p = [D[i, k] > 0 for k in range(P) if has[i, k]]
        sil = np.zeros((H, W), bool)
        for s in sil_p:
            sil |= s
        region = cv2.dilate(sil.astype(np.uint8), np.ones((2 * DIL + 1,) * 2, np.uint8)) > 0
        V = L[i] > 0
        Vd = cv2.dilate(V.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        inner = sil & ~Vd
        inv = sil & V & ~(cv2.dilate((~V).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
        n = len(grid_points(inner, STRIDE_IN)) + len(grid_points(inv, STRIDE_V, off=2)) + \
            len(grid_points(region & ~sil & ~Vd, STRIDE_OUT))
        npts.append(n)
        region_frac.append(region.mean())
        inner_frac.append(inner.sum() / max(sil.sum(), 1))
        reg_p = [cv2.dilate(s.astype(np.uint8), np.ones((2 * DIL + 1,) * 2, np.uint8)) > 0 for s in sil_p]
        bbox_frac.append(bbox_union_frac(reg_p, H, W))
        # whole image with the same strides: stride 4 where the frozen grid uses 4, else 8 -> lower bound: stride 8
        nwhole.append(len(range(STRIDE_OUT // 2, H, STRIDE_OUT)) * len(range(STRIDE_OUT // 2, W, STRIDE_OUT)))
    c = np.load(f"{cache}/cands.npz")
    fr = c["frame"]
    mode = str(c["mode"]) if "mode" in c.files else "full"
    ev = pickle.load(open(f"{cache}/evidence.pkl", "rb"))
    track = np.asarray(ev["track"])
    ids, cnt = np.unique(track, return_counts=True) if len(track) else (np.zeros(0), np.zeros(0))
    # track length in frames
    tlen = {}
    for t_id in ids:
        tlen[int(t_id)] = len(np.unique(fr[track == t_id]))
    lens = np.array(list(tlen.values())) if tlen else np.zeros(0)
    dec = fusion.decide(ev, fusion.P)
    front_tr = sorted({k[0] for k, v in dec.items() if k[3] == "o" and v[0]})
    front_tr_obj = [t for t in front_tr if t >= 0]
    n_front_cands = int(np.isin(track, front_tr_obj).sum()) if len(track) else 0
    why = {}
    for k, v in dec.items():
        if k[3] == "o" and v[0]:
            why[v[1]] = why.get(v[1], 0) + 1
    # tracks that overlap any person's mesh at all (have a unit): candidates considered by the decision
    unit_tr = {u["tr"] for u in ev["units"] if u.get("person") is None}
    # hide masks before / after the temporal majority
    t0 = time.time()
    P0 = dict(fusion.P, TSMOOTH=0)
    res0 = fusion.render(None, ev, cache, dec, None, P0, save=False)
    res2 = fusion.temporal_smooth_img(res0, D, pids, has)
    f0, n0 = flicker(res0, pids, T)
    f2, n2 = flicker(res2, pids, T)
    ev0, evf = events(res2, pids, T)
    hidden_frames = sum(1 for (i, p), h in res2.items() if (h >= 0.5).sum() >= 30)
    return dict(
        T=int(T), H=int(H), W=int(W), n_targets=int(P), mode=mode,
        region_frac_mean=float(np.mean(region_frac)), region_frac_max=float(np.max(region_frac)),
        bbox_frac_mean=float(np.mean(bbox_frac)), inner_over_sil_mean=float(np.mean(inner_frac)),
        pts_full_per_frame=float(np.mean(npts)), pts_whole_image_stride8=float(np.mean(nwhole)),
        cands_per_frame=float(len(fr) / T), n_cands=int(len(fr)),
        n_tracks=int(len(ids)), track_len_median=float(np.median(lens)) if len(lens) else 0.0,
        track_len_mean=float(lens.mean()) if len(lens) else 0.0,
        frac_cands_in_singleton_tracks=float(sum(cnt[lens == 1]) / max(len(fr), 1)) if len(lens) else 0.0,
        frac_tracks_singleton=float((lens == 1).mean()) if len(lens) else 0.0,
        frac_tracks_le3=float((lens <= 3).mean()) if len(lens) else 0.0,
        n_tracks_touching_mesh=int(len(unit_tr)),
        n_front_tracks=int(len(front_tr_obj)), n_front_person_keys=int(sum(1 for k, v in dec.items() if k[3] == "p" and v[0])),
        front_why=why,
        front_track_len=[tlen[t] for t in front_tr_obj],
        frac_cands_in_front_tracks=float(n_front_cands / max(len(fr), 1)),
        flicker_raw=f0 / max(n0, 1), flicker_smoothed=f2 / max(n2, 1),
        new_occ_events=int(ev0), new_occ_event_frames=int(evf), person_frames_with_hide=int(hidden_frames),
        render_s=round(time.time() - t0, 1),
    )


if __name__ == "__main__":
    root, out = sys.argv[1], sys.argv[2]
    clips = sys.argv[3:] or sorted(os.listdir(root))
    res = {}
    if os.path.exists(out):
        res = json.load(open(out))
    for c in clips:
        if c in res:
            continue
        t = time.time()
        try:
            res[c] = clip_stats(f"{root}/{c}")
        except Exception as e:  # report and continue
            res[c] = dict(error=repr(e))
        print(c, round(time.time() - t, 1), "s", {k: res[c].get(k) for k in ("T", "cands_per_frame", "n_tracks",
              "n_front_tracks", "flicker_raw", "flicker_smoothed")}, flush=True)
        json.dump(res, open(out, "w"), indent=1)
