"""Build EdgeTAM (C:/VScodeFiles/EdgeTAM, facebookresearch/EdgeTAM @ 7711e01) without installing anything.

The repo's `sam2` package needs hydra only to initialise its config module (sam2/__init__.py) and to compose +
instantiate edgetam.yaml (sam2/build_sam.py). Here OmegaConf (already installed) reads the yaml, the overrides of
build_sam2_video_predictor / build_sam2 are applied by hand, and a small recursive instantiate builds the same
objects. timm's ImageNet weights for the RepViT trunk are not downloaded (pretrained=False): the EdgeTAM checkpoint
holds every weight and is loaded with strict key checks, so the result is the same model.
"""
import importlib
import sys
import types

import torch

EDGETAM = "C:/VScodeFiles/EdgeTAM"
CKPT = f"{EDGETAM}/checkpoints/edgetam.pt"
CFG = f"{EDGETAM}/sam2/configs/edgetam.yaml"


def _import_sam2():
    if "sam2.modeling.backbones.timm" in sys.modules:
        return
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
    if EDGETAM not in sys.path:
        sys.path.insert(0, EDGETAM)
    try:
        import sam2  # noqa: F401
        import sam2.build_sam  # noqa: F401
        import sam2.modeling.backbones.timm as tb
    finally:
        for k in stubs:
            sys.modules.pop(k, None)
    create = tb.create_model
    tb.create_model = lambda *a, **k: create(*a, **{**k, "pretrained": False})


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


def build(kind="video", device="cuda", postprocessing=True):
    """kind 'video': SAM2VideoPredictor (build_sam2_video_predictor); 'image': SAM2Base for SAM2ImagePredictor."""
    from omegaconf import OmegaConf
    _import_sam2()
    cfg = OmegaConf.to_container(OmegaConf.load(CFG), resolve=True)["model"]
    if kind == "video":
        cfg["_target_"] = "sam2.sam2_video_predictor.SAM2VideoPredictor"
        if postprocessing:
            cfg["sam_mask_decoder_extra_args"] = dict(dynamic_multimask_via_stability=True,
                                                      dynamic_multimask_stability_delta=0.05,
                                                      dynamic_multimask_stability_thresh=0.98)
            cfg["binarize_mask_from_pts_for_mem_enc"] = True
            # build_sam2_video_predictor sets 8, but the hole filling needs the repo's CUDA extension (sam2._C),
            # which is not built here: every frame would raise, warn and return the masks unchanged. 0 skips it
            # directly (same masks).
            cfg["fill_hole_area"] = 0
    elif postprocessing:
        cfg["sam_mask_decoder_extra_args"] = dict(dynamic_multimask_via_stability=True,
                                                  dynamic_multimask_stability_delta=0.05,
                                                  dynamic_multimask_stability_thresh=0.98)
    model = _inst(cfg)
    sd = torch.load(CKPT, map_location="cpu", weights_only=True)["model"]
    missing, unexpected = model.load_state_dict(sd)
    if missing or unexpected:
        raise RuntimeError(f"EdgeTAM checkpoint mismatch: missing {missing[:5]}, unexpected {unexpected[:5]}")
    return model.to(device).eval()
