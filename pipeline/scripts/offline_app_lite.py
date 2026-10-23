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
      [--preload_frames]  SAM3's original frame loading: every frame as 1008x1008 float32 in RAM up front (21.9 GB
                           for 1800 frames). Default: rebuilt per access from the JPEG bytes, bit-identical
                           (utils/lazy_frames.py, scripts/eval/check_lazy_frames.py)
      [--keep_tracker_memory] keep every tracked frame's SAM3 output (+3.9 GB VRAM per 1800 frames). Default: drop
                           the ones no later frame can select (utils/tracker_prune.py; masks bit-identical)
      [--avatar_out <dir>] then replace the targets with the VRM avatar (scripts/avatar_app.py, fusion v2 occlusion)
                           in a fresh process, after releasing this process's cached CUDA tensors and allocations
      [--vrm <file>]       avatar for --avatar_out (default: avatar_app.py's)
      [--avatar_hq]        --avatar_out at the input video's own quality (avatar_app.py --hq)
      [--compile_backbone] torch.compile the HMR backbone (faster HMR; not bit-identical to eager)
      (the hand head always shares the body head's MHR model, and both heads run hmr_fast's sync-free code)
      [--hmr_input_size N] HMR crop size (multiple of 16; default 384, trained at 512). On 11 3DPW clips 384 vs 512:
                           PA-MPJPE 35.27 vs 35.27 mm, MPJPE 57.81 vs 55.62 mm, hmr_4d -19% (testdata/2026-10-14/res_eval2,
                           scripts/eval/gt_3dpw_hmr.py). Pass 512 for the original model input.
      [--save_workers N]   threads saving meshes / overlays while HMR runs (default 2; GL drawing stays on one thread)
      [--keep_rejected_hands] run the hand pass on every hand crop, as the original does (default: skip the crops
                           run_inference rejects by box size anyway; see skip_rejected_hands)
      [--slow_tracker]     SAM3 tracking without utils/sam3_track_fast (bf16 backbone weights, the tracker's neck only,
                           frames decoded ahead): masks bit-identical either way, 341 -> 310 ms/frame on downtown_bar
                           (RTX 5060 Ti, scripts/eval/check_track_fast.py)
      [--tracker_size N]   SAM3 tracker input resolution (default 672; 1008 is the trained one). 672: 3x faster
                           tracking, mask IoU vs 1008 0.96-0.98, joints ~7 mm, avatar output judged visually fine
                           (scripts/eval/check_track_res.py, utils/sam3_track_fast.build_tracker_at)
      [--sdpa_attn]        the tracker's memory attention through PyTorch SDPA. Default: utils/triton_attn (PyTorch's
                           Windows wheels have no flash kernel): 323 -> 270 ms/frame at 1008, 111 -> 95 at 672; masks
                           not bit-identical (IoU 0.994-0.999 vs SDPA, scripts/eval/check_track_fast.py --attn)
      (--tracker_size 1008 --sdpa_attn --slow_tracker: the original tracking, bit for bit)
      [--moge_ahead]       compute fusion v2's MoGe-2 depth in a second process while HMR runs
                           (scripts/occlusion/moge_ahead.py; the same bytes as fusion's own MoGe, which then skips it).
                           Off by default: on an RTX 5060 Ti the two processes share the GPU by time slicing, HMR grew
                           17.7 -> 29.0 s, and the end-to-end time only moved 111.9 -> 111.3 s (downtown_bar)
"""
import argparse
import contextlib
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
from utils.bf16_weights import bf16_weights  # noqa: E402
from utils import vitdet_attn  # noqa: E402
from utils.stream_blocks import stream_blocks  # noqa: E402
from utils.compile_cache import use_persistent_compile_cache  # noqa: E402
from utils.lazy_frames import lazy_jpeg_frames  # noqa: E402
from utils.tracker_prune import prune_propagation  # noqa: E402
from utils import sam3_track_fast  # noqa: E402
from utils.lazy_frames import LazyFrames  # noqa: E402
import hmr_fast  # noqa: E402
from omegaconf import OmegaConf  # noqa: E402

CAPTURE = {"detection": [], "hmr_batches": [], "faces": None, "timing": {}, "stages": {}}


def install_pruned_propagation(predictor):
    """predictor.propagate_in_video through utils.tracker_prune.prune_propagation (an instance attribute; remove it
    after the propagation so the tracker can be freed)."""
    full = type(predictor).propagate_in_video

    def pruned(inference_state, *a, **k):
        return prune_propagation(predictor, inference_state, full(predictor, inference_state, *a, **k),
                                 reverse=k.get("reverse", False))
    predictor.propagate_in_video = pruned


def free_gpu():
    gc.collect()
    # SAM3's builder leaves a bf16 autocast open for the whole process, and autocast keeps its bf16 weight copies
    # until that region exits: without this the freed SAM3 tracker's copies (0.9 GB) stay on the GPU through HMR.
    # Dropping them only means a later cast is redone, with the same values.
    torch.clear_autocast_cache()
    torch.cuda.empty_cache()


class Stage:
    """Wall time and peak GPU memory of one stage."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        self.start = torch.cuda.memory_allocated() / 1024 ** 3
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        torch.cuda.synchronize()
        dt = time.perf_counter() - self.t0
        peak = torch.cuda.max_memory_allocated() / 1024 ** 3
        res = torch.cuda.max_memory_reserved() / 1024 ** 3
        CAPTURE["stages"][self.name] = dict(time_s=dt, peak_alloc_GB=peak, peak_reserved_GB=res)
        print(f"[STAGE][{self.name}] time={dt:.2f} s, peak_alloc={peak:.2f} GB, peak_reserved={res:.2f} GB, "
              f"alloc at start={self.start:.2f} GB",
              flush=True)


class StagedFOV:
    """MoGe-2, alive only for the one get_cam_intrinsics call that on_4d_generation makes (uni-cam-int)."""

    def __init__(self, path, device):
        self.path, self.device = path, device
        self.pre = None

    def precompute(self, img):
        """Run the call for `img` now, before the HMR model is on the GPU (MoGe and HMR together made the hmr_4d
        peak; with free_gpu's autocast-cache clear 6.43 -> 3.23 GB allocated on penn 1791). Called under the same autocast(enabled=False) as hmr_4d; the result is
        returned only for an identical image, any other image runs MoGe as before."""
        self.pre = (img.copy(), self._run(img))

    def get_cam_intrinsics(self, img, **kw):
        if self.pre is not None and not kw and np.array_equal(self.pre[0], img):
            return self.pre[1]
        if self.pre is not None:
            print("[fov] not the precomputed image: running MoGe-2 again", flush=True)
        return self._run(img, **kw)

    def _run(self, img, **kw):
        from models.sam_3d_body.tools.build_fov_estimator import FOVEstimator
        with skip_random_init():
            est = FOVEstimator(name="moge2", device=self.device, path=self.path)
        try:
            return est.get_cam_intrinsics(img, **kw)
        finally:
            del est
            free_gpu()


def compile_backbone(backbone, multiple):
    """torch.compile the HMR backbone (DINOv3, 64% of HMR time) with one static graph.

    Inputs are zero-padded to a multiple of `multiple` (the HMR batch size) and the padding is sliced off, so the
    shorter last batch reuses the full-batch graph instead of compiling a new one (~25 s per new shape). Each crop is
    encoded independently, so padding does not change the real crops' features. Not bit-identical to eager bf16; its
    difference from an fp32 backbone is the same size as eager's (testdata/2026-10-14/compile).

    The inductor cache (default %TEMP%/torchinductor_<user>) must sit on a short path: triton's temp file names
    under testdata/... exceeded Windows MAX_PATH.
    """
    compiled = torch.compile(backbone.forward, dynamic=False)

    def forward(x, extra_embed=None):
        n = x.shape[0]
        pad = -n % multiple
        if pad:
            x = torch.cat([x, x.new_zeros((pad, *x.shape[1:]))])
        return compiled(x, extra_embed=extra_embed)[:n]

    backbone.forward = forward


def share_mhr(model):
    """Let the hand head use the body head's MHR body model instead of its own copy (0.65 GB).

    The checkpoint stores the MHR twice (head_pose.mhr, head_pose_hand.mhr) with identical tensors (checked here on
    every run, not assumed), and MHR is a stateless TorchScript module. MHR is not bitwise repeatable by itself; the
    difference after sharing stays below its repeat-to-repeat difference (scripts/eval/check_share_mhr.py).
    """
    a, b = model.head_pose.mhr, model.head_pose_hand.mhr
    ta = dict(a.named_parameters()) | dict(a.named_buffers())
    tb = dict(b.named_parameters()) | dict(b.named_buffers())
    if ta.keys() != tb.keys() or not all(torch.equal(ta[k], tb[k]) for k in ta):
        print("[share_mhr] the two MHR copies differ; keeping both")
        return
    del ta, tb
    model.head_pose_hand.mhr = a
    del b
    free_gpu()


HAND_BOX_SIZE_THRESH = 64  # CRITERIA 2 in SAM3DBody.run_inference


def skip_rejected_hands(model):
    """Run the hand pass (backbone + hand decoder) only on crops whose result run_inference can use.

    run_inference ANDs every hand's result with `bbox_scale > 64` (CRITERIA 2) before it touches the output: wrist IK,
    hand pose/scale/shape and the keypoint prompts are all gated by hand_valid_mask. That test depends only on the hand
    box from the body pass, so it is known before the hand pass. Rejected rows get zeros instead of a computed result.
    On 3DPW / Penn clips 87-100% of hand crops are rejected (median padded box 16-48 px).

    When every crop is rejected, the first such call runs one crop to learn the output layout; later ones only build
    zeros from that template (one 1-crop hand pass costs ~0.25 s, almost all of it launch-bound MHR head work).
    """
    orig = model.forward_step
    template = {}

    def forward_step(batch, decoder_type="body", kps_batch=None):
        if decoder_type != "hand" or kps_batch is not None:
            return orig(batch, decoder_type, kps_batch)
        B, P = batch["img"].shape[:2]
        n = B * P
        keep = (batch["bbox_scale"].flatten(0, 1) > HAND_BOX_SIZE_THRESH).all(dim=1)
        if n == 1 or bool(keep.all()):
            return orig(batch, decoder_type, kps_batch)
        # The model keeps the batch layout from _initialize_batch (the body batch) as state; the hand batch shares it.
        # The kept rows run as a (rows, 1 person) batch, the layout the model flattens (frame, person) batches to.
        assert model._batch_size == B and (model._max_num_person or 1) == P, (batch["img"].shape, model._batch_size)
        idx = keep.nonzero().flatten()
        run_idx = idx if len(idx) else torch.zeros(1, dtype=torch.long, device=keep.device)
        m = len(run_idx)

        def scatter(v):
            if isinstance(v, dict):
                return {k: scatter(x) for k, x in v.items()}
            if torch.is_tensor(v) and v.dim() >= 1 and v.shape[0] == m:
                full = v.new_zeros((n, *v.shape[1:]))
                if len(idx):
                    full[idx.to(v.device)] = v
                return full
            return v.clone() if torch.is_tensor(v) else v  # the template is reused; callers edit outputs in place

        if not len(idx) and "out" in template:
            model.hand_batch_idx, model.body_batch_idx = list(range(n)), []  # as orig leaves them
            return scatter(template["out"])

        def sub(v):
            if not torch.is_tensor(v) or v.dim() < 1:
                return v
            if v.dim() >= 2 and v.shape[:2] == (B, P):  # per (frame, person)
                rows = v.reshape(n, 1, *v.shape[2:])
            elif v.shape[0] == B:  # per frame (cam_int): one copy per person
                rows = v.repeat_interleave(P, dim=0) if P > 1 else v
            else:
                return v
            return rows[run_idx.to(v.device)]

        state = model._batch_size, model._max_num_person, model._person_valid
        model._batch_size = m
        if state[1]:
            model._max_num_person = 1
        if state[2] is not None:
            model._person_valid = state[2][run_idx.to(state[2].device)]
        try:
            out = orig({k: sub(v) for k, v in batch.items()}, decoder_type, kps_batch)
        finally:
            model._batch_size, model._max_num_person, model._person_valid = state
        model.hand_batch_idx = list(range(n))
        if not len(idx):  # keep the 1-row output; scatter() turns it into zeros for any n
            template["out"] = torch.utils._pytree.tree_map(lambda v: v.clone() if torch.is_tensor(v) else v, out)
        return scatter(out)

    model.forward_step = forward_step


def build_sam3_tracker(ckpt_path):
    """SAM3 tracker with its own vision backbone, loaded from the full SAM3 video checkpoint.
    In the video model the tracker borrows detector.backbone (vision + text); the tracker only ever calls
    backbone.forward_image, so the vision part is enough. strict=True: every tracker weight must be present."""
    from models.sam3.sam3.model_builder import build_tracker
    with skip_random_init():
        tracker = build_tracker(apply_temporal_disambiguation=True, with_backbone=True)
    # Materialize only the tracker/vision tensors selected below. Keep the
    # ordinary copy load (dtype/layout unchanged) and support legacy .pt files.
    with open(ckpt_path, "rb") as checkpoint_file:
        use_mmap = checkpoint_file.read(4) == b"PK\x03\x04"
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True, mmap=use_mmap)
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
        det = HumanDetector(name="vitdet", device=torch.device("cpu"), path=cfg.sam_3d_body["detector_path"])
    # It only runs under the bf16 autocast below: keep one bf16 copy of its Linear / Conv weights instead of fp32 plus
    # autocast's cached bf16 copy (same values, boxes bit-identical). Cast on the CPU, so the fp32 weights never
    # reach the GPU.
    bf16_weights(det.detector)
    vitdet_attn.install()  # per-head softmax in the global blocks: same boxes, -1.8 GB peak
    det.device = torch.device("cuda")
    det.detector.to(det.device)

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
        "save_workers": 2,       # saving threads (one job per frame); see offline_app.on_4d_generation
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
    app.RUNTIME["save_workers"] = getattr(args, "save_workers", 2)
    if not getattr(args, "autocast_cache", False):
        # SAM3's process-wide bf16 autocast would cache a bf16 copy of every fp32 weight it casts (SAM3, MoGe):
        # recast per call instead. Same casts, same values; masks bit-identical, -0.9 GB in sam3, -0.6 GB in fov.
        torch.set_autocast_cache_enabled(False)

    # 1. person boxes on the first frame with a person (ViTDet only)
    with Stage("detect"):
        start_idx, boxes, width, height, image_list = detect_first_frame(cfg, args.input_video)
        boxes = [boxes[i] for i in oa.select_targets(boxes, cfg.runtime.get("max_targets", 2))]

    # 2. SAM3 tracking (tracker + vision backbone; frames on CPU)
    with Stage("sam3_load"):
        size = getattr(args, "tracker_size", 672)
        app.predictor = (build_sam3_tracker(cfg.sam3["ckpt_path"]) if size == 1008
                         else sam3_track_fast.build_tracker_at(cfg.sam3["ckpt_path"], size))
        if not getattr(args, "slow_tracker", False):
            sam3_track_fast.install(app.predictor)
        if not getattr(args, "sdpa_attn", False):
            from utils import triton_attn
            triton_attn.install()
    with Stage("sam3_init_state"):
        # frames are rebuilt on access from their JPEG bytes (bit-identical): preloading them as 1008^2 float32
        # takes 12.2 MB per frame, 21.9 GB for a 60 s clip
        with (contextlib.nullcontext() if getattr(args, "preload_frames", False) else lazy_jpeg_frames()):
            state = app.predictor.init_state(video_path=args.input_video if image_list is None else image_list,
                                             offload_video_to_cpu=True, offload_state_to_cpu=args.offload_state)
        if not getattr(args, "slow_tracker", False) and isinstance(state["images"], LazyFrames):
            state["images"] = sam3_track_fast.Prefetch(state["images"])
        app.predictor.clear_all_points_in_video(state)
        app.RUNTIME["inference_state"] = state
        app.RUNTIME["out_obj_ids"] = []
        for obj_id, (xmin, ymin, xmax, ymax) in enumerate(boxes):
            rel_box = np.array([[xmin / width, ymin / height, xmax / width, ymax / height]], dtype=np.float32)
            _, app.RUNTIME["out_obj_ids"], _, _ = app.predictor.add_new_points_or_box(
                inference_state=state, frame_idx=start_idx, obj_id=obj_id + 1, box=rel_box)
    with Stage("sam3_propagate"):
        if not getattr(args, "keep_tracker_memory", False):
            # outputs of past frames that no later frame can select are dropped as the propagation goes
            # (utils/tracker_prune.py, masks bit-identical): +3.9 GB of VRAM per 1800 frames otherwise
            install_pruned_propagation(app.predictor)
        # on_mask_generation's default stops after frame 1800 (60 s at 30 fps); track the whole video. Videos of up
        # to 1801 frames run exactly as before.
        app.on_mask_generation(start_frame_idx=0, max_frame_num_to_track=max(1800, state["num_frames"]))
        app.predictor.__dict__.pop("propagate_in_video", None)   # the wrapper references the tracker: drop it
        if size != 1008:   # images/ as a 1008 tracker writes them (sam3_track_fast.write_images_as_1008)
            frames = getattr(state["images"], "frames", state["images"])
            if isinstance(frames, LazyFrames):
                sam3_track_fast.write_images_as_1008(frames, app.OUTPUT_DIR, state["video_height"], state["video_width"])
            else:
                print(f"[tracker_size {size}] images/ written from the {size} frames (not a JPEG folder input)")
    obj_ids = list(app.RUNTIME["out_obj_ids"])
    app.RUNTIME["inference_state"] = None
    app.predictor = None
    del state
    free_gpu()

    # --moge_ahead: fusion v2's MoGe-2 depth needs only images/ and the focal length, so a second process can compute
    # it while HMR runs; avatar_app's fusion then takes the file instead of running MoGe itself
    ahead = None
    if getattr(args, "moge_ahead", False):
        import subprocess
        for f in glob.glob(os.path.join(app.OUTPUT_DIR, "moge_level*.npz")):   # a previous run's
            os.remove(f)
        ahead = subprocess.Popen([sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                     "occlusion", "moge_ahead.py"),
                                  "--pipeline_out", app.OUTPUT_DIR])

    # 3. HMR (SAM-3D-Body; MoGe-2 only for the single intrinsics call inside on_4d_generation, run ahead of the load)
    dev = torch.device("cuda")
    fov = StagedFOV(cfg.sam_3d_body["fov_path"], dev)
    with Stage("fov"):
        imgs = sorted(glob.glob(os.path.join(app.OUTPUT_DIR, "images", "*.jpg")))  # on_4d_generation's images_list[0]
        if imgs:
            with torch.autocast("cuda", enabled=False):
                fov.precompute(np.array(Image.open(imgs[0])).astype("uint8"))
    with Stage("hmr_load"):
        with skip_random_init():
            model, model_cfg = oa.load_sam_3d_body(cfg.sam_3d_body["ckpt_path"], device=torch.device("cpu"),
                                                   mhr_path=cfg.sam_3d_body["mhr_path"])
        # Share the MHR copy on the CPU, so the second copy never reaches the GPU (it set the hmr_load peak).
        share_mhr(model)
        enc = model.backbone.encoder
        if not getattr(args, "resident_backbone", False):
            # DINOv3 blocks stay in pinned CPU memory and stream through two GPU slots (utils/stream_blocks.py):
            # -1.5 GB resident, same weights, no measurable time cost.
            blocks, enc.blocks = enc.blocks, torch.nn.ModuleList()
            model.to(dev)
            enc.blocks = blocks
            stream_blocks(blocks, dev)
        else:
            model.to(dev)
        size = getattr(args, "hmr_input_size", 384)
        if size:
            # Crop size fed to the backbone (trained at 512). The DINOv3 RoPE, the ray-conditioning embedding and the
            # decoder's dense positional encoding all follow the feature-map size, and _get_hand_box / the crop
            # transforms read MODEL.IMAGE_SIZE, so this one setting changes body and hand crops consistently.
            assert size % model.backbone.patch_size == 0, (size, model.backbone.patch_size)
            assert model.cfg is model_cfg
            model_cfg.defrost()
            model_cfg.MODEL.IMAGE_SIZE = [size, size]
            model_cfg.freeze()
        app.sam3_3d_body_model = oa.SAM3DBodyEstimator(
            sam_3d_body_model=model, model_cfg=model_cfg, human_detector=None, human_segmentor=None,
            fov_estimator=fov)
        hmr_fast.install(model)  # sync-free MHR head, bitwise identical (scripts/eval/check_hmr_fast.py)
        if getattr(args, "compile_backbone", False):
            compile_backbone(model.backbone, app.RUNTIME["batch_size"])
        if not getattr(args, "keep_rejected_hands", False):
            skip_rejected_hands(model)
    app.RUNTIME["out_obj_ids"] = obj_ids
    with Stage("hmr_4d"):
        with torch.autocast("cuda", enabled=False):
            app.on_4d_generation()
    if ahead is not None:
        t0 = time.perf_counter()
        if glob.glob(os.path.join(app.OUTPUT_DIR, "focal_4d_individual", "*", "*.json")):
            ahead.wait()
        else:   # no person was meshed: nothing for MoGe to wait for
            ahead.kill()
        print(f"[STAGE][moge_ahead_wait] time={time.perf_counter() - t0:.2f} s, peak_alloc=0.00 GB "
              f"(exit code {ahead.returncode})", flush=True)
        CAPTURE["stages"]["moge_ahead_wait"] = dict(time_s=time.perf_counter() - t0, peak_alloc_GB=0.0,
                                                    peak_reserved_GB=0.0)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Low-VRAM offline 4D body generation (see module docstring)")
    p.add_argument("--input_video", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--capture", default=None)
    p.add_argument("--offload_state", action="store_true")
    p.add_argument("--avatar_out", default=None)
    p.add_argument("--avatar_hq", action="store_true",
                   help="avatar output over the original input frames, PNG frames, mp4 CRF 12 (avatar_app.py --hq)")
    p.add_argument("--vrm", default=None)
    p.add_argument("--compile_backbone", action="store_true")
    p.add_argument("--keep_rejected_hands", action="store_true")
    p.add_argument("--hmr_input_size", type=int, default=384)
    p.add_argument("--save_workers", type=int, default=2)
    p.add_argument("--autocast_cache", action="store_true")
    p.add_argument("--resident_backbone", action="store_true")
    p.add_argument("--preload_frames", action="store_true")
    p.add_argument("--keep_tracker_memory", action="store_true")
    p.add_argument("--slow_tracker", action="store_true")
    p.add_argument("--tracker_size", type=int, default=672)
    p.add_argument("--sdpa_attn", action="store_true")
    p.add_argument("--moge_ahead", action="store_true")
    args = p.parse_args()
    if not os.path.exists(args.input_video):
        raise FileNotFoundError(args.input_video)
    use_persistent_compile_cache()   # also inherited by the avatar subprocess
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
        # The parent stays alive during subprocess.call. SAM3's process-wide autocast
        # retains weight copies even after inference_lite's models go out of scope.
        # Release those references before emptying the allocator cache so the avatar
        # process does not compete with the completed lite stage for VRAM.
        torch.cuda.synchronize()
        gc.collect()
        torch.clear_autocast_cache()
        torch.cuda.empty_cache()
        # the pinned host blocks of the streamed DINOv3 weights (2.3 GB) stay cached by torch's host allocator after
        # the model is gone; give them back before the avatar process needs the RAM
        pinned = torch.cuda.host_memory_stats().get("reserved_bytes.current", 0)
        torch._C._host_emptyCache()
        print(f"[HANDOFF] parent CUDA alloc={torch.cuda.memory_allocated() / 1024 ** 3:.3f} GiB, "
              f"reserved={torch.cuda.memory_reserved() / 1024 ** 3:.3f} GiB, pinned host released "
              f"{pinned / 1024 ** 3:.2f} -> {torch.cuda.host_memory_stats().get('reserved_bytes.current', 0) / 1024 ** 3:.2f} GiB",
              flush=True)
        cmd = [sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), "avatar_app.py"),
               "--pipeline_out", args.output_dir, "--out", args.avatar_out] + (["--vrm", args.vrm] if args.vrm else [])             + (["--hq", args.input_video] if args.avatar_hq else [])
        sys.exit(subprocess.call(cmd))
