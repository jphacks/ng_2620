"""Let SAM3's tracker read JPEG frames on demand instead of preloading them all as float32.

sam2_utils.load_video_frames_from_jpg_images / _jpg_image_list resize every frame to 1008x1008 float32 up front:
12.2 MB per frame, so a 60 s / 1800-frame clip asks for 21.9 GB of RAM and fails. The tracker only ever reads
`images[frame_idx]` (sam3_tracking_predictor.py) and `len(images)`, so `LazyFrames` keeps the JPEG bytes (~47 KB
per 270x480 frame) and rebuilds a frame on access with the loader's own ops in the same dtypes:
PIL resize -> uint8 / 255.0 (float64) -> float32 -> -= mean -> /= std. The tensors are bit-identical
(scripts/eval/check_lazy_frames.py). Per-frame decode work moves from init_state to the first access.
"""
import contextlib
import io

import numpy as np
import torch
from PIL import Image


class LazyFrames:
    def __init__(self, img_paths, image_size, img_mean, img_std):
        self.data = []
        for p in img_paths:
            with open(p, "rb") as f:
                self.data.append(f.read())
        self.image_size = image_size
        self.img_mean, self.img_std = img_mean, img_std
        self.video_width, self.video_height = Image.open(io.BytesIO(self.data[0])).size

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        img_pil = Image.open(io.BytesIO(self.data[index]))
        img_np = np.array(img_pil.convert("RGB").resize((self.image_size, self.image_size)))
        if img_np.dtype != np.uint8:
            raise RuntimeError(f"Unknown image dtype: {img_np.dtype} on frame {index}")
        img = torch.from_numpy(img_np / 255.0).permute(2, 0, 1).to(torch.float32)
        img -= self.img_mean
        img /= self.img_std
        return img


class DiskFrames:
    """LazyFrames that keeps only the paths: frame i is read from its JPEG when asked for (one decode per access;
    callers that walk a clip once, such as fusion v2's keyframe trackers, read each frame once). Same ops and dtypes
    as LazyFrames / the preloading loaders, with the tracker's own image_size, mean and std (SAM3: 1008, 0.5, 0.5;
    EdgeTAM: 1024, ImageNet), so the tensors are bit-identical (scripts/eval/check_edgetam_tracker.py)."""

    def __init__(self, img_paths, image_size, img_mean, img_std):
        self.paths, self.image_size = list(img_paths), image_size
        self.img_mean = torch.tensor(img_mean, dtype=torch.float32)[:, None, None]
        self.img_std = torch.tensor(img_std, dtype=torch.float32)[:, None, None]
        with Image.open(self.paths[0]) as im:
            self.video_width, self.video_height = im.size

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, index):
        img_pil = Image.open(self.paths[index])
        img_np = np.array(img_pil.convert("RGB").resize((self.image_size, self.image_size)))
        if img_np.dtype != np.uint8:
            raise RuntimeError(f"Unknown image dtype: {img_np.dtype} on {self.paths[index]}")
        img = torch.from_numpy(img_np / 255.0).permute(2, 0, 1).to(torch.float32)
        img -= self.img_mean
        img /= self.img_std
        return img


def _lazy_loader(paths_of):
    def load(video_path, image_size, offload_video_to_cpu, img_mean=(0.5, 0.5, 0.5), img_std=(0.5, 0.5, 0.5),
             async_loading_frames=False, compute_device=torch.device("cuda")):
        if not offload_video_to_cpu or async_loading_frames:
            raise NotImplementedError("lazy_jpeg_frames supports offload_video_to_cpu=True without async loading")
        frames = LazyFrames(paths_of(video_path), image_size,
                            torch.tensor(img_mean, dtype=torch.float32)[:, None, None],
                            torch.tensor(img_std, dtype=torch.float32)[:, None, None])
        return frames, frames.video_height, frames.video_width
    return load


def _dir_paths(folder):
    import os
    names = [p for p in os.listdir(folder) if os.path.splitext(p)[-1] in [".jpg", ".jpeg", ".JPG", ".JPEG"]]
    names.sort(key=lambda p: int(os.path.splitext(p)[0]))
    if not names:
        raise RuntimeError(f"no images found in {folder}")
    return [os.path.join(folder, n) for n in names]


def _list_paths(paths):
    import os
    if not paths:
        raise RuntimeError("no images found")
    folder = os.path.dirname(paths[0])
    return [os.path.join(folder, os.path.basename(f)) for f in paths]


@contextlib.contextmanager
def lazy_jpeg_frames():
    """Within this context, SAM3 init_state on a JPEG folder or JPEG list returns LazyFrames."""
    from sam3.model.utils import sam2_utils
    saved = sam2_utils.load_video_frames_from_jpg_images, sam2_utils.load_video_frames_from_jpg_image_list
    sam2_utils.load_video_frames_from_jpg_images = _lazy_loader(_dir_paths)
    sam2_utils.load_video_frames_from_jpg_image_list = _lazy_loader(_list_paths)
    try:
        yield
    finally:
        sam2_utils.load_video_frames_from_jpg_images, sam2_utils.load_video_frames_from_jpg_image_list = saved
