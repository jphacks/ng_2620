"""How many of fusion v2's per-frame SAM3 candidates can affect a decision, and how many tracks they form.

A candidate C becomes a decision unit for person p only if (evidence.compute / fusion.decide): C overlaps p's mesh
silhouette M_p by >= 15 px, has pixels in M_p outside p's visible mask, and lies mostly outside p's visible mask
(own <= OWNMAX = 0.3). This counts, per frame, the candidates that pass a superset of that test, computed with the
visible mask refined by the 'person' detections but before evidence's depth step (which only grows the visible mask,
so it can only drop more candidates): those are the ones a keyframe + tracking scheme has to carry between keyframes.

usage: python scripts/eval/keyframe_study.py <run dir (profile_stages --out)> ...   -> prints a table, writes
       <run dir>/profile/relevant.json
"""
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(REPO, "scripts", "occlusion", "fusion_v2"), REPO]

OWN_MAX = 0.5   # looser than fusion's 0.3: a superset


def relevant(run_dir):
    from r3common import Run
    from evidence import load_person_dets, refine_visible, link
    from gpu_stage import load_cands
    import disk_arrays
    name = os.path.basename(run_dir.rstrip("/"))
    run = Run(f"{run_dir}/pipeline_out", name)
    cache = f"{run_dir}/occlusion/_r3cache/{name}"
    masks, fr, score, inV = load_cands(cache)
    D = disk_arrays.npz_member(f"{cache}/mesh.npz", "D")       # (T, P, H, W) mesh depth
    T = len(run)
    dets = load_person_dets(cache, T, run.H, run.W)
    keep = np.zeros(len(masks), bool)
    for i in range(T):
        lab = run.mask(i)
        Vr = refine_visible(lab, run.pids, dets[i])
        for ci in np.nonzero(fr == i)[0]:
            C = masks[ci]
            carea = max(int(C.sum()), 1)
            for k, p in enumerate(run.pids):
                M = D[i, k] > 0
                ovl = C & M
                if ovl.sum() < 15 or not (ovl & ~Vr[p]).any():
                    continue
                if (C & Vr[p]).sum() / carea <= OWN_MAX:
                    keep[ci] = True
                    break
    track = link(masks, fr) if len(masks) else np.zeros(0, int)
    # outermost relevant candidates (not >= 80% inside a larger relevant one of the same frame) and the connected
    # regions of their union: the number of distinct occluders a tracker would have to carry
    outer = np.zeros(T, int)
    regions = np.zeros(T, int)
    for i in range(T):
        idx = [c for c in np.nonzero((fr == i) & keep)[0]]
        idx.sort(key=lambda c: -masks[c].sum())
        top = []
        for c in idx:
            a = masks[c].sum()
            if not any((masks[c] & masks[t]).sum() >= 0.8 * a for t in top):
                top.append(c)
        outer[i] = len(top)
        if idx:
            u = np.any(masks[idx], 0).astype(np.uint8)
            regions[i] = cv2.connectedComponents(u)[0] - 1
    per_frame = np.bincount(fr[keep], minlength=T)
    all_frame = np.bincount(fr, minlength=T)
    res = dict(clip=name, frames=T, cands_per_frame=float(all_frame.mean()),
               relevant_per_frame_mean=float(per_frame.mean()), relevant_per_frame_max=int(per_frame.max()),
               outer_per_frame_mean=float(outer.mean()), outer_per_frame_max=int(outer.max()),
               regions_per_frame_mean=float(regions.mean()), regions_per_frame_max=int(regions.max()),
               mean_track_len=float(len(track) / max(len(np.unique(track)), 1)),
               relevant_tracks=int(len(np.unique(track[keep]))), all_tracks=int(len(np.unique(track))),
               relevant_per_frame=per_frame.tolist())
    json.dump(res, open(f"{run_dir}/profile/relevant.json", "w"))
    return res


if __name__ == "__main__":
    for d in sys.argv[1:]:
        r = relevant(d)
        print(f"{r['clip']}: cands/frame {r['cands_per_frame']:.1f}, relevant/frame {r['relevant_per_frame_mean']:.1f} "
              f"(max {r['relevant_per_frame_max']}), outer {r['outer_per_frame_mean']:.1f} (max {r['outer_per_frame_max']}), "
              f"regions {r['regions_per_frame_mean']:.1f} (max {r['regions_per_frame_max']}), "
              f"mean track length {r['mean_track_len']:.1f} f, tracks relevant {r['relevant_tracks']} / {r['all_tracks']}",
              flush=True)
