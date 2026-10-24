"""fusion_v2/evidence_fast.compute against another evidence implementation on the same cache: time and equality of
the outputs (units, per, Vr, track; NaN == NaN).

usage: python scripts/eval/check_evidence_fast.py --pipeline_out <dir> --cache <fusion cache dir> [--ref <module.py>]
  --ref: the file of the reference implementation (default: the frozen evidence.py)
"""
import argparse
import importlib.util
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FUSION = os.path.join(os.path.dirname(HERE), "occlusion", "fusion_v2")
sys.path[:0] = [FUSION, os.path.dirname(os.path.dirname(HERE))]

import numpy as np  # noqa: E402


def eq(x, y):
    if isinstance(x, dict):
        return x.keys() == y.keys() and all(eq(x[k], y[k]) for k in x)
    if isinstance(x, (list, tuple)):
        return len(x) == len(y) and all(eq(a, b) for a, b in zip(x, y))
    if isinstance(x, np.ndarray):
        return np.array_equal(x, y, equal_nan=x.dtype.kind == "f")
    if isinstance(x, float):
        return (x != x and y != y) or x == y
    return x == y


def run(mod_path, name, pout, cache):
    spec = importlib.util.spec_from_file_location(name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from r3common import Run
    work = tempfile.mkdtemp()
    for f in ("mesh.npz", "cands.npz", "person.npz", "moge.npz"):
        shutil.copyfile(os.path.join(cache, f), os.path.join(work, f))
    t0 = time.perf_counter()
    out = mod.compute(Run(pout), work)
    dt = time.perf_counter() - t0
    shutil.rmtree(work, ignore_errors=True)
    return out, dt


def main(a):
    import shared_cache
    shared_cache.install()
    ref_path = a.ref or os.path.join(FUSION, "evidence.py")
    order = [("ref", ref_path), ("new", os.path.join(FUSION, "evidence_fast.py"))]
    if a.new_first:   # the first run also fills shared_cache's memoized loads
        order.reverse()
    res = {k: run(path, "ev_" + k, a.pipeline_out, a.cache) for k, path in order}
    (ref, t_ref), (new, t_new) = res["ref"], res["new"]
    print(f"reference {t_ref:.2f} s, evidence_fast {t_new:.2f} s; units {len(ref['units'])} / {len(new['units'])}; "
          f"identical: {eq(ref, new)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline_out", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--ref", default=None)
    ap.add_argument("--new_first", action="store_true", help="run evidence_fast first (the first run warms caches)")
    main(ap.parse_args())
