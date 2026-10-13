"""Is utils.skip_init.skip_random_init() exact for each pipeline model? Builds every model twice (normally and with
the random init skipped, same seed), loads its checkpoint, and compares all parameters and buffers, persistent or not.
Prints the construction times. usage (repo root): .venv/Scripts/python.exe scripts/eval/check_skip_init.py [names]
"""
import gc
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "scripts"), ROOT, os.path.join(ROOT, "models", "sam3")]
os.chdir(ROOT)
import torch  # noqa: E402
from omegaconf import OmegaConf  # noqa: E402

from utils.skip_init import skip_random_init  # noqa: E402

cfg = OmegaConf.load(os.path.join(ROOT, "configs", "body4d.yaml"))
dev = torch.device("cuda")


def modules(obj, seen=None):
    """All nn.Modules reachable from obj's attributes (the detector / FOV wrappers hold their models as attributes)."""
    if isinstance(obj, torch.nn.Module):
        return [("", obj)]
    out = []
    for k, v in vars(obj).items():
        if isinstance(v, torch.nn.Module):
            out.append((k, v))
    return out


def tensors(obj):
    t = {}
    for pre, m in modules(obj):
        for n, p in m.named_parameters(remove_duplicate=False):
            t[f"{pre}.p.{n}"] = p.detach()
        for n, b in m.named_buffers(remove_duplicate=False):
            t[f"{pre}.b.{n}"] = b.detach()
    return t


def build(name):
    if name == "sam3_tracker":
        import offline_app_lite as L
        return L.build_sam3_tracker(cfg.sam3["ckpt_path"])
    if name == "hmr":
        import offline_app as oa
        return oa.load_sam_3d_body(cfg.sam_3d_body["ckpt_path"], device=dev, mhr_path=cfg.sam_3d_body["mhr_path"])[0]
    if name == "vitdet":
        from models.sam_3d_body.tools.build_detector import HumanDetector
        return HumanDetector(name="vitdet", device=dev, path=cfg.sam_3d_body["detector_path"])
    if name == "moge":
        from models.sam_3d_body.tools.build_fov_estimator import FOVEstimator
        return FOVEstimator(name="moge2", device=dev, path=cfg.sam_3d_body["fov_path"])
    if name == "sam3_image":
        from sam3.model_builder import build_sam3_image_model
        return build_sam3_image_model(checkpoint_path=cfg.sam3["ckpt_path"], load_from_HF=False,
                                      enable_inst_interactivity=True)
    raise KeyError(name)


def run(name):
    res = {}
    for mode in ("normal", "skip"):
        torch.manual_seed(0)
        t0 = time.perf_counter()
        if mode == "skip":
            with skip_random_init():
                obj = build(name)
        else:
            obj = build(name)
        torch.cuda.synchronize()
        dt = time.perf_counter() - t0
        res[mode] = ({k: v.cpu() for k, v in tensors(obj).items()}, dt)
        del obj
        gc.collect()
        torch.cuda.empty_cache()
    a, ta = res["normal"]
    b, tb = res["skip"]
    diff = [k for k in a if k not in b or a[k].shape != b[k].shape or not torch.equal(a[k], b[k])]
    print(f"[skip_init] {name}: build {ta:.1f} s -> {tb:.1f} s; tensors {len(a)}; differing {len(diff)} "
          f"{diff[:6]}", flush=True)
    return diff


if __name__ == "__main__":
    names = sys.argv[1:] or ["vitdet", "sam3_tracker", "hmr", "moge", "sam3_image"]
    for n in names:
        run(n)
