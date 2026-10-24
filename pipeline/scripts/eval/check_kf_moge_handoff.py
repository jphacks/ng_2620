"""Check that keyframe_stage gets the same MoGe depth whether gpu_stage_fast hands it the Z it just computed
(moge.npz still being written by its saver thread) or the stage reads a finished moge.npz.

  A: run_fusion(keyframe=True) from scratch (prep, MoGe -> Z in memory -> keyframe stage)
  B: the same on a cache holding A's mesh.npz and moge.npz (keyframe stage reads moge.npz)
Each run is its own process (building SAM3 changes process-wide numerics). cands.npz, person.npz and the hide masks
must be identical.

usage (repo root): .venv/Scripts/python.exe scripts/eval/check_kf_moge_handoff.py <pipeline_out> <scratch dir>
"""
import filecmp
import glob
import os
import shutil
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if len(sys.argv) > 1 and sys.argv[1] == "--run":
    sys.path[:0] = [os.path.join(ROOT, "scripts", "occlusion"), ROOT]
    from utils.compile_cache import use_persistent_compile_cache
    use_persistent_compile_cache()
    import run_fusion_v2
    run_fusion_v2.run_fusion(sys.argv[2], sys.argv[3], "clip", save=True, keyframe=True)
    sys.exit(0)

pout, scratch = sys.argv[1], sys.argv[2]
A, B = os.path.join(scratch, "A"), os.path.join(scratch, "B")
for d in (A, B):
    if os.path.exists(d):
        raise SystemExit(f"{d} exists: use an empty scratch dir")
subprocess.run([sys.executable, os.path.abspath(__file__), "--run", pout, A], check=True)
os.makedirs(f"{B}/_r3cache/clip")
for f in ("mesh.npz", "moge.npz"):
    shutil.copy(f"{A}/_r3cache/clip/{f}", f"{B}/_r3cache/clip/{f}")
subprocess.run([sys.executable, os.path.abspath(__file__), "--run", pout, B], check=True)
ok = True
for f in ("cands.npz", "person.npz"):
    with np.load(f"{A}/_r3cache/clip/{f}") as a, np.load(f"{B}/_r3cache/clip/{f}") as b:
        same = set(a.files) == set(b.files) and all(np.array_equal(a[k], b[k]) for k in a.files)
    print(f, "identical" if same else "DIFFERENT")
    ok &= same
ha = sorted(os.path.relpath(p, A) for p in glob.glob(f"{A}/hide/clip/*/*.png"))
hb = sorted(os.path.relpath(p, B) for p in glob.glob(f"{B}/hide/clip/*/*.png"))
same = ha == hb and all(filecmp.cmp(os.path.join(A, p), os.path.join(B, p), shallow=False) for p in ha)
print(f"hide masks ({len(ha)}):", "identical" if same else "DIFFERENT")
ok &= same
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
