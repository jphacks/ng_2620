"""Faster SAM3 person tracking (offline_app_lite's tracker) with bit-identical masks.

Only where and when the same work happens changes, not what is computed:
  1. bf16 weights: the tracker's vision backbone runs only under SAM3's process-wide bf16 autocast, and lite turns
     autocast's weight cache off, so every Linear / Conv recast its fp32 weight on every frame (~445M parameters).
     The weights are stored in bf16 once instead (utils/bf16_weights.py: the same bf16 values).
  2. tracker-only neck: Sam3DualViTDetNeck computes the SAM3 neck (the detector's) and the SAM2 neck (the tracker's)
     for all four scales, and the backbone then drops the lowest scale of both (scalp=1). The tracker reads only the
     SAM2 neck's three kept scales, so only those are computed.
  3. frame prefetch: LazyFrames (utils/lazy_frames.py) decoded and resized each frame when the tracker asked for it,
     between two frames' GPU work. The next frames are decoded in a thread, into pinned memory (so the tracker's
     .cuda() copy is faster); same ops, same values.
On downtown_bar (120 frames, RTX 5060 Ti) scripts/eval/check_track_fast.py checks the masks bit for bit.
"""
import types
from concurrent.futures import ThreadPoolExecutor

import torch

from utils.bf16_weights import bf16_weights


def tracker_only_neck(tracker):
    """2. above. tracker: the lite tracker (with its own backbone)."""
    bb = tracker.backbone
    neck = bb.vision_backbone
    assert neck.sam2_convs is not None
    keep = len(neck.sam2_convs) - bb.scalp

    def forward(self, tensor_list):
        x = self.trunk(tensor_list)[-1]
        out, pos = [], []
        for conv in self.sam2_convs[:keep]:
            y = conv(x)
            out.append(y)
            pos.append(self.position_encoding(y).to(y.dtype))
        # the SAM3 lists alias the SAM2 ones (nothing on the tracker path reads them); one extra level that scalp drops
        out.append(out[-1])
        pos.append(pos[-1])
        return out, pos, out, pos

    neck.forward = types.MethodType(forward, neck)


def install(tracker):
    """1. and 2. above on a tracker built by offline_app_lite.build_sam3_tracker."""
    bf16_weights(tracker.backbone)
    tracker_only_neck(tracker)
    torch.cuda.empty_cache()


class Prefetch:
    """3. above: wraps a LazyFrames; frames i+1..i+depth are decoded ahead whenever frame i is read."""

    def __init__(self, frames, depth=3):
        self.frames, self.depth = frames, depth
        self.pool = ThreadPoolExecutor(1)
        self.fut = {}
        for k in ("video_width", "video_height"):
            setattr(self, k, getattr(frames, k))

    def _load(self, i):
        return self.frames[i].pin_memory()

    def __len__(self):
        return len(self.frames)

    def __getitem__(self, i):
        f = self.fut.pop(i, None)
        img = f.result() if f is not None else self._load(i)
        for j in range(i + 1, min(i + 1 + self.depth, len(self.frames))):
            if j not in self.fut:
                self.fut[j] = self.pool.submit(self._load, j)
        for j in [j for j in self.fut if j < i]:
            self.fut.pop(j).cancel()
        return img


def build_tracker_at(ckpt_path, image_size):
    """offline_app_lite.build_sam3_tracker with the tracker's input resolution set to image_size instead of 1008
    (NOT the trained setting: masks differ; see scripts/eval/check_track_res.py). image_size must be a multiple of
    14 * 24 (patch size times the ViT window): 672 or 1008. What follows the size:
      * the ViT's global-attention RoPE: rebuilt for the new grid, interpolated from the same 24x24 pretraining grid
        (rope_interp), so it spans the same position range as at 1008; the window blocks are unchanged
      * the memory encoder's mask resize (1152 at 1008: 16x the feature grid)
      * the tracker's image size (prompt encoder grid, low-res mask size, frame resize in init_state)
    The memory attention's RoPE follows the feature grid on its own (RoPEAttention recomputes it)."""
    from unittest import mock
    import sam3.model_builder as mb
    from utils.skip_init import skip_random_init
    grid = image_size // 14
    assert image_size % (14 * 24) == 0, image_size

    def forced(cls, **over):   # the builder passes the 1008 values as keywords: override them
        return lambda *a, **k: cls(*a, **{**k, **over})
    with mock.patch.object(mb, "ViT", forced(mb.ViT, img_size=image_size)), \
            mock.patch.object(mb, "SimpleMaskDownSampler",
                              forced(mb.SimpleMaskDownSampler, interpol_size=[16 * grid, 16 * grid])), \
            mock.patch.object(mb, "Sam3TrackerPredictor", forced(mb.Sam3TrackerPredictor, image_size=image_size)), \
            skip_random_init():
        tracker = mb.build_tracker(apply_temporal_disambiguation=True, with_backbone=True)
    with open(ckpt_path, "rb") as f:
        use_mmap = f.read(4) == b"PK\x03\x04"
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True, mmap=use_mmap)
    if "model" in ckpt and isinstance(ckpt["model"], dict):
        ckpt = ckpt["model"]
    sd = {k[len("tracker."):]: v for k, v in ckpt.items() if k.startswith("tracker.")}
    pre = "detector.backbone.vision_backbone."
    sd.update({"backbone.vision_backbone." + k[len(pre):]: v for k, v in ckpt.items() if k.startswith(pre)})
    del ckpt
    own = tracker.state_dict()
    # RoPE tables are buffers saved with the checkpoint: keep the ones built for this grid
    skipped = [k for k, v in sd.items() if k.endswith("freqs_cis") and own[k].shape != v.shape]
    for k in skipped:
        sd[k] = own[k]
    tracker.load_state_dict(sd, strict=True)
    return tracker.to("cuda").eval()


def write_images_as_1008(frames, out_dir, out_h, out_w, start=0):
    """Rewrite <out_dir>/images/*.jpg as offline_app.on_mask_generation writes them from a 1008 tracker.

    on_mask_generation saves images/ (what HMR, fusion and the avatar read) from the tracker's own input frames,
    scaled back to the video size. With a lower tracker resolution (build_tracker_at) those frames are blurrier, which
    moved HMR's depth by ~4 cm on downtown_bar. frames: the run's LazyFrames; its JPEG bytes are rebuilt at 1008 and
    written with on_mask_generation's ops, so images/ is byte-identical to a 1008 run's."""
    import copy
    import os
    import torch.nn.functional as F
    from PIL import Image
    f = copy.copy(frames)
    f.image_size = 1008
    for i in range(len(f)):
        img = ((f[i].float() + 1) / 2).clamp(0, 1)
        img = F.interpolate(img.unsqueeze(0), size=(out_h, out_w), mode="bilinear", align_corners=False).squeeze(0)
        img = (img.permute(1, 2, 0).numpy() * 255).astype("uint8")
        Image.fromarray(img).convert("RGB").save(os.path.join(out_dir, "images", f"{i + start:08d}.jpg"))
