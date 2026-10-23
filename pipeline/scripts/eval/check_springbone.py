"""SpringBoneSim's vectorized _advance against the per-joint loop on a real pose sequence: time and how far apart the
spring node positions get (both simulations are fed the same retargeted poses frame by frame, as avatar_app does).

usage: python scripts/eval/check_springbone.py --pipeline_out <dir> --vrm <file> [--pid 1] [--fps 30]
"""
import argparse
import glob
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(REPO, "scripts"), REPO]

import numpy as np  # noqa: E402


def main(a):
    from avatar.vrm import load_vrm
    from avatar.retarget_mhr import MhrToVrm
    from avatar.springbone import SpringBoneSim
    model = load_vrm(a.vrm)
    rt = MhrToVrm(model)
    files = sorted(glob.glob(f"{a.pipeline_out}/skeleton_4d_individual/{a.pid}/*.npz"))
    sk = [dict(np.load(f)) for f in files]
    scale = rt.leg_scale([z["joint_coords"] for z in sk])
    poses = []
    for z in sk:
        g, t, _ = rt.solve(z["joint_rots"], z["joint_coords"] + z["cam_t"], scale)
        poses.append((g, ((rt.A.T @ t) / scale).astype(np.float32)))
    sims = {"loop": SpringBoneSim(model, center="none", vectorized=False),
            "vec": SpringBoneSim(model, center="none", vectorized=True)}
    out, dt = {}, {}
    for k, sim in sims.items():
        res = []
        t0 = time.perf_counter()
        for g, tg in poses:
            res.append(sim.step(g, tg, 1.0 / a.fps, warmup=30))
        dt[k] = time.perf_counter() - t0
        out[k] = np.stack(res)
    nodes = sorted(set(sims["loop"].by_node) | {i for i, _, _ in sims["vec"].follow})
    pa, pb = out["loop"][:, nodes][..., :3, 3], out["vec"][:, nodes][..., :3, 3]   # (frames, nodes, 3)
    d = np.linalg.norm(pa - pb, axis=-1) / scale * 1000   # mm in the avatar's own (glTF) units
    print(f"{len(poses)} frames, {sims['vec'].n_joints} spring joints, {len(sims['vec'].levels)} levels: loop "
          f"{1000 * dt['loop'] / len(poses):.1f} ms/frame, vectorized {1000 * dt['vec'] / len(poses):.1f} ms/frame; "
          f"spring node position difference mean {d.mean():.4f} mm, "
          f"max {d.max():.4f} mm (frame {int(np.unravel_index(d.argmax(), d.shape)[0])})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline_out", required=True)
    ap.add_argument("--vrm", required=True)
    ap.add_argument("--pid", type=int, default=1)
    ap.add_argument("--fps", type=float, default=30.0)
    main(ap.parse_args())
