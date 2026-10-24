"""SAM3's video tracker (with its vision backbone) behind edgetam_tracker's interface, for the objects keyframe_stage
tracks with it (thin objects in front of a target, see keyframe_stage.THIN): one state per object, one backbone run
per frame shared by all of them.

  * the tracker is built as offline_app_lite.build_sam3_tracker does (the 'tracker.' weights of the SAM3 checkpoint
    plus the detector's vision backbone), without the random init that the checkpoint overwrites;
  * frames come from utils/lazy_frames.DiskFrames (1008, mean / std 0.5: SAM3's loader), read when needed;
  * each object's propagation goes through utils/tracker_prune.prune_propagation (outputs no later frame can select
    are dropped; masks bit-identical).
Same calls as the prototype's Sam3Backend (scripts/occlusion/keyframe/kf_stage.py). It runs under the bf16 autocast
that SAM3 keeps open process-wide (gpu_stage_fast.enable_sam3_globals / building SAM3 on the main thread).
"""
import os
import sys

import torch

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
for p in (REPO, os.path.join(REPO, "models", "sam3")):
    if p not in sys.path:
        sys.path.insert(0, p)
from utils.lazy_frames import DiskFrames  # noqa: E402
from utils.skip_init import skip_random_init  # noqa: E402

CKPT = "C:/VScodeFiles/checkpoints/sam3/sam3.pt"


def build_sam3_tracker(ckpt_path=CKPT):
    from sam3.model_builder import build_tracker
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


class Track:
    def __init__(self, state, gen, t0):
        self.state, self.gen, self.t0 = state, gen, t0


class Sam3Tracker:
    def __init__(self, frame_paths, H, W):
        self.tr = build_sam3_tracker()
        self.frames = DiskFrames(frame_paths, self.tr.image_size, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
        self.T, self.H, self.W = len(self.frames), H, W

    def features(self, i):
        with torch.inference_mode():
            img = self.frames[i].cuda().float().unsqueeze(0)
            return img, self.tr.forward_image(img)

    def start(self, i, feats, mask):
        from utils.tracker_prune import prune_propagation
        st = self.tr.init_state(video_height=self.H, video_width=self.W, num_frames=self.T,
                                cached_features={i: feats}, offload_video_to_cpu=True)
        st["images"] = self.frames
        self.tr.add_new_mask(inference_state=st, frame_idx=i, obj_id=1, mask=torch.from_numpy(mask))
        gen = prune_propagation(self.tr, st, self.tr.propagate_in_video(
            st, start_frame_idx=i, max_frame_num_to_track=self.T, reverse=False, tqdm_disable=True,
            propagate_preflight=True))
        tr = Track(st, gen, i)
        return tr, self.step(tr, i, feats)

    def step(self, tr, i, feats):
        tr.state["cached_features"] = {i: feats}
        out = next(tr.gen)
        assert out[0] == i, (out[0], i)
        return (out[3][0, 0] > 0).cpu().numpy()

    @staticmethod
    def stop(tr):
        tr.gen.close()
        tr.state = tr.gen = None
