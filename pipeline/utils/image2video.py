import os, glob
import cv2
import numpy as np
import imageio.v2 as imageio 

from typing import List 


def images_to_mp4(images: List[np.ndarray], output_path: str, fps: int = 25):
    """
    Convert a list of images into an HTML5-compatible MP4 video.
    - Ensures correct size
    - Forces RGB uint8
    - Uses H.264 + yuv420p (browser-safe)
    """
    if len(images) == 0:
        raise ValueError("Image frame list is empty.")

    first = images[0]
    if first.ndim == 2:
        h, w = first.shape
    else:
        h, w, _ = first.shape

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    writer = imageio.get_writer(
        output_path,
        fps=int(fps),
        format="FFMPEG",
        codec="libx264",
        pixelformat="yuv420p"
    )

    for img in images:
        if img.ndim == 2:  # Gray → 3 channels
            img = np.stack([img] * 3, axis=-1)
        img = cv2.resize(img, (w, h))
        if img.shape[2] == 4:
            img = img[..., :3]  # Remove alpha
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        writer.append_data(img)

    writer.close()
    print(f"[OK] Saved video: {output_path}")


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
