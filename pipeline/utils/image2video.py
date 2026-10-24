import os, glob
import cv2
import numpy as np
import imageio.v2 as imageio 

from typing import List 


class Mp4Writer:
    """images_to_mp4 one frame at a time: append() as frames are produced, close() at the end. Each frame goes
    through the same conversions as in images_to_mp4 (which uses this class), so the encoder gets the same input
    without the caller holding every frame (a 60 s compare video at 1440x480: 3.7 GB)."""

    def __init__(self, output_path: str, fps: int = 25, crf: int = None):
        self.output_path, self.fps, self.crf = output_path, fps, crf
        self.writer, self.size = None, None

    def append(self, img: np.ndarray):
        if self.writer is None:
            h, w = img.shape[:2]
            self.size = (w, h)
            os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
            self.writer = imageio.get_writer(
                self.output_path,
                fps=int(self.fps),
                format="FFMPEG",
                codec="libx264",
                pixelformat="yuv420p",
                **({} if self.crf is None else {"quality": None, "macro_block_size": 2,   # yuv420p needs even sizes only: no stretch to x16
                                               "output_params": ["-crf", str(self.crf), "-preset", "slow"]})
            )
        if img.ndim == 2:  # Gray → 3 channels
            img = np.stack([img] * 3, axis=-1)
        img = cv2.resize(img, self.size)
        if img.shape[2] == 4:
            img = img[..., :3]  # Remove alpha
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        self.writer.append_data(img)

    def close(self):
        if self.writer is None:
            raise ValueError("Image frame list is empty.")
        self.writer.close()
        print(f"[OK] Saved video: {self.output_path}")


def images_to_mp4(images: List[np.ndarray], output_path: str, fps: int = 25):
    """
    Convert a list of images into an HTML5-compatible MP4 video.
    - Ensures correct size
    - Forces RGB uint8
    - Uses H.264 + yuv420p (browser-safe)
    """
    if len(images) == 0:
        raise ValueError("Image frame list is empty.")
    w = Mp4Writer(output_path, fps)
    for img in images:
        w.append(img)
    w.close()


def jpg_folder_to_mp4(folder: str, output_filename: str, fps: int = 25):
    """
    Convert JPG images in a folder into an MP4 video (HTML5 compatible).
    Uses H.264 + yuv420p to ensure Gradio/browser playback (not download-only).
    Sorted by filename.
    """
    # Gather all JPG images (case-insensitive patterns)
    patterns = ["*.jpg", "*.JPG", "*.jpeg", "*.JPEG", "*.png", "*.PNG"]
    img_paths = []
    for p in patterns:
        img_paths.extend(glob.glob(os.path.join(folder, p)))

    if not img_paths:
        raise ValueError(f"No image files found in folder: {folder}")

    # Sort by filename
    img_paths = sorted(img_paths)

    # Read the first image to determine resolution
    first_img = cv2.imread(img_paths[0])
    if first_img is None:
        raise ValueError(f"Failed to read image: {img_paths[0]}")
    h, w = first_img.shape[:2]

    # Ensure output folder exists
    os.makedirs(os.path.dirname(output_filename), exist_ok=True)

    # Initialize writer with browser-safe configuration
    writer = imageio.get_writer(
        output_filename,
        fps=int(fps),
        format="FFMPEG",
        codec="libx264",       # Required for HTML5 playback
        pixelformat="yuv420p"  # Required for <video> compatibility
    )

    for path in img_paths:
        img = cv2.imread(path)
        if img is None:
            print(f"Warning: skipped unreadable image {path}")
            continue

        # Force resize if needed
        if img.shape[:2] != (h, w):
            img = cv2.resize(img, (w, h))

        # BGR → RGB
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        # Ensure correct dtype
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)

        writer.append_data(img)

    writer.close()
    print(f"[OK] Saved video to: {output_filename}")
