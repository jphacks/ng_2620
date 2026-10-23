"""fusion v2 with another depth model in place of MoGe-2: time per frame and the hide masks against MoGe's.

Copies a finished run's fusion cache (mesh.npz, cands.npz, person.npz: none of them depends on the depth), writes the
candidate model's depth as moge.npz (Z, the same layout; 0 = invalid), and reruns evidence / decision / render.
Fusion only uses the depth after fitting a per-person, per-frame scale to the mesh (evidence.robust_scale), so a
model with metric depth up to scale is a drop-in candidate; relative-disparity models are not (unknown shift).

usage: python scripts/eval/depth_swap_study.py --run <e2e.py run dir> --model <hf id> [--model ...]
"""
import argparse
import glob
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(REPO, "scripts", "occlusion", "fusion_v2"), os.path.join(REPO, "scripts", "occlusion"),
                os.path.join(REPO, "scripts"), REPO]

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402


def depth_hf(model_id, run, half=True):
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation
    proc = AutoImageProcessor.from_pretrained(model_id)
    m = AutoModelForDepthEstimation.from_pretrained(model_id).cuda().eval()
    if half:
        m = m.half()
    Z = np.zeros((len(run), run.H, run.W), np.float32)
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for i in range(len(run)):
        rgb = cv2.cvtColor(run.image(i), cv2.COLOR_BGR2RGB)
        x = proc(images=rgb, return_tensors="pt")["pixel_values"].cuda()
        with torch.no_grad():
            d = m(pixel_values=x.half() if half else x).predicted_depth
        d = torch.nn.functional.interpolate(d[:, None].float(), size=(run.H, run.W), mode="bilinear",
                                            align_corners=False)[0, 0]
        Z[i] = d.cpu().numpy()
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    del m
    torch.cuda.empty_cache()
    Z[~np.isfinite(Z)] = 0
    Z[Z < 0] = 0
    return Z, dt


def hide_iou(a, b):
    it = un = 0
    for k in a:
        x, y = a[k] >= 0.5, b[k] >= 0.5
        it += int((x & y).sum())
        un += int((x | y).sum())
    return it / max(un, 1), sum(int((v >= 0.5).sum()) for v in b.values()) / max(sum(int((v >= 0.5).sum()) for v in a.values()), 1)


def main(a):
    from r3common import Run
    import shared_cache
    import evidence_fast
    import fusion
    shared_cache.install()
    name = os.path.basename(os.path.normpath(a.run))
    run = Run(f"{a.run}/pipeline_out", name)
    src = f"{a.run}/avatar/occlusion/_r3cache/{name}"
    ref = None
    for mid in ["moge"] + a.model:
        cache = f"{a.out}/{name}/{mid.replace('/', '_')}"
        os.makedirs(cache, exist_ok=True)
        for f in ("mesh.npz", "cands.npz", "person.npz"):
            if not os.path.exists(f"{cache}/{f}"):
                shutil.copyfile(f"{src}/{f}", f"{cache}/{f}")
        if mid == "moge":
            shutil.copyfile(f"{src}/moge.npz", f"{cache}/moge.npz")
            dt = float("nan")
        elif not os.path.exists(f"{cache}/moge.npz"):
            Z, dt = depth_hf(mid, run)
            np.savez_compressed(f"{cache}/moge.npz", Z=Z, fov_x=0.0, masked=True, level=3)
        else:
            dt = float("nan")
        if os.path.exists(f"{cache}/evidence.pkl"):
            os.remove(f"{cache}/evidence.pkl")
        ev = evidence_fast.compute(run, cache)
        dec = fusion.decide(ev, fusion.P)
        hide = fusion.render(run, ev, cache, dec, f"{a.out}/{name}", fusion.P, save=False)
        if ref is None:
            ref = hide
            print(f"{name} moge: reference", flush=True)
            continue
        iou, ratio = hide_iou(ref, hide)
        print(f"{name} {mid}: {1000 * dt / len(run):.1f} ms/frame; hide clip IoU vs MoGe {iou:.3f}, hidden px ratio "
              f"{ratio:.3f}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--model", nargs="+", required=True)
    ap.add_argument("--out", default=os.path.join(REPO, "testdata", "depthswap"))
    main(ap.parse_args())
