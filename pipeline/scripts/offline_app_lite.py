"""Low-VRAM variant of scripts/offline_app.py for 12 GB GPUs (RTX 4070 SUPER).

Same models, same weights, same per-stage computations; only *what is resident on the GPU when* changes:

  offline_app.py                          offline_app_lite.py
  ------------------------------------    ------------------------------------------------------------
  all four models loaded at start         one stage at a time, each model freed after its stage
  SAM3 = full video model (detector       SAM3 = tracker + vision backbone only (the text encoder and the
         transformer, text encoder, ...)         detector heads are never used with box prompts)
  first frame: ViTDet + full HMR + MoGe   first frame: ViTDet only (only the detector's box is used;
               (only the box is used)                  process_one_image returns it unchanged)
  all video frames on GPU (fp32, 1008^2)  video frames on CPU, moved to GPU one at a time
  MoGe-2 resident for the whole run       MoGe-2 loaded for the single cam-intrinsics call, then freed

Every model is constructed under utils.skip_init.skip_random_init(): its random weight initialisation (overwritten by
the checkpoint right after) is skipped. scripts/eval/check_skip_init.py verifies, per model, that all parameters and
buffers after loading are identical to a normal build; no lite stage draws random numbers after construction.

SAM3's tracker enters a process-wide bf16 autocast when it is built (sam3_tracking_predictor.py), so in
offline_app.py every later GPU op outside on_4d_generation (including the ViTDet detection) runs under bf16.
Detection runs before SAM3 is built here, so it gets the same bf16 autocast explicitly.

usage (repo root):
  .venv/Scripts/python.exe -u scripts/offline_app_lite.py --input_video <frames dir | .mp4> --output_dir <dir>
      [--capture <pkl>]   per-frame HMR outputs + stage timings, same format as scripts/eval/run_profiled.py
      [--offload_state]   keep SAM3's per-frame tracking memory on CPU (long videos)
      [--avatar_out <dir>] then replace the targets with the VRM avatar (scripts/avatar_app.py, fusion v2 occlusion)
                           in a fresh process, so that none of this process's GPU memory is held
      [--vrm <file>]       avatar for --avatar_out (default: avatar_app.py's)
"""
import argparse
import gc
import glob
import os
import pickle
import sys
import time

import cv2
import numpy as np
import torch
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_app as oa  # noqa: E402  (sets sys.path for models/*)
from utils.skip_init import skip_random_init  # noqa: E402
from omegaconf import OmegaConf  # noqa: E402

CAPTURE = {"detection": [], "hmr_batches": [], "faces": None, "timing": {}, "stages": {}}


def free_gpu():
    gc.collect()
    torch.cuda.empty_cache()


