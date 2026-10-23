"""Cost of carrying K occluders with SAM 3.1 (Object Multiplex), through its official video predictor API.

Same protocol as tracker_cost.py (SAM3 tracker): K objects from the clip's first-frame candidates (cands.npz, largest
first), each added on frame 0 with one positive click at its mask's innermost pixel, then propagated over --frames
frames. SAM 3.1's predictor computes its own image features, so its ms/frame includes the image encoder (SAM3's
tracker_cost.py reports it separately as set_image).

Runs on the upstream sam3 package (github.com/facebookresearch/sam3, which has the multiplex model) from --sam3_root,
put first on sys.path in this process only, so the repo's vendored models/sam3 stays what the pipeline uses.

usage: python scripts/eval/sam31_cost.py <run dir> --sam3_root <upstream sam3 clone> --ckpt <sam3.1_multiplex.pt>
       [--ks 1,4,8,16,32] [--frames 60]
"""
import argparse
import json
import os
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _k in ("USE_TF", "USE_JAX", "USE_FLAX"):
    os.environ.setdefault(_k, "0")


def click(mask):
    """the innermost pixel of a mask (max of the distance transform), as (x, y)."""
    import cv2
    d = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    y, x = np.unravel_index(int(d.argmax()), d.shape)
    return x, y


def main(a):
    sys.path.insert(0, a.sam3_root)
    import torch
    import sam3
    assert os.path.realpath(os.path.dirname(sam3.__file__)).startswith(os.path.realpath(a.sam3_root)), sam3.__file__
    from sam3.model_builder import build_sam3_multiplex_video_predictor
    sys.path.insert(1, os.path.join(REPO, "scripts", "occlusion", "fusion_v2"))
    from r3common import Run
    import gpu_stage   # load_cands only (numpy; does not import the vendored sam3)
    name = os.path.basename(a.run.rstrip("/"))
    run = Run(f"{a.run}/pipeline_out", name)
    masks, fr, _, _ = gpu_stage.load_cands(f"{a.run}/occlusion/_r3cache/{name}")
    T = min(a.frames, len(run))
    frames = tempfile.mkdtemp(prefix="sam31_frames_")
    for i in range(T):
        os.symlink(f"{run.pout}/images/{run.stems[i]}.jpg", f"{frames}/{i:05d}.jpg")
    first = np.nonzero(fr == 0)[0]
    first = first[np.argsort([-masks[c].sum() for c in first])]
    pred = build_sam3_multiplex_video_predictor(checkpoint_path=a.ckpt, use_fa3=False)   # FA3 is Hopper-only
    # upstream 7f473cf: Sam3BasePredictor.start_session always passes offload_state_to_cpu, which
    # Sam3MultiplexTrackingWithInteractivity.init_state does not take (it is False by default anyway)
    init = pred.model.init_state
    pred.model.init_state = lambda **kw: init(**{k: v for k, v in kw.items() if k != "offload_state_to_cpu"})
    out = dict(clip=name, frames=T, gpu=torch.cuda.get_device_name(0), ks={})
    for K in [int(k) for k in a.ks.split(",")]:
        if K > len(first):
            break
        sid = pred.handle_request(dict(type="start_session", resource_path=frames))["session_id"]
        for k in range(K):
            x, y = click(masks[first[k]])
            pred.handle_request(dict(type="add_prompt", session_id=sid, frame_index=0, obj_id=k + 1,
                                     points=torch.tensor([[x / run.W, y / run.H]], dtype=torch.float32),
                                     point_labels=torch.tensor([1], dtype=torch.int32)))
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        ious, n = [], 0
        for resp in pred.handle_stream_request(dict(type="propagate_in_video", session_id=sid, start_frame_index=0,
                                                              propagation_direction="forward")):
            n += 1
            f = resp["frame_index"]
            o = resp["outputs"]
            if f % 10 == 5 and "out_binary_masks" in o:
                cm = masks[fr == f]
                for m in np.asarray(o["out_binary_masks"]).astype(bool):
                    if m.shape == (run.H, run.W) and m.sum() and len(cm):
                        inter = (cm & m).reshape(len(cm), -1).sum(1)
                        ious.append(float((inter / (cm | m).reshape(len(cm), -1).sum(1)).max()))
        torch.cuda.synchronize()
        dt = time.perf_counter() - t0
        pred.handle_request(dict(type="close_session", session_id=sid))
        out["ks"][K] = dict(ms_per_frame=1000 * dt / max(n, 1), frames_out=n,
                            iou_to_cands_median=float(np.median(ious)) if ious else None)
        print(f"K={K}: SAM 3.1 {1000 * dt / max(n, 1):.1f} ms/frame (incl. image encoder) over {n} frames, "
              f"tracked-vs-candidate IoU median {out['ks'][K]['iou_to_cands_median']}", flush=True)
        torch.cuda.empty_cache()
    json.dump(out, open(f"{a.run}/profile/sam31_cost.json", "w"), indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--sam3_root", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--ks", default="1,4,8,16,32")
    ap.add_argument("--frames", type=int, default=60)
    main(ap.parse_args())
