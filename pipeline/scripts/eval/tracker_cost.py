"""Cost of carrying K occluder masks with the SAM3 tracker, on the image features fusion v2 already computes.

SAM3's image model and its tracker share the vision backbone: the image model's inst-interactive predictor loads the
checkpoint's tracker.* weights (model_builder._load_checkpoint), and Sam3Processor.set_image builds the tracker's
'sam2_backbone_out' (conv_s0 / conv_s1 applied). Sam3VideoInference feeds its tracker the same way, through
init_state(cached_features=...) with {"tracker_backbone_out": ...} per frame (sam3_video_base.py). So one set_image per
frame serves the point prompts, the 'person' grounding and the tracker.

Per K: K masks from the clip's first frame's candidates (cands.npz, largest first) are added on frame 0 and
propagated over --frames frames. Reports the tracker's ms/frame (set_image excluded, timed separately) and the mean
IoU between each tracked mask and the best-matching per-frame candidate (a sanity check that the features fit).

usage: python scripts/eval/tracker_cost.py <run dir (profile_stages --out)> [--ks 1,4,8,16,32] [--frames 60]
"""
import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FUSION = os.path.join(REPO, "scripts", "occlusion", "fusion_v2")
sys.path[:0] = [FUSION, os.path.join(REPO, "models", "sam3"), REPO]
for _k in ("USE_TF", "USE_JAX", "USE_FLAX"):
    os.environ.setdefault(_k, "0")


def main(a):
    import cv2
    import torch
    from PIL import Image
    import gpu_stage_fast as gs
    from keyframes import build_tracker, tracker_feats
    from gpu_stage import load_cands
    from r3common import Run
    name = os.path.basename(a.run.rstrip("/"))
    run = Run(f"{a.run}/pipeline_out", name)
    masks, fr, score, inV = load_cands(f"{a.run}/occlusion/_r3cache/{name}")
    T = min(a.frames, len(run))
    model, proc, _ = gs._sam3_build(compile_decoder=False)
    gs.enable_sam3_globals()
    tracker = build_tracker()
    feats, t_enc = [], 0.0
    for i in range(T):   # features once, reused for every K (kept on the GPU: T x ~40 MB)
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        with torch.autocast("cuda", dtype=torch.bfloat16), torch.inference_mode():
            st = proc.set_image(Image.fromarray(cv2.cvtColor(run.image(i), cv2.COLOR_BGR2RGB)))
        torch.cuda.synchronize()
        t_enc += time.perf_counter() - t0
        feats.append(tracker_feats(st))
        del st
    first = np.nonzero(fr == 0)[0]
    first = first[np.argsort([-masks[c].sum() for c in first])]
    out = dict(clip=name, frames=T, set_image_ms=1000 * t_enc / T, gpu=torch.cuda.get_device_name(0), ks={})
    for K in [int(k) for k in a.ks.split(",")]:
        if K > len(first):
            break
        cache = {}
        with torch.autocast("cuda", dtype=torch.bfloat16), torch.inference_mode():
            state = tracker.init_state(video_height=run.H, video_width=run.W, num_frames=T, cached_features=cache)
            cache[0] = feats[0]
            for k in range(K):
                tracker.add_new_mask(state, frame_idx=0, obj_id=k + 1, mask=torch.from_numpy(masks[first[k]]))
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            ious = []
            for f, obj_ids, _, video_res_masks, _, _ in tracker.propagate_in_video(
                    state, start_frame_idx=0, max_frame_num_to_track=T, reverse=False, tqdm_disable=True,
                    propagate_preflight=True):
                if f + 1 < T:
                    cache[f + 1] = feats[f + 1]
                cache.pop(f - 1, None)
                if f % 10 == 5:   # sanity: tracked masks vs the frame's own candidates
                    tm = (video_res_masks[:, 0] > 0).cpu().numpy()
                    cm = masks[fr == f]
                    for m in tm:
                        if m.sum() and len(cm):
                            inter = (cm & m).reshape(len(cm), -1).sum(1)
                            ious.append(float((inter / ((cm | m).reshape(len(cm), -1).sum(1))).max()))
            torch.cuda.synchronize()
            dt = time.perf_counter() - t0
        out["ks"][K] = dict(ms_per_frame=1000 * dt / T, iou_to_cands_median=float(np.median(ious)) if ious else None)
        print(f"K={K}: tracker {1000 * dt / T:.1f} ms/frame, tracked-vs-candidate IoU median "
              f"{out['ks'][K]['iou_to_cands_median']}", flush=True)
        del state
        torch.cuda.empty_cache()
    print(f"set_image {out['set_image_ms']:.1f} ms/frame", flush=True)
    json.dump(out, open(f"{a.run}/profile/tracker_cost.json", "w"), indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--ks", default="1,4,8,16,32")
    ap.add_argument("--frames", type=int, default=60)
    main(ap.parse_args())
