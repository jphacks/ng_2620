"""Check fusion_v2/prep_fast.py: mesh.npz from prep_fast.prep equals the frozen prep.prep's, array by array.

usage (repo root): .venv/Scripts/python.exe scripts/eval/check_prep_fast.py <pipeline_out> <work dir>
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "scripts", "occlusion", "fusion_v2")]
import prep  # noqa: E402
import prep_fast  # noqa: E402
from r3common import Run  # noqa: E402

pout, work = sys.argv[1:3]
run = Run(pout)
a, b = os.path.join(work, "frozen"), os.path.join(work, "fast")
prep.prep(run, a)
prep_fast.prep(run, b)
with np.load(f"{a}/mesh.npz") as za, np.load(f"{b}/mesh.npz") as zb:
    same = za.files == zb.files and all(za[k].dtype == zb[k].dtype and za[k].shape == zb[k].shape
                                        and np.array_equal(za[k], zb[k], equal_nan=za[k].dtype.kind == "f")
                                        for k in za.files)
    print({k: (za[k].shape, str(za[k].dtype)) for k in za.files})
left = [f for f in os.listdir(b) if f.startswith("_prep_")]
print("scratch files left:", left)
print("IDENTICAL" if same else "DIFFERENT")
sys.exit(0 if same else 1)
