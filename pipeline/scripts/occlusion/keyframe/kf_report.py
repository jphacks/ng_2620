"""One table per variant: GT pass count / pooled F1 on the 44 Penn clips, flips vs base, SAM3 time, keyframes, flicker.

usage: python kf_report.py <out_root> <score dir> <tag> [<tag> ...]
  reads <score dir>/gt_<tag>.json (score_gt), <out_root>/<tag>_times.json, <out_root>/compare_<tag>.json
"""
import json
import os
import sys

import numpy as np


def main():
    root, sp, tags = sys.argv[1], sys.argv[2], sys.argv[3:]
    gb = json.load(open(f"{sp}/gt_base.json"))
    bt = json.load(open(f"{root}/base_times.json"))
    penn = sorted(gb)
    tp = sum(gb[c]["tp"] for c in penn)
    fp = sum(gb[c]["fp"] for c in penn)
    fn = sum(gb[c]["fn"] for c in penn)
    print(f"base: pass {sum(gb[c]['passed'] for c in penn)}/{len(penn)}, pooled F1 {2 * tp / (2 * tp + fp + fn):.3f}, "
          f"overcut {fp / sum(gb[c]['neg'] for c in penn):.4f}")
    for tag in tags:
        g = json.load(open(f"{sp}/gt_{tag}.json"))
        kt = json.load(open(f"{root}/{tag}_times.json"))
        cm = json.load(open(f"{root}/compare_{tag}.json")) if os.path.exists(f"{root}/compare_{tag}.json") else {}
        cs = [c for c in penn if c in g]
        tp = sum(g[c]["tp"] for c in cs)
        fp = sum(g[c]["fp"] for c in cs)
        fn = sum(g[c]["fn"] for c in cs)
        print(f"\n== {tag}: pass {sum(g[c]['passed'] for c in cs)}/{len(cs)} (base {sum(gb[c]['passed'] for c in cs)}), "
              f"pooled F1 {2 * tp / (2 * tp + fp + fn):.3f}, overcut {fp / sum(g[c]['neg'] for c in cs):.4f}")
        for c in cs:
            if g[c]["passed"] != gb[c]["passed"]:
                print(f"   {c}: base {'PASS' if gb[c]['passed'] else 'fail'} F1 {gb[c]['F1']:.3f} oc {gb[c]['overcut']:.4f}"
                      f" -> {'PASS' if g[c]['passed'] else 'fail'} F1 {g[c]['F1']:.3f} oc {g[c]['overcut']:.4f}")
        dF = np.array([g[c]["F1"] - gb[c]["F1"] for c in cs if gb[c]["pos"] >= 100])
        print(f"   F1 change on clips with >= 100 positives: median {np.median(dF):+.3f}, worst {dF.min():+.3f}, "
              f"best {dF.max():+.3f}")
        for grp, sel in (("penn", [c for c in kt if c.startswith("penn")]), ("3dpw", [c for c in kt if c.startswith("3dpw")])):
            if not sel or "t_backbone" not in kt[sel[0]]:
                continue
            F = sum(kt[c]["frames"] for c in sel)
            b = sum(bt[c]["_sam3_stage"] for c in sel)
            k = sum(kt[c]["t_backbone"] + kt[c]["t_track"] + kt[c]["t_keyframe"] + kt[c]["t_signal"] for c in sel)
            parts = {n: 1000 * sum(kt[c][n] for c in sel) / F for n in ("t_backbone", "t_track", "t_keyframe", "t_signal")}
            print(f"   [{grp}] {len(sel)} clips {F} frames: base SAM3 {1000 * b / F:.0f} ms/f -> {tag} {1000 * k / F:.0f} ms/f "
                  f"({', '.join(f'{n[2:]} {v:.0f}' for n, v in parts.items())}); keyframes "
                  f"{sum(kt[c]['n_keyframes'] for c in sel) / F:.1%}, objects {sum(kt[c]['n_objects'] for c in sel) / len(sel):.1f}/clip,"
                  f" active {sum(kt[c]['obj_frames'] for c in sel) / F:.2f}/frame, build {np.mean([kt[c]['t_build'] for c in sel]):.1f} s/clip")
        if cm:
            fr = np.mean([cm[c]["flicker_ref"] for c in cm])
            fk = np.mean([cm[c]["flicker"] for c in cm])
            print(f"   flicker (mean over clips): base {fr:.4f} -> {tag} {fk:.4f}")


if __name__ == "__main__":
    main()
