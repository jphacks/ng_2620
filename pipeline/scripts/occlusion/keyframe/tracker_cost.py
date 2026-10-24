"""SAM3 tracker cost per frame vs the number of tracked objects (masks added on frame 0).

usage: python tracker_cost.py <frames dir (jpg)> <r3cache dir of the same clip> <out.json> [K ...]

The tracker is built exactly like offline_app_lite.build_sam3_tracker (tracker + vision backbone). Objects are
the K highest-score SAM3 candidates of frame 0 (cands.npz), added with add_new_mask. Also times the vision
backbone alone per frame (the part shared by all objects).
"""
import json
import os
import sys
import time

import numpy as np
import torch

REPO = "C:/VScodeFiles/sam-body4d"
sys.path.insert(0, REPO)
from utils.skip_init import skip_random_init  # noqa: E402


def build_sam3_tracker(ckpt_path):
    from models.sam3.sam3.model_builder import build_tracker
    with skip_random_init():
        tracker = build_tracker(apply_temporal_disambiguation=True, with_backbone=True)
    with open(ckpt_path, "rb") as f:
        use_mmap = f.read(4) == b"PK\x03\x04"
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True, mmap=use_mmap)
    if "model" in ckpt and isinstance(ckpt["model"], dict):
        ckpt = ckpt["model"]
    sd = {k[len("tracker."):]: v for k, v in ckpt.items() if k.startswith("tracker.")}
    pre = "detector.backbone.vision_backbone."
    sd.update({"backbone.vision_backbone." + k[len(pre):]: v for k, v in ckpt.items() if k.startswith(pre)})
    del ckpt
    tracker.load_state_dict(sd, strict=True)
    return tracker.to("cuda").eval()


def frame0_masks(cache, K):
    c = np.load(f"{cache}/cands.npz")
    H, W = c["shape"]
    rows = np.nonzero(c["frame"] == 0)[0]
    rows = rows[np.argsort(-c["score"][rows])]
    out = []
    for r in rows[:K]:
        out.append(np.unpackbits(c["masks"][r])[:H * W].reshape(H, W).astype(bool))
    while len(out) < K:      # fewer candidates than K: repeat (cost is per object slot)
        out.append(out[len(out) % max(len(out), 1)])
    return out


def main():
    frames, cache, out = sys.argv[1], sys.argv[2], sys.argv[3]
    Ks = [int(k) for k in sys.argv[4:]] or [1, 2, 4, 8, 16]
    t0 = time.time()
    tr = build_sam3_tracker("C:/VScodeFiles/checkpoints/sam3/sam3.pt")
    print("build", round(time.time() - t0, 1), "s", flush=True)
    state = tr.init_state(video_path=frames, offload_video_to_cpu=True)
    T = state["num_frames"]
    res = dict(T=T, frames=frames)
    # vision backbone alone (what every object shares per frame)
    with torch.inference_mode():
        for rep in range(2):
            torch.cuda.synchronize()
            t = time.time()
            for i in range(T):
                img = state["images"][i].cuda().float().unsqueeze(0)
                tr.forward_image(img)
            torch.cuda.synchronize()
            res[f"backbone_ms_per_frame_rep{rep}"] = 1000 * (time.time() - t) / T
            print("backbone", rep, round(res[f"backbone_ms_per_frame_rep{rep}"], 1), "ms/f", flush=True)
    for rep in range(2):          # rep 0 doubles as warm-up
        for K in Ks:
            tr.reset_state(state) if hasattr(tr, "reset_state") else tr.clear_all_points_in_video(state)
            state["cached_features"] = {}
            for k, m in enumerate(frame0_masks(cache, K)):
                tr.add_new_mask(inference_state=state, frame_idx=0, obj_id=k + 1, mask=torch.from_numpy(m))
            torch.cuda.synchronize()
            t = time.time()
            n = 0
            for _ in tr.propagate_in_video(state, start_frame_idx=0, max_frame_num_to_track=T, reverse=False,
                                           tqdm_disable=True, propagate_preflight=True):
                n += 1
            torch.cuda.synchronize()
            dt = time.time() - t
            res[f"K{K}_ms_per_frame_rep{rep}"] = 1000 * dt / n
            res[f"K{K}_peak_GB_rep{rep}"] = torch.cuda.max_memory_allocated() / 2**30
            torch.cuda.reset_peak_memory_stats()
            print(f"K={K}: {1000 * dt / n:.1f} ms/frame over {n} frames, peak {res[f'K{K}_peak_GB_rep{rep}']:.2f} GB",
                  flush=True)
            json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
