"""EdgeTAM (facebookresearch/EdgeTAM @ 7711e01, Apache-2.0; a light SAM 2 video tracker) for keyframe_stage.

The repo is not installed: it is read from EDGETAM_ROOT (default C:/VScodeFiles/EdgeTAM, checkpoints/edgetam.pt).
  * build: its `sam2` package needs hydra only to initialise its config module and to compose + instantiate
    edgetam.yaml. OmegaConf (installed) reads the yaml, build_sam2_video_predictor's overrides are applied by hand and a
    small recursive instantiate builds the same objects; timm's ImageNet weights for the RepViT trunk are not
    downloaded (pretrained=False) because the checkpoint holds every weight (loaded with strict key checks).
    `sam2` goes on sys.path only while it is imported (models/sam_3d_body has its own `sam2` imports).
  * frames are read when a frame is first needed (utils/lazy_frames.DiskFrames, one JPEG decode per frame):
    init_state would decode the whole clip up front into 1024x1024 float32 tensors (12.6 MB per frame; 22.6 GB for
    60 s at 30 fps). Same ops and dtypes as sam2.utils.misc._load_img_as_tensor + the in-place normalisation of
    load_video_frames_from_jpg_images, so the tensors are bit-identical (scripts/eval/check_edgetam_tracker.py).
  * one inference state per object (EdgeTAM's spatial perceiver fails on a state holding several objects: a `view`
    of an expanded tensor), all reading one backbone run per frame (cached_features).
  * after each frame, outputs no later frame of a forward propagation reads are dropped: frame u reads the memory of
    u-1 .. u-(num_maskmem-1) and the object pointers of u-1 .. u-(max_obj_ptrs_in_encoder-1) (sam2_base.
    _prepare_memory_conditioned_features, stride 1), plus the conditioning frame, which is kept. Without this every
    tracked frame's outputs stay on the GPU (~0.3 MB per object-frame). Masks are bit-identical with and without the
    pruning (check_edgetam_tracker.py).
Every call runs under torch.inference_mode() and bf16 autocast (as EdgeTAM's demo does). `fill_hole_area` is 0
because the repo's CUDA extension (sam2._C) is not built: with 8, every frame would raise, warn and return the same
masks.
"""
import importlib
import os
import sys
import types
from collections import OrderedDict

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from utils.lazy_frames import DiskFrames  # noqa: E402

EDGETAM = os.environ.get("EDGETAM_ROOT", "C:/VScodeFiles/EdgeTAM").replace("\\", "/")
CKPT = f"{EDGETAM}/checkpoints/edgetam.pt"
CFG = f"{EDGETAM}/sam2/configs/edgetam.yaml"
IMG_MEAN, IMG_STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)   # load_video_frames' defaults


def _import_sam2():
    if "sam2.modeling.backbones.timm" in sys.modules:
        return
    if not os.path.exists(CKPT):
        raise FileNotFoundError(f"EdgeTAM checkpoint not found: {CKPT} (set EDGETAM_ROOT)")
    stubs = {}
    if "hydra" not in sys.modules:   # only the names sam2/__init__.py and build_sam.py import; none is called
        hydra = types.ModuleType("hydra")
        hydra.initialize_config_module = lambda *a, **k: None
        hydra.compose = None
        gh = types.ModuleType("hydra.core.global_hydra")

        class GlobalHydra:
            @staticmethod
            def instance():
                return GlobalHydra

            @staticmethod
            def is_initialized():
                return True
        gh.GlobalHydra = GlobalHydra
        utils = types.ModuleType("hydra.utils")
        utils.instantiate = None
        stubs = {"hydra": hydra, "hydra.core": types.ModuleType("hydra.core"), "hydra.core.global_hydra": gh,
                 "hydra.utils": utils}
        sys.modules.update(stubs)
    sys.path.insert(0, EDGETAM)
    try:
        import sam2  # noqa: F401
        import sam2.build_sam  # noqa: F401
        import sam2.modeling.backbones.timm as tb
        import sam2.sam2_video_predictor as vp
    finally:
        for k in stubs:
            sys.modules.pop(k, None)
        sys.path.remove(EDGETAM)
    create = tb.create_model
    tb.create_model = lambda *a, **k: create(*a, **{**k, "pretrained": False})
    vp.tqdm = lambda it, **k: it   # one progress bar per object state otherwise


