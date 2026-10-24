"""Tracking cost per frame vs number of objects, as kf_stage tracks them: one tracker state per object, the vision
backbone run once per frame and shared. Backends: kf_stage.Sam3Backend, kf_stage.EdgeTAMBackend.

usage: python track_cost_states.py <sam3|edgetam> <pipeline_out> <r3cache of the same clip> <out.json> [K ...]
"""
import json
import os
import sys
import time

import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kf_stage  # noqa: E402
import r3common  # noqa: E402
from tracker_cost import frame0_masks  # noqa: E402


def main():
    backend, pout, cache, out = sys.argv[1:5]
    Ks = [int(k) for k in sys.argv[5:]] or [1, 2, 4, 8]
    run = r3common.Run(pout)
    T = len(run)
    t = time.time()
    bk = (kf_stage.Sam3Backend if backend == "sam3" else kf_stage.EdgeTAMBackend)(run, T, run.H, run.W)
    res = dict(backend=backend, T=T, build_s=round(time.time() - t, 1))
    for rep in range(2):
        for K in Ks:
            masks = frame0_masks(cache, K)
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
            t = time.time()
            t_bb = 0.0
            states = []
            for i in range(T):
                tb = time.time()
                feats = bk.features(i)
                torch.cuda.synchronize()
                t_bb += time.time() - tb
                if i == 0:
                    states = [bk.start(0, feats, m) for m in masks]
                for st, gen in states:
                    bk.step(st, gen, i, feats)
            torch.cuda.synchronize()
            dt = time.time() - t
            for _, gen in states:
                gen.close()
            res[f"K{K}_ms_per_frame_rep{rep}"] = round(1000 * dt / T, 1)
            res[f"K{K}_backbone_ms_rep{rep}"] = round(1000 * t_bb / T, 1)
            res[f"K{K}_peak_GB_rep{rep}"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
            print(f"{backend} K={K}: {1000 * dt / T:.1f} ms/frame (backbone {1000 * t_bb / T:.1f}), peak "
                  f"{res[f'K{K}_peak_GB_rep{rep}']} GB", flush=True)
            json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
