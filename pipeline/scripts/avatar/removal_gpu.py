"""removal.clean_plates with the per-frame fill on the GPU: same plates, holes and stats, byte for byte
(scripts/eval/check_removal_gpu.py).

At 1080x1920 (avatar_app --hq) the CPU fill takes ~0.7 s per frame on 24 cores: a 5-minute clip's removal (7718 s)
outlasted fusion v2 (3085 s) by 4632 s with the GPU idle (Modal L40S, 2026-10-20). Moved to the GPU, each step
reproducing OpenCV's integer arithmetic:
  * the hole / exclusion dilations: a 0/1 correlation with the structuring element at cv2.dilate's anchor (k // 2),
    by FFT; the counts are whole numbers, so "> 0.5" is the dilated mask (a batch with a count off by more than 0.25
    from a whole number goes to cv2.dilate)
  * cv2.warpPerspective of each reference frame (INTER_LINEAR, border 0) and of its exclusion mask (INTER_NEAREST,
    border 1), evaluated only at the hole pixels: the inverse map from cv2.invert, the source coordinates as
    OpenCV's WarpPerspectiveInvoker computes them in double (per 64-pixel x block: M0*x0 + M1*y + M2, then
    + M0*x1, times 32 / w (1 / w for nearest), rounded half to even), 5-bit sub-pixel positions and its remap's
    15-bit bilinear weights ((32 - ax)(32 - ay) * 32, ...), (sum + 2^14) >> 15
  * the masked median (the same uint8 sort with the valid count)
On the CPU, the same calls as clean_plates: the gray images, the corners, every LK + RANSAC homography (worker
threads, ahead of the GPU) and the Telea inpaint of pixels that no other frame shows. The prep loops that
clean_plates runs one frame at a time run in the worker threads too.
The reference frames within +-span_sec stay on the GPU (_Ring): 8.3 MB per 1080x1920 frame, 3.7 GB at 30 fps; the
candidates are sampled 16 at a time (~1.5 GB of temporaries at 1080x1920). With less free VRAM than both, clean_plates
runs instead (same results).

Options that change the plates (all off by default, the byte-identical path above):
  work_short   the homographies (adjacent KLT and the per-pair LK refinement) on grey images scaled to this short
               side, mapped back as S^-1 H S: 1904 LK + RANSAC calls took 57 s of CPU at 1080x1920 (downtown_bar,
               16 threads), competing with fusion v2 running beside the removal
  static_bg    when the chained homographies move the image corners by < STATIC_FRAC of the diagonal over the whole
               clip (a fixed camera), one background per window of span_sec: the per-pixel median of the window's
               sampled frames where they show background, the never-shown pixels filled once by `inpaint`; each
               frame's hole is replaced by its window's background. A static camera barely shows the background
               behind people that stay put, so the per-frame fill inpainted 75% of downtown_bar's hole pixels
  inpaint      callable(img, mask) -> img for the pixels no frame shows (default cv2.inpaint Telea); e.g.
               avatar/inpaint_lama.inpaint
  mosaic_sec   window backgrounds for a moving camera too (_mosaic_plates): per window of mosaic_sec, the middle frame r
               gets one background over every pixel any window frame needs (their holes, and shadow bands, mapped
               into r), from the same per-pixel median of the frames within +-span; the pixels none of them shows are
               inpainted once; each frame's hole is the window background warped into it (one refined homography per
               frame). The per-frame fill inpainted every frame on its own (texture can change from frame to frame)
               and refined ~16 homographies per frame; here ~16 per window plus one per frame
  shadow       with mosaic_sec: also replace the targets' cast shadows, in two passes. Pass 1 builds the window
               backgrounds as above and finds each frame's shadow: band pixels (within shadow_frac of the frame height
               of its hole) darker than the background (luminance 0.2-0.8 x) with the same chromaticity, in components
               touching the hole. Pass 2 treats the shadows as the people are treated: excluded from every frame that
               lends pixels (dilated like the exclusion masks) and filled in their own frame. A shadow is outside the
               person mask, so it stayed in the plate, and other frames' shadows were copied into the fill (the people
               stay around the same place, so a pass-1 background still holds some shadow)
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from avatar.removal import _corners, _homography, clean_plates

_BLOCK_W = 64   # WarpPerspectiveInvoker: bh0 = min(32 / 2, rows), bw0 = min(32 * 32 / bh0, cols)


def _dilate(m, kernel):
    """cv2.dilate of 0/1 masks (B, H, W) with a structuring element (default anchor and border) -> bool (B, H, W),
    or None when a count is not clearly a whole number (the caller then runs cv2.dilate).
    The correlation counts come from an FFT: cuDNN's conv2d takes ~0.9 s per 1080x1920 frame with the 60x60 element
    of --hq (cv2.dilate 51 ms, the FFT ~1 ms)."""
    import torch
    import torch.nn.functional as F

    kh, kw = kernel.shape
    ay, ax = kh // 2, kw // 2
    x = F.pad(m.float(), (ax, kw - 1 - ax, ay, kh - 1 - ay))
    s = x.shape[-2:]
    k = torch.flip(torch.from_numpy(kernel.astype(np.float32)).to(m.device), (0, 1))
    y = torch.fft.irfft2(torch.fft.rfft2(x) * torch.fft.rfft2(k, s=s), s=s)
    y = y[:, kh - 1:kh - 1 + m.shape[1], kw - 1:kw - 1 + m.shape[2]]
    if bool(((y - y.round()).abs() > 0.25).any()):
        return None
    return y > 0.5


def _warp_sample(img, Minv, xs, ys):
    """Bilinear samples (N, 3) float32 of img (H, W, 3) uint8 tensor at the points Minv @ (xs, ys, 1); out of the
    image -> ok False."""
    import torch
    H, W = img.shape[:2]
    M = torch.as_tensor(Minv, dtype=torch.float64, device=img.device)
    X = M[0, 0] * xs + M[0, 1] * ys + M[0, 2]
    Y = M[1, 0] * xs + M[1, 1] * ys + M[1, 2]
    Z = M[2, 0] * xs + M[2, 1] * ys + M[2, 2]
    X, Y = X / Z, Y / Z
    ok = (X >= 0) & (X <= W - 1) & (Y >= 0) & (Y <= H - 1)
    x0 = X.floor().clamp(0, W - 2).long()
    y0 = Y.floor().clamp(0, H - 2).long()
    ax = (X - x0).clamp(0, 1).float()[:, None]
    ay = (Y - y0).clamp(0, 1).float()[:, None]
    f = img.float()
    v = (f[y0, x0] * (1 - ax) * (1 - ay) + f[y0, x0 + 1] * ax * (1 - ay)
         + f[y0 + 1, x0] * (1 - ax) * ay + f[y0 + 1, x0 + 1] * ax * ay)
    return v, ok


def _mosaic_plates(frames, holes, n, H, W, L, cands_of, homography, pts, C, ring, inpaint, out, dev, log, t0, pool,
                   shadow, shadow_frac, fill=True, quantile=0.5, ring_check=False):
    """mosaic_sec's plates (module docstring): one background per window of L frames, warped into each frame.
    shadow: also find each frame's cast shadows (-> the 4th result, uint8 masks); fill=False: only find them.
    quantile: of the valid samples per pixel (0.5: the median, as the per-frame fill). Shadow detection uses a higher
    one: where the people stay, most samples of a pixel can be shadowed, and their median is a shadowed background.
    ring_check: drop the candidates that do not match r around the region they would fill (_ring_reject).
    -> (plates, holes, stats, shadow masks or None)."""
    import torch
    rad = max(3, int(round(shadow_frac * H)))
    by_plate = by_inpaint = n_shadow = 0
    n_samples = n_rejected = 0
    shadows = [np.zeros((H, W), np.uint8) for _ in range(n)] if shadow else None
    for a in range(0, n, L):
        b = min(n, a + L)
        r = (a + b - 1) // 2
        win = list(range(a, b))
        if not any(np.asarray(holes[t]).any() for t in win):
            for t in win:
                out[t] = np.array(frames[t])
            continue

        def h_rt(t):   # frame r -> frame t (refined from the chain), and the regions frame t needs
            if t == r:
                Hm = np.eye(3)
            else:
                Hc = C[t] @ np.linalg.inv(C[r])
                Hc /= Hc[2, 2]
                Hm = homography(r, t, pts[r], init=Hc)
                Hm = Hc if Hm is None else Hm
            hole = np.asarray(holes[t]).astype(np.uint8)
            if shadow:   # within rad of the hole (a distance transform: a disc this large is slow as a dilation)
                band = (cv2.distanceTransform(1 - hole, cv2.DIST_L2, 3) <= rad).astype(np.uint8)
            else:
                band = hole
            return Hm, hole, band
        per = dict(zip(win, pool.map(h_rt, win)))
        need = np.zeros((H, W), np.uint8)
        for t in win:   # what frame t needs, in r's pixels
            Hm, _, band = per[t]
            need |= cv2.warpPerspective(band, np.linalg.inv(Hm), (W, H), flags=cv2.INTER_NEAREST)
        ys, xs = np.nonzero(need)
        img = ring.f[ring.slot(r)]
        Ms = []
        cands = cands_of(r)
        for u in cands:   # = inverse_maps(r): H(u -> r) refined, inverted for the sampler
            Hc = C[r] @ np.linalg.inv(C[u])
            Hc /= Hc[2, 2]
            Hm = homography(u, r, pts[u], init=Hc)
            Hm = Hc if Hm is None else Hm
            Ms.append(cv2.invert(Hm)[1].ravel().tolist())
        yt, xt = torch.from_numpy(ys).to(dev), torch.from_numpy(xs).to(dev)
        mosaic = img.clone()
        shown = torch.zeros(len(ys), dtype=torch.bool, device=dev)
        if cands:
            slots = [ring.slot(u) for u in cands]
            Mt = torch.tensor(Ms, dtype=torch.float64, device=dev)
            yf, xf = yt.double(), xt.double()
            parts = [ring.sample(slots[k:k + 16], Mt[k:k + 16], xf, yf) for k in range(0, len(cands), 16)]
            v = torch.cat([p_[0] for p_ in parts])
            oks = torch.cat([p_[1] for p_ in parts])
            del parts
            if ring_check:
                excl_r = ring.e[ring.slot(r)].cpu().numpy()
                bad = _ring_reject(ring, img, slots, Mt, need, excl_r, ys, xs, dev)
                if bad is not None:
                    n_samples += int(oks.sum())
                    n_rejected += int((oks & bad).sum())
                    oks &= ~bad
            v[~oks] = 255
            v = v.sort(dim=0).values
            c = oks.sum(dim=0)
            if quantile == 0.5:
                lo = torch.div(c - 1, 2, rounding_mode="floor").clamp(min=0)
                hi = torch.div(c, 2, rounding_mode="floor")
            else:   # per channel sorted values: the quantile of the valid ones
                lo = hi = (quantile * (c - 1).clamp(min=0).float()).floor().long()
            med = (v.gather(0, lo[None, :, None].expand(1, -1, 3))[0].float()
                   + v.gather(0, hi[None, :, None].expand(1, -1, 3))[0].float()) / 2
            shown = c > 0
            mosaic[yt[shown], xt[shown]] = (med[shown] + 0.5).clamp(0, 255).to(torch.uint8)
            del v, oks, med
        # never shown: r's own hole pixels need a fill; the rest of `need` (other frames' bands) keeps r's pixels
        hole_r = torch.from_numpy(np.asarray(holes[r])).to(dev)
        never = ~shown & hole_r[yt, xt]   # outside r's own hole, r's pixel is background at r
        rest = np.zeros((H, W), np.uint8)
        rest_any = bool(never.any())
        if rest_any:
            rest[ys[never.cpu().numpy()], xs[never.cpu().numpy()]] = 255
        mos = mosaic.cpu().numpy()
        if rest_any:
            mos = inpaint(mos, rest)
        mos_t = torch.from_numpy(mos).to(dev)
        rest_t = torch.from_numpy(rest).to(dev)
        for t in win:
            Hm, hole, band = per[t]
            frame = np.array(frames[t])
            sel = band > 0
            if not sel.any():
                out[t] = frame
                continue
            py, px = np.nonzero(sel)
            pyt, pxt = torch.from_numpy(py).to(dev), torch.from_numpy(px).to(dev)
            Minv = np.linalg.inv(Hm)   # t's pixels -> r's
            bg, ok = _warp_sample(mos_t, Minv, pxt.double(), pyt.double())
            replace = torch.from_numpy(hole[py, px] > 0).to(dev) & ok
            if shadow:
                fr = torch.from_numpy(frame).to(dev)[pyt, pxt].float()
                yf_ = fr.mean(1)
                yb = bg.mean(1).clamp(min=1)
                ratio = yf_ / yb
                chroma = (fr / fr.sum(1, keepdim=True).clamp(min=1) - bg / bg.sum(1, keepdim=True).clamp(min=1)).abs().max(1).values
                cand = ok & ~replace & (ratio > 0.2) & (ratio < 0.8) & (chroma < 0.06)
                sm = np.zeros((H, W), np.uint8)
                sm[py[cand.cpu().numpy()], px[cand.cpu().numpy()]] = 1
                sm = cv2.morphologyEx(sm, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
                sm = cv2.morphologyEx(sm, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
                nlab, lab = cv2.connectedComponents(sm)
                touch = np.unique(lab[(cv2.dilate(hole, np.ones((9, 9), np.uint8)) > 0) & (sm > 0)])
                keep = np.isin(lab, touch[touch > 0]) & sel
                n_shadow += int(keep.sum())
                shadows[t] = keep.astype(np.uint8)
                replace = replace | torch.from_numpy(keep[py, px]).to(dev)
            if not fill:
                continue
            idx = replace.nonzero().flatten()
            frame[py[idx.cpu().numpy()], px[idx.cpu().numpy()]] = (bg[idx] + 0.5).clamp(0, 255).to(torch.uint8).cpu().numpy()
            out[t] = frame
            hp = hole > 0
            n_h = int(hp.sum())
            if n_h:   # how the hole's pixels were filled: from r's inpainted pixels or from the median
                hy, hx = np.nonzero(hp)
                q, _ = _warp_sample(rest_t[..., None].expand(H, W, 3).contiguous(), Minv,
                                    torch.from_numpy(hx).to(dev).double(), torch.from_numpy(hy).to(dev).double())
                n_inp = int((q[:, 0] > 127).sum())
                by_inpaint += n_inp
                by_plate += n_h - n_inp
    tot = max(by_plate + by_inpaint, 1)
    stats = {"hole_px": tot, "clean_plate_%": round(100 * by_plate / tot, 1),
             "inpaint_%": round(100 * by_inpaint / tot, 1), "shadow_px": n_shadow, "mosaic_frames": L}
    if ring_check:
        stats["ring_rejected_%"] = round(100 * n_rejected / max(n_samples, 1), 1)
    log(f"[removal] window backgrounds of {L} frames: hole pixels from other frames {stats['clean_plate_%']}%, "
        f"inpainted {stats['inpaint_%']}%, shadow pixels replaced {n_shadow}"
        + (f", samples dropped by the ring check {stats['ring_rejected_%']}%" if ring_check else "")
        + f" (total {time.perf_counter() - t0:.0f} s)")
    return out, holes, stats, shadows


def _static_plates(frames, holes, excl, n, H, W, span, stride, inpaint, scratch, dev, log, t0):
    """static_bg's plates: per window of `span` frames, one background (the masked per-pixel median of the frames
    sampled every `stride` within +-span of the window, where they show background; pixels that none shows are
    filled by `inpaint`), pasted into each frame's hole."""
    import torch
    if scratch is not None:
        from disk_arrays import scratch as disk
        out = disk(f"{scratch}/removal_plates.npy", (n, H, W, 3), np.uint8)
    else:
        out = [None] * n
    by_plate = by_inpaint = 0
    for a in range(0, n, span):
        b = min(n, a + span)
        need = np.zeros((H, W), bool)
        for t in range(a, b):
            need |= np.asarray(holes[t])
        if not need.any():
            for t in range(a, b):
                out[t] = frames[t].copy() if scratch is None else frames[t]
            continue
        samp = list(range(max(0, a - span), min(n, b + span), stride))
        ys, xs = np.nonzero(need)
        yt, xt = torch.from_numpy(ys).to(dev), torch.from_numpy(xs).to(dev)
        v = torch.stack([torch.from_numpy(np.asarray(frames[u])).to(dev)[yt, xt] for u in samp])     # (k, P, 3)
        ok = torch.stack([torch.from_numpy(np.asarray(excl[u])).to(dev)[yt, xt] == 0 for u in samp])  # (k, P)
        v[~ok] = 255
        v = v.sort(dim=0).values
        c = ok.sum(dim=0)
        lo = torch.div(c - 1, 2, rounding_mode="floor").clamp(min=0)
        hi = torch.div(c, 2, rounding_mode="floor")
        med = (v.gather(0, lo[None, :, None].expand(1, -1, 3))[0].float()
               + v.gather(0, hi[None, :, None].expand(1, -1, 3))[0].float()) / 2
        shown = (c > 0).cpu().numpy()
        bg = np.asarray(frames[(a + b) // 2]).copy()
        bg[ys[shown], xs[shown]] = (med[c > 0] + 0.5).clamp(0, 255).to(torch.uint8).cpu().numpy()
        rest = np.zeros((H, W), np.uint8)
        rest[ys[~shown], xs[~shown]] = 255
        if rest.any():
            bg = inpaint(bg, rest)
        for t in range(a, b):
            h = np.asarray(holes[t])
            img = np.array(frames[t])
            img[h] = bg[h]
            out[t] = img
            by_inpaint += int((h & (rest > 0)).sum())
            by_plate += int(h.sum()) - int((h & (rest > 0)).sum())
    tot = max(by_plate + by_inpaint, 1)
    stats = {"hole_px": tot, "clean_plate_%": round(100 * by_plate / tot, 1),
             "inpaint_%": round(100 * by_inpaint / tot, 1), "static": True}
    log(f"[removal] hole pixels from the window backgrounds {stats['clean_plate_%']}%, inpainted {stats['inpaint_%']}% "
        f"(static camera; total {time.perf_counter() - t0:.0f} s)")
    return out, holes, stats


class _Ring:
    """The reference frames and their exclusion masks on the GPU, slot u % size (any `size` consecutive frames have
    their own slots)."""

    def __init__(self, frames, excl, size, dev):
        import torch

        H, W = frames[0].shape[:2]
        self.frames, self.excl, self.size, self.H, self.W = frames, excl, size, H, W
        self.f = torch.empty((size, H, W, 3), dtype=torch.uint8, device=dev)
        self.e = torch.empty((size, H, W), dtype=torch.uint8, device=dev)
        self.held = [-1] * size

    def slot(self, u):
        import torch

        s = u % self.size
        if self.held[s] != u:
            self.f[s].copy_(torch.from_numpy(np.ascontiguousarray(self.frames[u])))
            self.e[s].copy_(torch.from_numpy(np.ascontiguousarray(self.excl[u])))
            self.held[s] = u
        return s

    def sample(self, slots, Ms, xs, ys):
        """For each candidate k: cv2.warpPerspective(frame, H_k, INTER_LINEAR, border 0)[ys, xs] -> (K, N, 3) uint8
        and cv2.warpPerspective(excl, H_k, INTER_NEAREST, border 1)[ys, xs] == 0 -> (K, N) bool, with Ms the
        cv2.invert(H_k) rows (K, 9) float64 and xs, ys the hole pixels (N,) float64."""
        import torch

        H, W = self.H, self.W
        M = [Ms[:, i:i + 1] for i in range(9)]
        bw = min(_BLOCK_W, W)
        xb = torch.div(xs, bw, rounding_mode="floor") * bw
        x1 = xs - xb
        X0 = M[0] * xb + M[1] * ys + M[2]
        Y0 = M[3] * xb + M[4] * ys + M[5]
        W0 = M[6] * xb + M[7] * ys + M[8]
        X0 = X0 + M[0] * x1
        Y0 = Y0 + M[3] * x1
        w = W0 + M[6] * x1
        nz = w != 0
        w = torch.where(nz, w, 1.0)
        base = (torch.as_tensor(slots, device=xs.device)[:, None] * (H * W))

        def rounded(scale):
            r = torch.where(nz, scale / w, 0.0)
            return ((X0 * r).clamp(-2.0 ** 31, 2.0 ** 31 - 1).round().int(),
                    (Y0 * r).clamp(-2.0 ** 31, 2.0 ** 31 - 1).round().int())

        X, Y = rounded(32.0)
        ix, iy = (X >> 5).clamp(-32768, 32767), (Y >> 5).clamp(-32768, 32767)   # saturate_cast<short>
        ax, ay = X & 31, Y & 31
        del X, Y
        # (sum_k v_k * 32 * wy_k * wx_k + 2^14) >> 15 == (sum_k v_k * wy_k * wx_k + 2^9) >> 10
        cols = [((ix >= 0) & (ix < W), ix.clamp(0, W - 1), 32 - ax), ((ix >= -1) & (ix < W - 1), (ix + 1).clamp(0, W - 1), ax)]
        rows = [((iy >= 0) & (iy < H), iy.clamp(0, H - 1).long() * W + base, 32 - ay),
                ((iy >= -1) & (iy < H - 1), (iy + 1).clamp(0, H - 1).long() * W + base, ay)]
        del ix, iy, ax, ay
        flat = self.f.view(-1, 3)
        acc = None
        for vy, ry, wy in rows:
            for vx, cx, wx in cols:
                v = flat[ry + cx].int() * (wy * wx * (vy & vx))[..., None]
                acc = v if acc is None else acc.add_(v)
        del rows, cols, v
        vals = ((acc + (1 << 9)) >> 10).clamp(0, 255).to(torch.uint8)
        del acc
        X, Y = rounded(1.0)
        inb = (Y >= 0) & (Y < H) & (X >= 0) & (X < W)
        e = self.e.view(-1)[Y.clamp(0, H - 1).long() * W + base + X.clamp(0, W - 1)]
        oks = (e == 0) & inb
        return vals, oks


STATIC_FRAC = 0.004   # static_bg: max corner motion of the chained homographies, as a fraction of the diagonal

# ring_check: a homography aligns one plane; where the scene has depth, a candidate frame can show something nearer
# (IMG_3386 seg09: the whiteboard beside a seated person, swept over the wall behind them by the camera's pan) at the
# hole's pixels, valid by its exclusion mask, and the median of the few frames that uncover the hole pasted the
# whiteboard's white there. The same misalignment shows just outside the hole: each candidate is compared with r on a
# ring of RING_FRAC x H around the needed region (outside r's exclusion), its per-channel median offset removed
# (exposure), and the absolute error spread into the region by normalised convolution (sigma RING_SIGMA_FRAC x H, at
# 1 / RING_DOWN); a candidate is dropped where that local error exceeds RING_MAX. Pixels no candidate keeps are
# inpainted.
RING_FRAC = 0.04
RING_SIGMA_FRAC = 0.06
RING_DOWN = 8
RING_MAX = 30.0


def _ring_reject(ring, img, slots, Mt, need, excl_r, ys, xs, dev):
    """-> (K, N) bool: candidate k is inconsistent with r around need pixel (ys, xs); None without a usable ring."""
    import torch
    import torch.nn.functional as F
    H, W = need.shape
    dist = cv2.distanceTransform(1 - need, cv2.DIST_L2, 3)
    band = (dist > 0) & (dist <= max(2.0, RING_FRAC * H)) & (excl_r == 0)
    band[1::2, :] = False   # every other pixel is plenty for a local mean
    band[:, 1::2] = False
    ry, rx = np.nonzero(band)
    if len(ry) < 50:
        return None
    ryt, rxt = torch.from_numpy(ry).to(dev), torch.from_numpy(rx).to(dev)
    parts = [ring.sample(slots[k:k + 16], Mt[k:k + 16], rxt.double(), ryt.double()) for k in range(0, len(slots), 16)]
    v = torch.cat([p[0] for p in parts]).float()
    ok = torch.cat([p[1] for p in parts])
    d = v - img[ryt, rxt].float()[None]
    d_nan = torch.where(ok[..., None], d, torch.full_like(d, float("nan")))
    off = torch.nan_to_num(d_nan.nanmedian(dim=1).values)                 # (K, 3): exposure differences
    e = (d - off[:, None]).abs().mean(-1) * ok                              # (K, Nr)
    gh, gw = -(-H // RING_DOWN), -(-W // RING_DOWN)
    idx = (ryt // RING_DOWN) * gw + rxt // RING_DOWN
    K = len(slots)
    E = torch.zeros((K, gh * gw), device=dev).index_add_(1, idx, e)
    Wt = torch.zeros((K, gh * gw), device=dev).index_add_(1, idx, ok.float())
    s = RING_SIGMA_FRAC * H / RING_DOWN
    r_ = int(3 * s) + 1
    g = torch.exp(-torch.arange(-r_, r_ + 1, device=dev, dtype=torch.float32) ** 2 / (2 * s * s))
    g = g / g.sum()

    def blur(x):
        x = x.view(K, 1, gh, gw)
        x = F.conv2d(F.pad(x, (r_, r_, 0, 0)), g.view(1, 1, 1, -1))
        return F.conv2d(F.pad(x, (0, 0, r_, r_)), g.view(1, 1, -1, 1)).view(K, gh * gw)
    Eb, Wb = blur(E), blur(Wt)
    glob_ = E.sum(1, keepdim=True) / Wt.sum(1, keepdim=True).clamp(min=1)   # far from any ring sample
    loc = torch.where(Wb > 1e-3, Eb / Wb.clamp(min=1e-6), glob_.expand_as(Eb))
    q = (ys // RING_DOWN) * gw + xs // RING_DOWN
    return loc[:, q] > RING_MAX


def _telea(img, mask):
    return cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA)


def clean_plates_gpu(frames, person_masks, fps=30.0, dilate=9, exclude_dilate=15, span_sec=7.5, stride_sec=8 / 30,
                     log=print, workers=4, scratch=None, device="cuda", work_short=None, static_bg=False,
                     inpaint=None, mosaic_sec=None, shadow=False, shadow_frac=0.15, ring_check=False):
    """removal.clean_plates (same arguments and results); `workers` CPU threads compute the homographies.
    work_short / static_bg / inpaint: see the module docstring (not byte-identical)."""
    import torch
    inpaint = inpaint or _telea

    n = len(frames)
    H, W = frames[0].shape[:2]
    span = max(1, int(round(span_sec * fps)))
    stride = max(1, int(round(stride_sec * fps)))
    dev = torch.device(device)
    need = min(n, 2 * span + 1) * H * W * 4 + 2 * 2 ** 30   # the window + sampling temporaries
    if torch.cuda.mem_get_info(dev)[0] < need:
        log(f"[removal] {torch.cuda.mem_get_info(dev)[0] / 2 ** 30:.1f} GiB of VRAM free < {need / 2 ** 30:.1f} GiB "
            f"for the GPU fill: CPU fill")
        return clean_plates(frames, person_masks, fps=fps, dilate=dilate, exclude_dilate=exclude_dilate,
                            span_sec=span_sec, stride_sec=stride_sec, log=log, workers=workers, scratch=scratch)
    workers = max(1, int(workers))
    k_hole = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate))
    k_ex = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (exclude_dilate, exclude_dilate))
    sc = min(1.0, work_short / min(H, W)) if work_short else 1.0   # homography grid scale
    gh, gw = (int(round(H * sc)), int(round(W * sc))) if sc < 1 else (H, W)
    S = np.diag([gw / W, gh / H, 1.0])
    Si = np.linalg.inv(S)
    if scratch is None:
        holes = [None] * n
        excl = [None] * n
        grays = [None] * n
    else:
        from disk_arrays import scratch as disk
        holes = disk(f"{scratch}/removal_holes.npy", (n, H, W), bool)
        excl = disk(f"{scratch}/removal_excl.npy", (n, H, W), np.uint8)
        grays = disk(f"{scratch}/removal_grays.npy", (n, gh, gw), np.uint8)
    pts = [None] * n
    stream = torch.cuda.Stream(dev)   # off fusion's default stream (the removal runs in a thread beside it)
    t0 = time.perf_counter()
    with torch.cuda.stream(stream), ThreadPoolExecutor(workers) as pool:
        def gray_corners(t):
            g = cv2.cvtColor(frames[t], cv2.COLOR_BGR2GRAY)
            e = excl[t]
            if sc < 1:   # the homography grid (work_short)
                g = cv2.resize(g, (gw, gh), interpolation=cv2.INTER_AREA)
                e = cv2.resize(np.asarray(e), (gw, gh), interpolation=cv2.INTER_NEAREST)
            grays[t] = g
            pts[t] = _corners(grays[t], ((1 - e) * 255).astype(np.uint8))

        jobs = []
        for a in range(0, n, 8):
            b = min(n, a + 8)
            mb = np.stack([np.asarray(person_masks[t], dtype=np.uint8) for t in range(a, b)])
            m = torch.from_numpy(mb).to(dev)
            hb, eb = _dilate(m, k_hole), _dilate(m, k_ex)
            hb = hb.cpu().numpy() if hb is not None else np.stack([cv2.dilate(x, k_hole) > 0 for x in mb])
            eb = eb.to(torch.uint8).cpu().numpy() if eb is not None else np.stack([cv2.dilate(x, k_ex) for x in mb])
            for t in range(a, b):
                holes[t], excl[t] = hb[t - a], eb[t - a]
                jobs.append(pool.submit(gray_corners, t))
        for j in jobs:
            j.result()

        # chained adjacent homographies: C[t] maps frame 0 -> frame t (pairs in parallel, the product in order)
        def homography(a_, b_, pa, init=None):   # on the homography grid, returned in frame pixels
            Hm = _homography(grays[a_], grays[b_], pa, init=None if init is None else S @ init @ Si)
            return Hm if Hm is None or sc == 1 else Si @ Hm @ S

        Ha = list(pool.map(lambda t: homography(t - 1, t, pts[t - 1]), range(1, n)))
        C = np.tile(np.eye(3), (n, 1, 1))
        for t in range(1, n):
            C[t] = (Ha[t - 1] if Ha[t - 1] is not None else np.eye(3)) @ C[t - 1]
            C[t] /= C[t][2, 2]
        t_prep = time.perf_counter() - t0
        if static_bg:
            corners = np.array([[0, 0, 1], [W, 0, 1], [0, H, 1], [W, H, 1]], np.float64).T
            motion = max(float(np.abs((Ct @ corners)[:2] / (Ct @ corners)[2:] - corners[:2]).max()) for Ct in C)
            if motion < STATIC_FRAC * np.hypot(H, W):
                log(f"[removal] static camera (corner motion {motion:.1f} px): window backgrounds")
                return _static_plates(frames, holes, excl, n, H, W, span, stride, inpaint, scratch, dev, log, t0)
            log(f"[removal] moving camera (corner motion {motion:.1f} px): per-frame fill")

        def cands_of(t):
            return [u for u in range(max(0, t - span), min(n, t + span + 1), stride) if u != t]

        def inverse_maps(t):
            """cv2.invert of the H(u -> t) that clean_plates' fill_frame warps with, per candidate u."""
            if not holes[t].any():
                return None
            Ms = []
            for u in cands_of(t):
                Hc = C[t] @ np.linalg.inv(C[u])
                Hc /= Hc[2, 2]
                Hm = homography(u, t, pts[u], init=Hc)
                Hm = Hc if Hm is None else Hm
                Ms.append(cv2.invert(Hm)[1].ravel().tolist())
            return Ms

        if scratch is not None:
            out = disk(f"{scratch}/removal_plates.npy", (n, H, W, 3), np.uint8)
        else:
            out = [None] * n
        ring = _Ring(frames, excl, min(n, 2 * span + 1), dev)
        if mosaic_sec:
            L = max(1, int(round(mosaic_sec * fps)))
            if not shadow:
                return _mosaic_plates(frames, holes, n, H, W, L, cands_of, homography, pts, C, ring, inpaint, out,
                                      dev, log, t0, pool, False, shadow_frac, ring_check=ring_check)[:3]
            _, _, _, shadows = _mosaic_plates(frames, holes, n, H, W, L, cands_of, homography, pts, C, ring, inpaint,
                                              [None] * n, dev, lambda *a: None, t0, pool, True, shadow_frac,
                                              fill=False, quantile=0.8)
            k_sh = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (exclude_dilate, exclude_dilate))
            excl2 = [np.maximum(np.asarray(excl[t]), cv2.dilate(shadows[t], k_sh)) for t in range(n)]
            holes2 = [np.asarray(holes[t]) | (shadows[t] > 0) for t in range(n)]
            del ring
            ring2 = _Ring(frames, excl2, min(n, 2 * span + 1), dev)
            res = _mosaic_plates(frames, holes2, n, H, W, L, cands_of, homography, pts, C, ring2, inpaint, out,
                                 dev, log, t0, pool, False, shadow_frac, ring_check=ring_check)
            res[2]["shadow_px"] = int(sum(int((sh > 0).sum()) for sh in shadows))
            return res[0], holes2, res[2]

        def store(t, res, rest):   # in a worker thread: the Telea inpaint of what no frame shows, then the plate
            if rest is not None:
                m = np.zeros((H, W), np.uint8)
                m[rest[0], rest[1]] = 255
                res = inpaint(res, m)
            out[t] = res

        filled_by_plate = filled_by_inpaint = 0
        t_wait = 0.0
        ahead = 4 * workers
        futs = {t: pool.submit(inverse_maps, t) for t in range(min(n, ahead))}
        stores, store_pool = [], ThreadPoolExecutor(4)
        for t in range(n):
            t1 = time.perf_counter()
            Ms = futs.pop(t).result()
            t_wait += time.perf_counter() - t1
            if t + ahead < n:
                futs[t + ahead] = pool.submit(inverse_maps, t + ahead)
            if Ms is None:   # no hole
                out[t] = frames[t].copy() if scratch is None else frames[t]
                continue
            img = ring.f[ring.slot(t)]
            ys, xs = torch.nonzero(torch.from_numpy(np.asarray(holes[t])).to(dev), as_tuple=True)
            cands = cands_of(t)
            if cands:
                slots = [ring.slot(u) for u in cands]
                Mt = torch.tensor(Ms, dtype=torch.float64, device=dev)
                yf, xf = ys.double(), xs.double()
                parts = [ring.sample(slots[k:k + 16], Mt[k:k + 16], xf, yf) for k in range(0, len(cands), 16)]
                v = torch.cat([p[0] for p in parts])
                oks = torch.cat([p[1] for p in parts])
                del parts
                v[~oks] = 255
                v = v.sort(dim=0).values
                c = oks.sum(dim=0)
                lo = torch.div(c - 1, 2, rounding_mode="floor").clamp(min=0)
                hi = torch.div(c, 2, rounding_mode="floor")
                a = v.gather(0, lo[None, :, None].expand(1, -1, 3))[0]
                b = v.gather(0, hi[None, :, None].expand(1, -1, 3))[0]
                med = (a.float() + b.float()) / 2
                ok = c > 0
                plate = img.clone()
                plate[ys[ok], xs[ok]] = (med[ok] + 0.5).clamp(0, 255).to(torch.uint8)
                del v, oks, a, b, med
            else:
                ok = torch.zeros(len(ys), dtype=torch.bool, device=dev)
                plate = img
            res = plate.cpu().numpy()
            n_ok = int(ok.sum())
            rest = (ys[~ok].cpu().numpy(), xs[~ok].cpu().numpy()) if n_ok < len(ys) else None
            stores.append(store_pool.submit(store, t, res, rest))
            while len(stores) > 8:
                stores.pop(0).result()
            filled_by_plate += n_ok
            filled_by_inpaint += len(ys) - n_ok
        for f in stores:
            f.result()
        store_pool.shutdown()
        del ring
    tot = max(filled_by_plate + filled_by_inpaint, 1)
    stats = {"hole_px": tot, "clean_plate_%": round(100 * filled_by_plate / tot, 1),
             "inpaint_%": round(100 * filled_by_inpaint / tot, 1)}
    log(f"[removal] hole pixels filled from other frames {stats['clean_plate_%']}%, inpainted {stats['inpaint_%']}% "
        f"(GPU fill; prep {t_prep:.0f} s, total {time.perf_counter() - t0:.0f} s, of which waiting for the "
        f"homographies {t_wait:.0f} s)")
    return out, holes, stats