def _inst(x):
    if isinstance(x, dict):
        if "_target_" in x:
            mod, _, name = x["_target_"].rpartition(".")
            fn = getattr(importlib.import_module(mod), name)
            return fn(**{k: _inst(v) for k, v in x.items() if k != "_target_"})
        return {k: _inst(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_inst(v) for v in x]
    return x


def build_video_predictor(device="cuda"):
    """sam2.build_sam.build_sam2_video_predictor("edgetam.yaml", edgetam.pt) without hydra (see module docstring)."""
    from omegaconf import OmegaConf
    _import_sam2()
    cfg = OmegaConf.to_container(OmegaConf.load(CFG), resolve=True)["model"]
    cfg["_target_"] = "sam2.sam2_video_predictor.SAM2VideoPredictor"
    cfg["sam_mask_decoder_extra_args"] = dict(dynamic_multimask_via_stability=True,
                                              dynamic_multimask_stability_delta=0.05,
                                              dynamic_multimask_stability_thresh=0.98)
    cfg["binarize_mask_from_pts_for_mem_enc"] = True
    cfg["fill_hole_area"] = 0
    model = _inst(cfg)
    sd = torch.load(CKPT, map_location="cpu", weights_only=True)["model"]
    missing, unexpected = model.load_state_dict(sd)
    if missing or unexpected:
        raise RuntimeError(f"EdgeTAM checkpoint mismatch: missing {missing[:5]}, unexpected {unexpected[:5]}")
    return model.to(device).eval()


def _ctx():
    import contextlib
    cm = contextlib.ExitStack()
    cm.enter_context(torch.inference_mode())
    cm.enter_context(torch.autocast("cuda", dtype=torch.bfloat16))
    return cm


class Track:
    """One object's inference state and its propagate_in_video generator."""

    def __init__(self, state, gen, t0):
        self.state, self.gen, self.t0 = state, gen, t0


class EdgeTAMTracker:
    """Shared per-frame features, one state per object (see module docstring). prune=False keeps every output."""

    def __init__(self, frame_paths, prune=True, device="cuda"):
        self.pred = build_video_predictor(device)
        self.frames = DiskFrames(frame_paths, self.pred.image_size, IMG_MEAN, IMG_STD)
        self.T, self.device, self.prune = len(self.frames), torch.device(device), prune
        p = self.pred
        assert p.memory_temporal_stride_for_eval == 1 and not p.training
        # frames before t - keep + 1 are never read again once frame t is done
        self.keep = max(min(self.T, p.max_obj_ptrs_in_encoder) - 1, p.num_maskmem - 1)

    def features(self, i):
        """forward_image of frame i, as _get_image_feature computes it on a cache miss."""
        with _ctx():
            img = self.frames[i].to(self.device).float().unsqueeze(0)
            return img, self.pred.forward_image(img)

    def _new_state(self):
        """init_state's dict, without decoding the clip and without its warm-up feature of frame 0."""
        return dict(images=self.frames, num_frames=self.T, offload_video_to_cpu=True, offload_state_to_cpu=False,
                    video_height=self.frames.video_height, video_width=self.frames.video_width, device=self.device,
                    storage_device=self.device, point_inputs_per_obj={}, mask_inputs_per_obj={},
                    cached_features={}, constants={}, obj_id_to_idx=OrderedDict(), obj_idx_to_id=OrderedDict(),
                    obj_ids=[], output_dict={"cond_frame_outputs": {}, "non_cond_frame_outputs": {}},
                    output_dict_per_obj={}, temp_output_dict_per_obj={},
                    consolidated_frame_inds={"cond_frame_outputs": set(), "non_cond_frame_outputs": set()},
                    tracking_has_started=False, frames_already_tracked={})

    def start(self, i, feats, mask):
        """A new object from its mask (bool H x W) on frame i -> (Track, mask on frame i)."""
        st = self._new_state()
        st["cached_features"] = {i: feats}
        with _ctx():
            self.pred.add_new_mask(st, frame_idx=i, obj_id=1, mask=torch.from_numpy(mask))
        tr = Track(st, self.pred.propagate_in_video(st, start_frame_idx=i, max_frame_num_to_track=self.T), i)
        return tr, self.step(tr, i, feats)

    def step(self, tr, i, feats):
        """The object's mask (bool H x W, numpy) on frame i; frames must come in order."""
        tr.state["cached_features"] = {i: feats}
        with _ctx():
            out = next(tr.gen)
        assert out[0] == i, (out[0], i)
        if self.prune:
            nc = tr.state["output_dict"]["non_cond_frame_outputs"]
            old = [f for f in nc if f <= i - self.keep]
            for f in old:
                del nc[f]
                for d in tr.state["output_dict_per_obj"].values():
                    d["non_cond_frame_outputs"].pop(f, None)
        return (out[2][0, 0] > 0).cpu().numpy()

    @staticmethod
    def stop(tr):
        tr.gen.close()
        tr.state = tr.gen = None
