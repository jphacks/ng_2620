"""LaMa (big-lama, Apache-2.0; github.com/advimman/lama, TorchScript export of simple-lama-inpainting) for the person
removal's pixels that no other frame shows: the learned replacement of cv2.inpaint (Telea), which smeared the large
holes of a static camera into grey blobs (reports/compare/2026-10-24/removal_current).

Only the mask's bounding box grown by a context margin (`context` x its size, at least 32 px) is inpainted, scaled down
so that its short side is at most `short` px (LaMa was trained on ~256-512 px images: larger inputs give it less
context per pixel and cost more), then scaled back; only the masked pixels are replaced.
Checkpoint: <ckpt_root>/big-lama/big-lama.pt (configs/body4d.yaml).
"""
import os
import threading

import cv2
import numpy as np

_LOCK = threading.Lock()
_MODEL = {}


def _ckpt():
    root = os.environ.get("BODY4D_CKPT_ROOT")
    if not root:
        from omegaconf import OmegaConf
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        root = OmegaConf.load(os.path.join(repo, "configs", "body4d.yaml")).paths.ckpt_root
    return os.path.join(root, "big-lama", "big-lama.pt")


def _model(device):
    import torch
    if device not in _MODEL:
        _MODEL[device] = torch.jit.load(_ckpt(), map_location=device).eval()
    return _MODEL[device]


def inpaint(img, mask, short=512, grow=3, context=0.5, device="cuda"):
    """img: BGR uint8 (H, W, 3); mask: (H, W) nonzero where to fill. -> BGR uint8 with the masked pixels replaced.
    Thread-safe (one model call at a time)."""
    import torch
    m_full = (np.asarray(mask) > 0).astype(np.uint8)
    if not m_full.any():
        return img
    H, W = img.shape[:2]
    rows, cols = np.nonzero(m_full.any(1))[0], np.nonzero(m_full.any(0))[0]
    pad = max(32, int(context * max(rows[-1] - rows[0], cols[-1] - cols[0])))
    y0, y1 = max(0, rows[0] - pad), min(H, rows[-1] + 1 + pad)
    x0, x1 = max(0, cols[0] - pad), min(W, cols[-1] + 1 + pad)
    crop, m = img[y0:y1, x0:x1], m_full[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    s = min(1.0, short / min(ch, cw))
    h, w = max(8, int(round(ch * s / 8)) * 8), max(8, int(round(cw * s / 8)) * 8)
    small = cv2.resize(crop, (w, h), interpolation=cv2.INTER_AREA)
    ms = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
    if grow:
        ms = cv2.dilate(ms, np.ones((2 * grow + 1,) * 2, np.uint8))
    x = torch.from_numpy(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)).to(device).permute(2, 0, 1)[None].float() / 255
    mk = torch.from_numpy(ms).to(device)[None, None].float()
    # optimized_execution(False): TorchScript's profiling executor re-specializes the graph for every new input shape
    # (1.2-1.7 s each), and every frame's crop has its own shape; unoptimized, any shape takes ~70-100 ms
    with _LOCK, torch.no_grad(), torch.autocast(device_type="cuda", enabled=False),             torch.jit.optimized_execution(False):
        y = _model(device)(x, mk)
    out = (y[0].permute(1, 2, 0).clamp(0, 1) * 255 + 0.5).to(torch.uint8).cpu().numpy()
    up = cv2.resize(cv2.cvtColor(out, cv2.COLOR_RGB2BGR), (cw, ch), interpolation=cv2.INTER_CUBIC)
    res = img.copy()
    view = res[y0:y1, x0:x1]
    view[m > 0] = up[m > 0]
    return res