class Stage:
    """Wall time and peak GPU memory of one stage."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        torch.cuda.synchronize()
        dt = time.perf_counter() - self.t0
        peak = torch.cuda.max_memory_allocated() / 1024 ** 3
        res = torch.cuda.max_memory_reserved() / 1024 ** 3
        CAPTURE["stages"][self.name] = dict(time_s=dt, peak_alloc_GB=peak, peak_reserved_GB=res)
        print(f"[STAGE][{self.name}] time={dt:.2f} s, peak_alloc={peak:.2f} GB, peak_reserved={res:.2f} GB",
              flush=True)


class StagedFOV:
    """MoGe-2, alive only for the one get_cam_intrinsics call that on_4d_generation makes (uni-cam-int)."""

    def __init__(self, path, device):
        self.path, self.device = path, device

    def get_cam_intrinsics(self, img, **kw):
        from models.sam_3d_body.tools.build_fov_estimator import FOVEstimator
        with skip_random_init():
            est = FOVEstimator(name="moge2", device=self.device, path=self.path)
        try:
            return est.get_cam_intrinsics(img, **kw)
        finally:
            del est
            free_gpu()


def build_sam3_tracker(ckpt_path):
    """SAM3 tracker with its own vision backbone, loaded from the full SAM3 video checkpoint.
    In the video model the tracker borrows detector.backbone (vision + text); the tracker only ever calls
    backbone.forward_image, so the vision part is enough. strict=True: every tracker weight must be present."""
    from models.sam3.sam3.model_builder import build_tracker
    with skip_random_init():
        tracker = build_tracker(apply_temporal_disambiguation=True, with_backbone=True)
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    if "model" in ckpt and isinstance(ckpt["model"], dict):
        ckpt = ckpt["model"]
    sd = {k[len("tracker."):]: v for k, v in ckpt.items() if k.startswith("tracker.")}
    pre = "detector.backbone.vision_backbone."
    sd.update({"backbone.vision_backbone." + k[len(pre):]: v for k, v in ckpt.items() if k.startswith(pre)})
    del ckpt
    tracker.load_state_dict(sd, strict=True)
    del sd
    return tracker.to("cuda").eval()  # Sam3VideoBase.__init__ puts the whole video model in eval mode


def detect_first_frame(cfg, input_video):
    """ViTDet on the first frame with a person, exactly the frame scan and thresholds of offline_app.inference().
    Returns (starting_frame_idx, boxes, width, height, image_list or None)."""
    from models.sam_3d_body.tools.build_detector import HumanDetector
    from models.sam_3d_body.sam_3d_body.data.utils.io import load_image
    with skip_random_init():
        det = HumanDetector(name="vitdet", device=torch.device("cuda"), path=cfg.sam_3d_body["detector_path"])

    def run(img_bgr):
        boxes = det.run_human_detection(img_bgr, det_cat_id=0, bbox_thr=0.6, nms_thr=0.3,
                                        default_to_full_image=False)
        print("Found boxes:", boxes)
        CAPTURE["detection"].append({"outputs": [{"bbox": b.copy()} for b in boxes]})
        return boxes

    try:
        with torch.autocast("cuda", dtype=torch.bfloat16):  # see module docstring
            if os.path.isfile(input_video) and input_video.endswith(".mp4"):
                width, height = oa.read_frame_at(input_video, 0).size
                for idx in range(10, 100):
                    img = cv2.cvtColor(np.array(oa.read_frame_at(input_video, idx)), cv2.COLOR_RGB2BGR)
                    boxes = run(img)
                    if len(boxes) > 0:
                        break
                return idx, boxes, width, height, None
            image_list = sorted(glob.glob(os.path.join(input_video, "*.jpg")))
            width, height = Image.open(image_list[0]).convert("RGB").size
            idx = 0
            for p in image_list:
                boxes = run(load_image(p, backend="cv2", image_format="bgr"))
                if len(boxes) > 0:
                    break
                idx += 1
            return idx, boxes, width, height, image_list
    finally:
        del det
        free_gpu()


def build_app(cfg, output_dir):
    """An OfflineApp with no models loaded; models are attached per stage."""
    app = oa.OfflineApp.__new__(oa.OfflineApp)
    app.CONFIG = cfg
    app.sam3_model = None
    app.predictor = None
    app.sam3_3d_body_model = None
    app.pipeline_mask = app.pipeline_rgb = app.depth_model = app.max_occ_len = app.generator = None
    app.RUNTIME = {
        "batch_size": cfg.sam_3d_body.get("batch_size", 1),
        "detection_resolution": cfg.completion.get("detection_resolution", [256, 512]),
        "completion_resolution": cfg.completion.get("completion_resolution", [512, 1024]),
        "smpl_export": cfg.runtime.get("smpl_export", False),
        "async_save": True,      # save batch b (CPU / OpenGL) while the GPU runs batch b+1
        "bboxes": None,
    }
    app.OUTPUT_DIR = output_dir
    os.makedirs(output_dir, exist_ok=True)
    return app


def capture_hmr():
    """Same per-batch capture as scripts/eval/run_profiled.py (for comparison against reference runs)."""
    orig = oa.process_image_with_mask

    def wrapped(estimator, image_path, mask_path, *a, **k):
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        res = orig(estimator, image_path, mask_path, *a, **k)
        torch.cuda.synchronize()
        dt = time.perf_counter() - t0
        outputs, id_batch, empty = res
        print(f"[TIME][process_image_with_mask] frames={len(image_path)} time={dt:.2f} s", flush=True)
        CAPTURE["faces"] = estimator.faces
        CAPTURE["hmr_batches"].append({
            "image_path": list(image_path), "mask_path": list(mask_path),
            "outputs": [[{k2: (v.detach().cpu().numpy() if torch.is_tensor(v) else v) for k2, v in p.items()}
                         for p in o] for o in outputs],
            "id_batch": id_batch, "empty_frame_list": list(empty), "time_s": dt,
        })
        return res

    oa.process_image_with_mask = wrapped


def inference_lite(args):
    cfg = OmegaConf.load(os.path.join(oa.ROOT, "configs", "body4d.yaml"))
    app = build_app(cfg, args.output_dir)

    # 1. person boxes on the first frame with a person (ViTDet only)
    with Stage("detect"):
        start_idx, boxes, width, height, image_list = detect_first_frame(cfg, args.input_video)
        boxes = [boxes[i] for i in oa.select_targets(boxes, cfg.runtime.get("max_targets", 2))]

    # 2. SAM3 tracking (tracker + vision backbone; frames on CPU)
    with Stage("sam3_load"):
        app.predictor = build_sam3_tracker(cfg.sam3["ckpt_path"])
    with Stage("sam3_init_state"):
        state = app.predictor.init_state(video_path=args.input_video if image_list is None else image_list,
                                         offload_video_to_cpu=True, offload_state_to_cpu=args.offload_state)
        app.predictor.clear_all_points_in_video(state)
        app.RUNTIME["inference_state"] = state
        app.RUNTIME["out_obj_ids"] = []
        for obj_id, (xmin, ymin, xmax, ymax) in enumerate(boxes):
            rel_box = np.array([[xmin / width, ymin / height, xmax / width, ymax / height]], dtype=np.float32)
            _, app.RUNTIME["out_obj_ids"], _, _ = app.predictor.add_new_points_or_box(
                inference_state=state, frame_idx=start_idx, obj_id=obj_id + 1, box=rel_box)
    with Stage("sam3_propagate"):
        app.on_mask_generation(start_frame_idx=0)
    obj_ids = list(app.RUNTIME["out_obj_ids"])
    app.RUNTIME["inference_state"] = None
    app.predictor = None
    del state
    free_gpu()

    # 3. HMR (SAM-3D-Body; MoGe-2 only for the single intrinsics call inside on_4d_generation)
    with Stage("hmr_load"):
        dev = torch.device("cuda")
        with skip_random_init():
            model, model_cfg = oa.load_sam_3d_body(cfg.sam_3d_body["ckpt_path"], device=dev,
                                                   mhr_path=cfg.sam_3d_body["mhr_path"])
        app.sam3_3d_body_model = oa.SAM3DBodyEstimator(
            sam_3d_body_model=model, model_cfg=model_cfg, human_detector=None, human_segmentor=None,
            fov_estimator=StagedFOV(cfg.sam_3d_body["fov_path"], dev))
    app.RUNTIME["out_obj_ids"] = obj_ids
    with Stage("hmr_4d"):
        with torch.autocast("cuda", enabled=False):
            app.on_4d_generation()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Low-VRAM offline 4D body generation (see module docstring)")
    p.add_argument("--input_video", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--capture", default=None)
    p.add_argument("--offload_state", action="store_true")
    p.add_argument("--avatar_out", default=None)
    p.add_argument("--vrm", default=None)
    args = p.parse_args()
    if not os.path.exists(args.input_video):
        raise FileNotFoundError(args.input_video)
    if args.capture:
        capture_hmr()

    t0 = time.perf_counter()
    inference_lite(args)
    total = time.perf_counter() - t0
    print(f"[TIME][total inference_lite()] {total:.2f} s")
    CAPTURE["timing"]["total_s"] = total
    CAPTURE["timing"]["max_memory_reserved_GB"] = max(s["peak_reserved_GB"] for s in CAPTURE["stages"].values())
    if args.capture:
        os.makedirs(os.path.dirname(os.path.abspath(args.capture)), exist_ok=True)
        with open(args.capture, "wb") as f:
            pickle.dump(CAPTURE, f)
        print(f"[CAPTURE] saved {args.capture}")
    if args.avatar_out:
        import subprocess
        cmd = [sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), "avatar_app.py"),
               "--pipeline_out", args.output_dir, "--out", args.avatar_out] + (["--vrm", args.vrm] if args.vrm else [])
        sys.exit(subprocess.call(cmd))
