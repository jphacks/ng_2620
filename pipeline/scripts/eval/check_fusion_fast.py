"""Bit-identity check of fusion v2's fast stages against the frozen ones on one pipeline output.

Runs gpu_stage.run_gpu (frozen) and gpu_stage_fast.run_gpu on the same prep cache, each in its own process, and
compares moge.npz / cands.npz / person.npz array by array; then runs evidence.compute and evidence_fast.compute on the
frozen cache and compares the evidence recursively (NaN == NaN). Exit code 0 only if everything is identical.
Separate processes matter: building SAM3 switches on a process-wide bf16 autocast and TF32, so a second GPU stage in
the same process would run MoGe under other numerics than the pipeline does.

usage (repo root): .venv/Scripts/python.exe scripts/eval/check_fusion_fast.py <run>/pipeline_out <scratch dir>
"""
import os
import shutil
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "scripts", "occlusion", "fusion_v2")]
from r3common import Run  # noqa: E402
import prep  # noqa: E402
import gpu_stage  # noqa: E402
import gpu_stage_fast  # noqa: E402
import evidence  # noqa: E402
import evidence_fast  # noqa: E402


def same_npz(a, b):
    ok = True
    for f in ("moge.npz", "cands.npz", "person.npz"):
        A, B = np.load(f"{a}/{f}"), np.load(f"{b}/{f}")
        # "mode" (which point grid made the candidates) exists only in gpu_stage_fast's cache
        for k in sorted((set(A.files) | set(B.files)) - {"mode"}):
            eq = k in A.files and k in B.files and A[k].shape == B[k].shape and np.array_equal(A[k], B[k])
            if not eq:
                print(f"  DIFFERENT {f}:{k}")
            ok &= eq
    return ok


def same_tree(a, b, path="ev"):
    if isinstance(a, dict):
        return set(a) == set(b) and all(same_tree(a[k], b[k], f"{path}[{k!r}]") for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(same_tree(x, y, f"{path}[{i}]") for i, (x, y) in enumerate(zip(a, b)))
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        a_, b_ = np.asarray(a), np.asarray(b)
        ok = a_.shape == b_.shape and np.array_equal(a_, b_, equal_nan=a_.dtype.kind == "f")
    elif isinstance(a, float) and isinstance(b, float) and np.isnan(a) and np.isnan(b):
        ok = True
    else:
        ok = type(a) is type(b) and a == b
    if not ok:
        print(f"  DIFFERENT {path}")
    return ok


def main(pout, scratch):
    run = Run(pout)
    base = os.path.join(scratch, "prep")
    os.makedirs(base, exist_ok=True)
    prep.prep(run, base)
    dirs = {}
    for name in ("frozen", "fast"):
        d = os.path.join(scratch, name)
        os.makedirs(d, exist_ok=True)
        for f in ("moge.npz", "cands.npz", "person.npz", "evidence.pkl"):
            if os.path.exists(f"{d}/{f}"):
                os.remove(f"{d}/{f}")
        shutil.copy(f"{base}/mesh.npz", d)
        t = time.perf_counter()
        subprocess.run([sys.executable, os.path.abspath(__file__), "--gpu", name, pout, d], check=True)
        print(f"gpu stage {name}: {time.perf_counter() - t:.1f} s (own process)")
        dirs[name] = d
    ok_gpu = same_npz(dirs["frozen"], dirs["fast"])
    print("GPU caches identical:", ok_gpu)
    evs = {}
    for name, mod in (("frozen", evidence), ("fast", evidence_fast)):
        d = os.path.join(scratch, f"ev_{name}")
        os.makedirs(d, exist_ok=True)
        for f in ("mesh.npz", "moge.npz", "cands.npz", "person.npz"):
            shutil.copy(f"{dirs['frozen']}/{f}", d)
        if os.path.exists(f"{d}/evidence.pkl"):
            os.remove(f"{d}/evidence.pkl")
        t = time.perf_counter()
        evs[name] = mod.compute(run, d)
        print(f"evidence {name}: {time.perf_counter() - t:.1f} s")
    ok_ev = same_tree(evs["frozen"], evs["fast"])
    print("evidence identical:", ok_ev)
    return ok_gpu and ok_ev


if __name__ == "__main__":
    if sys.argv[1] == "--gpu":
        mod = gpu_stage if sys.argv[2] == "frozen" else gpu_stage_fast
        mod.run_gpu(Run(sys.argv[3]), sys.argv[4], owner="check")
        sys.exit(0)
    sys.exit(0 if main(sys.argv[1], sys.argv[2]) else 1)
