"""Keyframe candidates for fusion v2: the SAM3 point grid runs on keyframes only; in between, the keyframe's
occluder candidates are carried forward instead of re-prompted (gpu_stage_fast points="kf_flow" / "kf_sam3").

Profiling (scripts/eval/profile_stages.py, L4, 3DPW occlusion clips) put the point-prompt decoder and its mask
bookkeeping at 450-800 ms/frame, re-run on every frame, while the per-frame candidates link into tracks only ~3-6
frames long (evidence.link). Carrying candidates keeps their identity across frames and skips the grid.

Keyframe i (the grid runs, all of its de-duplicated candidates are written as before):
  * the first frame, and every KF_MAX frames
  * a new occlusion: the mesh-but-not-visible pixels (gpu_stage's `inner`) that no carried mask covers grew by more
    than KF_GROW px over what the keyframe's own candidates left uncovered (mesh misfits that no segment explains
    do not retrigger every frame)
Carried set: the keyframe candidates that can become a decision unit (evidence.compute / fusion.decide: >= 15 px
on a mesh silhouette, some of it outside the visible mask, at most OWN_MAX of the candidate inside the visible mask,
visible = SAM3's tracked mask refined by the frame's 'person' detections as evidence.refine_visible does; a superset of
fusion's own test, OWNMAX 0.3). Between keyframes, frame i's candidates are the carried masks, with the keyframe
candidate's score and from-visible flag.

Carriers:
  flow   DIS optical flow (OpenCV, CPU) from frame i back to i-1; every carried mask is warped by it. Cost does not
         depend on the number of masks.
  sam3   the SAM3 tracker on the image features set_image already computed (the image model's inst-interactive
         predictor and the tracker share weights; Sam3VideoBase feeds its tracker the same way). ~40 ms per mask per
         frame on an L4 (scripts/eval/tracker_cost.py).
"""
import cv2
import numpy as np

KF_MAX = 10        # frames between forced keyframes
KF_GROW = 300      # px of newly uncovered occluded mesh that make a keyframe (480x854 frames)
OWN_MAX = 0.5
MIN_PX = 30        # a carried mask smaller than this is dropped (gpu_stage.MIN_PX)


def relevant(cands, lab, pids, dets, sil):
    """indices of the candidates [(score, mask, inV)] that can become a decision unit (see module docstring)."""
    from evidence import refine_visible
    Vr = refine_visible(lab, pids, dets)
    Vu = np.zeros(lab.shape, bool)
    for v in Vr.values():
        Vu |= v
    out = []
    for j, (_, C, _) in enumerate(cands):
        ovl = C & sil
        if ovl.sum() < 15 or not (ovl & ~Vu).any():
            continue
        if (C & Vu).sum() / max(int(C.sum()), 1) <= OWN_MAX:
            out.append(j)
    return out


class _Flow:
    def __init__(self):
        self.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
        self.gray = None
        self.masks = []

    def start(self, i, img, masks, feats=None):
        self.gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        self.masks = [m.astype(np.float32) for m in masks]

    def step(self, i, img, feats=None):
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        f = self.dis.calc(g, self.gray, None)          # backward flow: where each pixel of frame i was at i-1
        H, W = g.shape
        gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
        mx, my = gx + f[..., 0], gy + f[..., 1]
        self.masks = [cv2.remap(m, mx, my, cv2.INTER_LINEAR, borderValue=0) for m in self.masks]
        self.gray = g
        return [m > 0.5 for m in self.masks]


class _SAM3:
    def __init__(self):
        self.tracker = build_tracker()
        self.gen = self.state = None

    def start(self, i, img, masks, feats):
        import torch
        self.gen = self.state = None
        if not masks:
            return
        self.cache = {i: feats}
        H, W = masks[0].shape
        with torch.inference_mode():
            self.state = self.tracker.init_state(video_height=H, video_width=W, num_frames=10 ** 6,
                                                 cached_features=self.cache)
            for k, m in enumerate(masks):
                self.tracker.add_new_mask(self.state, frame_idx=i, obj_id=k + 1, mask=torch.from_numpy(m))
            self.gen = self.tracker.propagate_in_video(self.state, start_frame_idx=i, max_frame_num_to_track=10 ** 6,
                                                       reverse=False, tqdm_disable=True, propagate_preflight=True)
            next(self.gen)   # the keyframe itself

    def step(self, i, img, feats):
        import torch
        if self.gen is None:
            return []
        self.cache[i] = feats
        self.cache.pop(i - 1, None)
        with torch.inference_mode():
            f, _, _, video_res_masks, _, _ = next(self.gen)
        assert f == i, (f, i)
        return list((video_res_masks[:, 0] > 0).cpu().numpy())


def build_tracker():
    """SAM3 tracker without its own vision backbone (fed set_image's features), checkpoint tracker.* weights."""
    import torch
    from sam3.model_builder import build_tracker as bt
    from r3common import ckpt
    from utils.skip_init import skip_random_init   # repo root: on sys.path via gpu_stage_fast
    with skip_random_init():
        tr = bt(apply_temporal_disambiguation=True, with_backbone=False)
    sd = torch.load(ckpt("sam3/sam3.pt"), map_location="cpu", weights_only=True, mmap=True)
    sd = sd.get("model", sd)
    sd = {k[len("tracker."):]: v for k, v in sd.items() if k.startswith("tracker.")}
    missing, unexpected = tr.load_state_dict(sd, strict=False)
    assert not unexpected and all(k.startswith("backbone") for k in missing), (missing[:5], unexpected[:5])
    return tr.to("cuda").eval()


def tracker_feats(st):
    """set_image state -> the tracker's cached-feature entry (as Sam3VideoBase builds it)."""
    import torch
    b = st["backbone_out"]["sam2_backbone_out"]
    fpn = list(b["backbone_fpn"])
    return (torch.zeros(1, 3, 1008, 1008, device="cuda"),
            {"tracker_backbone_out": {"vision_features": fpn[-1], "vision_pos_enc": list(b["vision_pos_enc"]),
                                      "backbone_fpn": fpn}})


class Keyframer:
    """Per-frame driver. carry(i, img, inner, st) -> None (frame i is a keyframe: run the grid) or the carried
    candidates [(score, mask, inV)]; after a keyframe's grid, start(i, img, cands, lab, pids, dets, sil, inner, st)."""

    def __init__(self, carrier):
        self.c = {"flow": _Flow, "sam3": _SAM3}[carrier]()
        self.carrier = carrier
        self.last = None
        self.meta = []
        self.base_unc = 0
        self.keyframes = []

    def carry(self, i, img, inner, st):
        if self.last is None or i - self.last >= KF_MAX:
            return None
        feats = tracker_feats(st) if self.carrier == "sam3" else None
        masks = self.c.step(i, img, feats)
        cov = np.zeros(inner.shape, bool)
        out = []
        for (sc, inv), m in zip(self.meta, masks):
            if m.sum() >= MIN_PX:
                out.append((sc, m, inv))
                cov |= m
        if int((inner & ~cov).sum()) > self.base_unc + KF_GROW:
            return None
        return out

    def start(self, i, img, cands, lab, pids, dets, sil, inner, st):
        sel = relevant(cands, lab, pids, dets, sil)
        masks = [cands[j][1] for j in sel]
        self.meta = [(cands[j][0], cands[j][2]) for j in sel]
        cov = np.zeros(inner.shape, bool)
        for m in masks:
            cov |= m
        self.base_unc = int((inner & ~cov).sum())
        self.c.start(i, img, masks, tracker_feats(st) if self.carrier == "sam3" else None)
        self.last = i
        self.keyframes.append(i)
