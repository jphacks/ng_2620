"""Preview layers for the desktop editor (designdocs/architecture/contracts §4.2, pipeline-integration §3.5).

Per frame f, at the pipeline's resolution:
  clean/{f:05d}.webp            the clean plate (avatar_app's composite base)
  avatar/{pid}/{f:05d}.webp     the avatar's RGBA, straight alpha: the supersampled render area-averaged with
                                premultiplied colour (the same reduction composite_gpu applies)
  hide/{pid}/{f:05d}.png        AvatarHider's smoothed hide mask, 0 / 255
and layers.json: {persons, order: {f: [pid, ...] far -> near by the median avatar depth}, avatarIds}.
The editor recomposites these with the contract's rule; writes go through a Sink (one worker thread, in order).
"""
import json
import os

import cv2
import numpy as np

WEBP_Q = 90


def _native_rgba(im, s):
    """(h*s, w*s, 4) uint8 (numpy or torch) -> (h, w, 4) uint8 straight alpha."""
    try:
        import torch
        if torch.is_tensor(im):
            import torch.nn.functional as F
            t = im.float()
            a = t[..., 3:4] / 255.0
            pre = torch.cat([t[..., :3] * a, a], -1).permute(2, 0, 1)[None]
            pre = F.avg_pool2d(pre, s)[0].permute(1, 2, 0)
            al = pre[..., 3:4]
            rgb = torch.where(al > 1e-6, pre[..., :3] / al.clamp_min(1e-6), torch.zeros_like(pre[..., :3]))
            return torch.cat([rgb, al * 255.0], -1).round().clamp(0, 255).to(torch.uint8).cpu().numpy()
    except ImportError:
        pass
    t = im.astype(np.float32)
    h, w = t.shape[0] // s, t.shape[1] // s
    a = t[..., 3:4] / 255.0
    pre = np.concatenate([t[..., :3] * a, a], -1).reshape(h, s, w, s, 4).mean(axis=(1, 3))
    al = pre[..., 3:4]
    rgb = np.where(al > 1e-6, pre[..., :3] / np.maximum(al, 1e-6), 0)
    return np.clip(np.round(np.concatenate([rgb, al * 255.0], -1)), 0, 255).astype(np.uint8)


def _median_depth(d):
    try:
        import torch
        if torch.is_tensor(d):
            d = d.float().cpu().numpy()
    except ImportError:
        pass
    v = d[np.isfinite(d)]
    return float(np.median(v)) if v.size else float("inf")


class LayerWriter:
    def __init__(self, out_dir, size, pids, avatar_ids=None, sink=None):
        self.dir, (self.W, self.H), self.pids = out_dir, size, list(pids)
        self.avatar_ids = avatar_ids or {}
        self.order = {}
        self.sink = sink
        for sub in ["clean"] + [f"avatar/{p}" for p in self.pids] + [f"hide/{p}" for p in self.pids]:
            os.makedirs(os.path.join(out_dir, sub), exist_ok=True)

    def _put(self, fn, *args):
        if self.sink is not None:
            self.sink.put(fn, *args)
        else:
            fn(*args)

    def frame(self, f, plate, bufs, hides, ssaa):
        """bufs: {pid: (rgba ssaa box, depth ssaa box, native box (x0, y0, x1, y1), _)}; hides: {pid: (H, W) 0..1}."""
        name = f"{f:05d}"
        self._put(cv2.imwrite, os.path.join(self.dir, "clean", f"{name}.webp"), plate,
                  [cv2.IMWRITE_WEBP_QUALITY, WEBP_Q])
        depth = {}
        for pid in self.pids:
            rgba = np.zeros((self.H, self.W, 4), np.uint8)
            hide = np.zeros((self.H, self.W), np.uint8)
            if pid in bufs:
                im, d, (x0, y0, x1, y1), _ = bufs[pid]
                rgba[y0:y1, x0:x1] = _native_rgba(im, ssaa)
                depth[pid] = _median_depth(d)
                h = hides.get(pid)
                if h is not None:
                    hide[h >= 0.5] = 255
            self._put(cv2.imwrite, os.path.join(self.dir, "avatar", str(pid), f"{name}.webp"), rgba,
                      [cv2.IMWRITE_WEBP_QUALITY, WEBP_Q])
            self._put(cv2.imwrite, os.path.join(self.dir, "hide", str(pid), f"{name}.png"), hide)
        self.order[str(f)] = sorted(depth, key=lambda p: -depth[p])          # far -> near

    def close(self):
        with open(os.path.join(self.dir, "layers.json"), "w", encoding="utf-8") as fh:
            json.dump({"persons": self.pids, "order": self.order,
                       "avatarIds": {str(p): self.avatar_ids.get(p) for p in self.pids if p in self.avatar_ids}},
                      fh)
