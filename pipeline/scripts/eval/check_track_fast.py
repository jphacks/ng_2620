"""SAM3 person tracking as offline_app_lite runs it, before and after utils/sam3_track_fast: time and bit-identity of
the masks. The box prompts are the bounding boxes of the given pipeline_out's first-frame masks (the same for both).

With --attn the fast run also routes the memory attention through utils/triton_attn (not bit-identical: the masks
are compared by IoU instead).

usage: python scripts/eval/check_track_fast.py --frames <JPEG dir> --pipeline_out <dir with masks/> [--frames_n N]
       [--attn] [--size 1008]
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
import torch  # noqa: E402
from PIL import Image  # noqa: E402


def track(tr, imgs, boxes, n, prefetch):
    from utils.lazy_frames import lazy_jpeg_frames
    from utils.sam3_track_fast import Prefetch
    import offline_app_lite as lite
    with lazy_jpeg_frames():
        st = tr.init_state(video_path=imgs, offload_video_to_cpu=True, offload_state_to_cpu=False)
    if prefetch:
        st["images"] = Prefetch(st["images"])
    for k, box in boxes:
        tr.add_new_points_or_box(inference_state=st, frame_idx=0, obj_id=k, box=box)
    lite.install_pruned_propagation(tr)
    out = []
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for f, ids, _, vm, _, _ in tr.propagate_in_video(st, start_frame_idx=0, max_frame_num_to_track=n, reverse=False,
                                                     propagate_preflight=True, tqdm_disable=True):
        out.append((vm[:, 0] > 0).cpu().numpy())   # as offline_app.on_mask_generation
    dt = time.perf_counter() - t0
    tr.__dict__.pop("propagate_in_video", None)
    return np.stack(out), dt


def main(a):
    import offline_app_lite as lite
    from omegaconf import OmegaConf
    from utils import sam3_track_fast
    torch.set_autocast_cache_enabled(False)   # as inference_lite
    cfg = OmegaConf.load(os.path.join(REPO, "configs", "body4d.yaml"))
    tr = (lite.build_sam3_tracker(cfg.sam3["ckpt_path"]) if a.size == 1008
          else sam3_track_fast.build_tracker_at(cfg.sam3["ckpt_path"], a.size))
    if a.attn:
        sam3_track_fast.install(tr)   # the reference keeps the bit-identical speed-ups; only attention differs
    imgs = sorted(glob.glob(os.path.join(a.frames, "*.jpg")))
    m0 = np.array(Image.open(sorted(glob.glob(os.path.join(a.pipeline_out, "masks", "*.png")))[0]))
    H, W = m0.shape
    boxes = []
    for k in sorted(set(np.unique(m0).tolist()) - {0}):
        ys, xs = np.nonzero(m0 == k)
        boxes.append((int(k), np.array([[xs.min() / W, ys.min() / H, xs.max() / W, ys.max() / H]], np.float32)))
    n = a.frames_n or len(imgs)
    track(tr, imgs, boxes, 8, False)                       # warm-up (cuDNN autotuning, allocator)
    ref, t_ref = track(tr, imgs, boxes, n, False)
    if a.attn:
        from utils import triton_attn
        triton_attn.install()
    else:
        sam3_track_fast.install(tr)
    track(tr, imgs, boxes, 8, True)
    new, t_new = track(tr, imgs, boxes, n, True)
    same = bool(np.array_equal(ref, new))
    diff = int((ref != new).sum())
    print(f"{len(ref)} frames: reference {t_ref:.1f} s ({1000 * t_ref / len(ref):.0f} ms/frame), "
          f"fast {t_new:.1f} s ({1000 * t_new / len(ref):.0f} ms/frame); masks bit-identical: {same} "
          f"({diff} differing pixels of {ref.size})")
    for o in range(ref.shape[1]):
        r, x = ref[:, o].reshape(len(ref), -1), new[:, o].reshape(len(ref), -1)
        un = (r | x).sum(1)
        iou = np.where(un > 0, (r & x).sum(1) / np.maximum(un, 1), 1.0)
        print(f"  object {boxes[o][0]}: mask IoU mean {iou.mean():.4f} min {iou.min():.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True)
    ap.add_argument("--pipeline_out", required=True)
    ap.add_argument("--frames_n", type=int, default=0)
    ap.add_argument("--attn", action="store_true")
    ap.add_argument("--size", type=int, default=1008)
    main(ap.parse_args())
