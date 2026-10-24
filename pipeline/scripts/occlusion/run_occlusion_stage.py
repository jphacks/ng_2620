"""The GPU server's occlusion stage: fusion v2 once, its decisions kept for the render and export jobs, and the
occluder units for the editor (designdocs/architecture/pipeline-integration §3.2, §3.4).

usage (repo root of the pipeline):
  python scripts/occlusion/run_occlusion_stage.py --pipeline_out <pout> --out <occ> [--name <name>]
writes <occ>/fusion_state.pkl ({ev, dec, name}; avatar_app.py --fusion_state skips run_fusion with it),
<occ>/units/{f:05d}.png, <occ>/units.json, and keeps fusion's cache in <occ>/_r3cache/<name>/.
"""
import argparse
import os
import pickle
import sys
import time

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ME)
sys.path.insert(0, os.path.join(ME, "fusion_v2"))

from run_fusion_v2 import run_fusion  # noqa: E402
import units as U  # noqa: E402


def main(a):
    t0 = time.time()
    res = run_fusion(a.pipeline_out, a.out, a.name, save=False, render=False)
    run, ev, dec, cache = res["run"], res["ev"], res["dec"], res["cache"]
    with open(os.path.join(a.out, "fusion_state.pkl"), "wb") as f:
        pickle.dump({"name": run.name, "ev": ev, "dec": dec}, f, protocol=pickle.HIGHEST_PROTOCOL)
    import gpu_stage
    masks, _fr, _score, _inV = gpu_stage.load_cands(cache)
    units = U.write_units(a.out, ev, dec, masks, len(run), (run.H, run.W), run.pids)
    print(f"[STAGE][units] time={time.time() - t0:.2f} s, peak_alloc=0.00 GB, units={len(units)}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline_out", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default=None)
    main(ap.parse_args())
