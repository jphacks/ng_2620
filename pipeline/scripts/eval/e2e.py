"""End-to-end time of the production pipeline on one clip, from the input frames to the avatar video, per stage.

Runs the two production processes as a user would, timing each one's wall clock (process start-up and model loading
included):
  mesh    scripts/offline_app_lite.py --input_video <lo>   detect, SAM3 person tracking, HMR (+ meshes / overlays)
  avatar  scripts/avatar_app.py [--hq <hi>]               fusion v2 occlusion (MoGe, SAM3 keyframes, CPU stages),
                                                          person removal (in a thread, overlapped with fusion),
                                                          retarget + render, composite + encode
and parses the stage lines both print ([STAGE][...] / stats.json) into <out>/e2e.json, with a table on stdout.

usage (repo root):
  python scripts/eval/e2e.py --input <frames dir or .mp4> --out <dir> [--hq <hi-res frames dir>] [--vrm <file>]
      [--name <run name>] [--mesh_args "<extra offline_app_lite.py args>"] [-- extra avatar_app.py args]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def run(cmd, log):
    t0 = time.perf_counter()
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        r = subprocess.run(cmd, cwd=REPO, stdout=f, stderr=subprocess.STDOUT)
    dt = time.perf_counter() - t0
    if r.returncode:
        raise SystemExit(f"failed ({r.returncode}): {' '.join(cmd)}; see {log}")
    return dt


def main(a, extra):
    os.makedirs(a.out, exist_ok=True)
    name = a.name or os.path.basename(os.path.normpath(a.out))
    pout = os.path.join(a.out, "pipeline_out")
    py = sys.executable
    res = {"input": a.input, "hq": a.hq, "vrm": a.vrm}

    res["mesh_wall_s"] = run([py, "-u", "scripts/offline_app_lite.py", "--input_video", a.input, "--output_dir", pout]
                             + a.mesh_args.split(), os.path.join(a.out, "mesh.log"))
    stages = {}
    for line in open(os.path.join(a.out, "mesh.log"), encoding="utf-8", errors="replace"):
        m = re.match(r"\[STAGE\]\[(\w+)\] time=([\d.]+) s, peak_alloc=([\d.]+) GB", line)
        if m:
            stages[m[1]] = dict(time_s=float(m[2]), peak_alloc_GB=float(m[3]))
    res["mesh_stages"] = stages

    av = os.path.join(a.out, "avatar")
    cmd = [py, "-u", "scripts/avatar_app.py", "--pipeline_out", pout, "--out", av, "--name", name, "--gpu_removal"]
    cmd += (["--hq", a.hq] if a.hq else []) + (["--vrm", a.vrm] if a.vrm else []) + extra
    res["avatar_wall_s"] = run(cmd, os.path.join(a.out, "avatar.log"))
    res["avatar_stats"] = json.load(open(os.path.join(av, "stats.json")))
    for line in open(os.path.join(a.out, "avatar.log"), encoding="utf-8", errors="replace"):
        m = re.search(r"\[fusion_v2\].*prep (\d+)s, gpu (\d+)s, cpu (\d+)s", line)
        if m:
            res["fusion_parts_s"] = dict(prep=int(m[1]), gpu=int(m[2]), cpu=int(m[3]))
        m = re.search(r"(\S+) (?:moge|depth \S+) ([\d.]+)$", line.strip())   # the scene depth model
        if m:
            res["fusion_moge_s"] = float(m[2])
    res["total_wall_s"] = res["mesh_wall_s"] + res["avatar_wall_s"]
    json.dump(res, open(os.path.join(a.out, "e2e.json"), "w"), indent=1)

    s = res["avatar_stats"]
    n = s["frames"]
    rows = [("mesh (process wall)", res["mesh_wall_s"])]
    rows += [(f"  {k}", v["time_s"]) for k, v in stages.items()]
    rows += [("avatar (process wall)", res["avatar_wall_s"]),
             ("  fusion v2", s.get("fusion_s", 0))]
    if "fusion_parts_s" in res:
        fp = res["fusion_parts_s"]
        rows += [("    prep", fp["prep"]), ("    gpu (MoGe + SAM3)", fp["gpu"]), ("      scene depth (MoGe or FUSION_DEPTH)", res.get("fusion_moge_s", 0)),
                 ("    cpu (evidence, decision, render)", fp["cpu"])]
    rows += [("  removal (thread, overlapped)", s.get("removal_s", 0)), ("  removal wait after fusion", s.get("removal_wait_s", 0)),
             ("  retarget + render", s.get("render_s", 0)), ("  hide", s.get("hide_s", 0)),
             ("  composite (+ encode before Sinks)", s.get("composite_s", 0)),
             ("  wait for queued writes / encodes", s.get("write_wait_s", 0)), ("  avatar_app total (in process)", s.get("total_s", 0)),
             ("TOTAL", res["total_wall_s"])]
    print(f"{name}: {n} frames, pipeline {s['size'][0]}x{s['size'][1]}, output {s['out_size'][0]}x{s['out_size'][1]}")
    for k, v in rows:
        print(f"{k:40s} {v:8.1f} s  {1000 * v / n:7.0f} ms/frame  {100 * v / res['total_wall_s']:5.1f}%")


if __name__ == "__main__":
    argv = sys.argv[1:]
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hq", default=None)
    ap.add_argument("--vrm", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--mesh_args", default="")
    main(ap.parse_args(argv), extra)
