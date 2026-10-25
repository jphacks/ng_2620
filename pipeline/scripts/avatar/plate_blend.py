"""Low-frequency colour matching of the clean plate to the frame around each removal hole.

The plate's fill comes from other frames (removal_gpu: window backgrounds warped by homographies) or LaMa. Exposure and
white balance drift over a handheld clip and the warp softens the texture, so a filled region often differs in
brightness from the frame right around it, and its outline shows (IMG_3386: the median colour step across the hole's
edge where no avatar covers it was 7-12 / 255, 25-49 behind other people). match() measures, along the hole's edge,
the difference between the frame just outside and the plate just inside (local means over BAND px), spreads it into
the hole by normalised convolution (a membrane-like smooth field, sigma SIGMA px) and adds it to the plate inside the
hole, fading with the distance from the edge (FADE px): the fill keeps its texture and takes the surrounding level.
Edge pixels next to what the removal keeps (`keep`: the restored cover) or the image border are not used.
"""

import cv2
import numpy as np

BAND = 4        # px each side of the hole's edge whose local means are compared
SIGMA = 24.0    # px, spread of the edge differences into the hole
FADE = 48.0     # px, the correction falls off as exp(-d / FADE) with the depth d inside the hole
DOWN = 4        # the field is computed at 1 / DOWN of the resolution
MAX_STEP = 60   # clip of the per-channel correction (a person's dark edge outside a too tight hole must not dye it)


def match(plate, frame, hole, keep=None):
    """-> the plate with its hole region colour-matched (a new array; plate unchanged); hole: bool (H, W)."""
    if not hole.any():
        return plate
    H, W = hole.shape
    ys, xs = np.nonzero(hole)
    m = int(3 * SIGMA + BAND + 2)
    y0, y1 = max(ys.min() - m, 0), min(ys.max() + m + 1, H)
    x0, x1 = max(xs.min() - m, 0), min(xs.max() + m + 1, W)
    h = hole[y0:y1, x0:x1].astype(np.uint8)
    pl = plate[y0:y1, x0:x1].astype(np.float32)
    fr = frame[y0:y1, x0:x1].astype(np.float32)
    din = cv2.distanceTransform(h, cv2.DIST_L2, 3)
    dout = cv2.distanceTransform(1 - h, cv2.DIST_L2, 3)
    inner = ((din > 0) & (din <= BAND)).astype(np.float32)
    outer = ((dout > 0) & (dout <= BAND)).astype(np.float32)
    if keep is not None:
        k = cv2.dilate(keep[y0:y1, x0:x1].astype(np.uint8), np.ones((2 * BAND + 1,) * 2, np.uint8)) > 0
        outer[k] = 0
        inner[k] = 0
    kb = (2 * BAND + 1, 2 * BAND + 1)
    wi, wo = cv2.blur(inner, kb), cv2.blur(outer, kb)
    mi = cv2.blur(pl * inner[..., None], kb) / np.maximum(wi, 1e-6)[..., None]
    mo = cv2.blur(fr * outer[..., None], kb) / np.maximum(wo, 1e-6)[..., None]
    edge = (din > 0) & (din <= 1.5) & (wi > 0.05) & (wo > 0.05)
    if edge.sum() < 10:
        return plate
    diff = np.clip(mo - mi, -MAX_STEP, MAX_STEP) * edge[..., None]
    # the field is smooth: spread it at 1 / DOWN of the resolution (area sums keep the weights) and upsample
    hh, ww = h.shape
    sz = (max(ww // DOWN, 1), max(hh // DOWN, 1))
    e_s = cv2.resize(edge.astype(np.float32), sz, interpolation=cv2.INTER_AREA)
    d_s = cv2.resize(diff, sz, interpolation=cv2.INTER_AREA)
    wsum = cv2.GaussianBlur(e_s, (0, 0), SIGMA / DOWN)
    f_s = cv2.GaussianBlur(d_s, (0, 0), SIGMA / DOWN) / np.maximum(wsum, 1e-6)[..., None]
    f_s[wsum < 1e-4] = 0
    field = cv2.resize(f_s, (ww, hh), interpolation=cv2.INTER_LINEAR)
    w = (np.exp(-din / FADE) * (h > 0))[..., None]
    out = plate.copy()
    out[y0:y1, x0:x1] = np.clip(pl + field * w, 0, 255).astype(plate.dtype)
    return out
