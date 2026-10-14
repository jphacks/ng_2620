"""Check that fusion_v2/shared_cache.py changes nothing: avatar_app on a run with cached fusion GPU outputs, once with
and once without shared_cache.install() (separate processes), output frames compared byte for byte; also the fusion
hide masks of run_fusion.

usage (repo root):
  .venv/Scripts/python.exe scripts/eval/check_shared_cache.py <pipeline_out> <name> <r3cache dir with mesh/moge/cands/person.npz> <work dir>
"""
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def worker(pout, name, src, out, use):
    import argparse
    import pickle
    import time
    sys.path[:0] = [os.path.join(ROOT, "scripts"), os.path.join(ROOT, "scripts", "occlusion"),
                    os.path.join(ROOT, "scripts", "occlusion", "fusion_v2"), ROOT]
    dst = os.path.join(out, "occlusion", "_r3cache", name)
    os.makedirs(dst)
    for f in ("mesh.npz", "moge.npz", "cands.npz", "person.npz"):
        shutil.copy(os.path.join(src, f), dst)
    import shared_cache
    if not use:
        shared_cache.install = lambda: None
    import avatar_app
    import run_fusion_v2
    hides = {}
    rf = run_fusion_v2.run_fusion

    def keep(*a, **k):
        r = rf(*a, **k)
        hides.update(r["hide"])
        return r
    run_fusion_v2.run_fusion = keep            # avatar_app imports it at call time
    import numpy as np
    with np.load(os.path.join(dst, "cands.npz")) as c:
        points = str(c["mode"]) if "mode" in c.files else "full"
    a = argparse.Namespace(pipeline_out=pout, out=out, vrm=avatar_app.DEFAULT_VRM, name=name, fps=30.0, ssaa=2,
                           removal_workers=4, no_occlusion=False, no_removal=False, no_spring=False, no_ik=False,
                           fusion_reference=False, fusion_points=points, fusion_compile_decoder=False)
    t = time.perf_counter()
    st = avatar_app.main(a)
    st["wall_s"] = round(time.perf_counter() - t, 1)
    pickle.dump({"stats": st, "hide": hides}, open(os.path.join(out, "check.pkl"), "wb"))


def main(pout, name, src, work):
    if os.path.exists(work):
        shutil.rmtree(work)
    outs = {}
    for use in (False, True):
        out = os.path.join(work, "shared" if use else "plain")
        subprocess.run([sys.executable, __file__, "--worker", pout, name, src, out, str(int(use))], check=True)
        outs[use] = out
    import pickle
    import numpy as np
    r = {u: pickle.load(open(os.path.join(o, "check.pkl"), "rb")) for u, o in outs.items()}
    fa = sorted(glob.glob(os.path.join(outs[False], "frames", "*.jpg")))
    fb = sorted(glob.glob(os.path.join(outs[True], "frames", "*.jpg")))
    same_names = [os.path.basename(x) for x in fa] == [os.path.basename(x) for x in fb]
    diff = [os.path.basename(x) for x, y in zip(fa, fb) if open(x, "rb").read() != open(y, "rb").read()]
    ha, hb = r[False]["hide"], r[True]["hide"]
    hdiff = [k for k in ha if k not in hb or not np.array_equal(ha[k], hb[k])] + [k for k in hb if k not in ha]
    keys = [k for k in r[False]["stats"] if k.endswith("_s")]
    print(f"frames {len(fa)} vs {len(fb)}, names equal {same_names}, differing frames {len(diff)}; "
          f"fusion hide masks {len(ha)}, differing {len(hdiff)}")
    print("time  plain :", {k: r[False]["stats"][k] for k in keys})
    print("time  shared:", {k: r[True]["stats"][k] for k in keys})
    ok = same_names and not diff and not hdiff and len(fa) > 0
    print("IDENTICAL" if ok else "DIFFERENT")
    return ok


if __name__ == "__main__":
    if sys.argv[1] == "--worker":
        worker(*sys.argv[2:6], use=bool(int(sys.argv[6])))
    else:
        sys.exit(0 if main(*sys.argv[1:5]) else 1)
