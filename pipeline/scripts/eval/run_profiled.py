"""Run scripts/offline_app.py unchanged, with two non-invasive additions:

1. Stage timing / GPU memory via the repo's own profiler (utils.gpu_profile).
2. Capture of the per-frame HMR outputs that offline_app computes but does not
   save (2D keypoints, vertices, cam_t, focal, bbox), for the overlay and the
   comparison against Penn Action GT.

Usage (from the repo root):
  .venv/Scripts/python.exe -u scripts/eval/run_profiled.py \
      --input_video C:/VScodeFiles/datasets/PennAction/frames/0826 \
      --output_dir <run_dir>/pipeline_out \
      --capture <run_dir>/captures/hmr_outputs.pkl
"""
import argparse
import os
import pickle
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import offline_app as oa  # noqa: E402  (prints device, sets sys.path)
import torch  # noqa: E402
from utils import gpu_profile  # noqa: E402

CAPTURE = {"detection": [], "hmr_batches": [], "faces": None, "timing": {}}


def _to_np(outputs):
    res = []
    for person in outputs:
        res.append({k: (v.detach().cpu().numpy() if torch.is_tensor(v) else v) for k, v in person.items()})
    return res


# --- model loading + instance-level stage wrappers -------------------------
_orig_init = oa.OfflineApp.__init__


def __init__(self, *a, **k):
    _orig_init(self, *a, **k)
    self.predictor.init_state = gpu_profile(self.predictor.init_state)
    self.predictor.add_new_points_or_box = gpu_profile(self.predictor.add_new_points_or_box)


oa.OfflineApp.__init__ = gpu_profile(__init__)

# --- targeting: vitdet detection + single-image HMR on the first frame -----
_orig_one = oa.SAM3DBodyEstimator.process_one_image


def process_one_image(self, img, *a, **k):
    out = _orig_one(self, img, *a, **k)
    CAPTURE["detection"].append({"image": img, "outputs": _to_np(out)})
    return out


oa.SAM3DBodyEstimator.process_one_image = gpu_profile(process_one_image)

# --- tracking (SAM3 propagation) and 4D (HMR + render + save) --------------
oa.OfflineApp.on_mask_generation = gpu_profile(oa.OfflineApp.on_mask_generation)
oa.OfflineApp.on_4d_generation = gpu_profile(oa.OfflineApp.on_4d_generation)

# --- HMR only (nested inside on_4d_generation, so plain timer: gpu_profile
#     would reset the outer peak-memory counter) ----------------------------
_orig_pim = oa.process_image_with_mask


def process_image_with_mask(estimator, image_path, mask_path, *a, **k):
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    res = _orig_pim(estimator, image_path, mask_path, *a, **k)
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    outputs, id_batch, empty = res
    print(f"[TIME][process_image_with_mask] frames={len(image_path)} time={dt:.2f} s")
    CAPTURE["faces"] = estimator.faces
    CAPTURE["hmr_batches"].append({
        "image_path": list(image_path),
        "mask_path": list(mask_path),
        "outputs": [_to_np(o) for o in outputs],
        "id_batch": id_batch,
        "empty_frame_list": list(empty),
        "time_s": dt,
    })
    return res


oa.process_image_with_mask = process_image_with_mask


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--input_video", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--capture", required=True)
    args = p.parse_args()

    t0 = time.perf_counter()
    oa.inference(argparse.Namespace(input_video=args.input_video, output_dir=args.output_dir))
    total = time.perf_counter() - t0
    print(f"[TIME][total inference()] {total:.2f} s")
    CAPTURE["timing"]["total_s"] = total
    CAPTURE["timing"]["max_memory_reserved_GB"] = torch.cuda.max_memory_reserved() / 1024 ** 3

    os.makedirs(os.path.dirname(args.capture), exist_ok=True)
    with open(args.capture, "wb") as f:
        pickle.dump(CAPTURE, f)
    print(f"[CAPTURE] saved {args.capture}")
