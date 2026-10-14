"""Faster drop-in for gpu_stage.run_gpu with bit-identical outputs (moge.npz, cands.npz, person.npz).

The frozen gpu_stage.py stays the reference; this module changes only where the work happens, not what is computed:
  1. SAM3 point prompts: predict() returned all 3 x 128 full-resolution mask logits per batch to the CPU as
     float32 (~200 MB per batch at 480x270), where three threshold counts were taken per mask. Here the logits stay
     on the GPU, the counts (exact integers) are taken there, and only the masks that pass the filter are copied,
     as booleans (kept on the CPU like before; only their 2x-downsampled copies stay on the GPU for dedup). The
     filter itself (iou, stability, area) is evaluated in numpy exactly as before.
  2. dedup: the frozen greedy de-duplication re-stacked the selected masks into a new matrix for every candidate
     (O(n^2) copies, 76 s on a 120-frame clip). Here all pairwise intersections of a frame's candidates come from
     one GPU matrix product of the 2x-downsampled 0/1 masks. Every partial sum is an integer < 2^24, so the
     products are exact in float32 (also under TF32, whose 10-bit mantissa holds 0 and 1 exactly); the greedy pass
     and the IoU expression are the frozen ones, in numpy float32.
  3. the mesh silhouettes come from prep's mesh depth (mesh.npz) instead of rendering every mesh again.
  4. VRAM: the 'person' concept prompt is the same text in every frame, so its text encoding is computed once (under
     the same autocast and inference mode as Sam3Processor.set_text_prompt) and the text encoder (1.32 GB of the
     3.2 GB of SAM3 weights) is moved off the GPU before the frame loop.
  5. model construction skips the random weight init that the checkpoint overwrites (utils/skip_init.py; all
     parameters and buffers verified identical by scripts/eval/check_skip_init.py).
Verified identical to gpu_stage.run_gpu (frozen): scripts/eval/check_fusion_fast.py.
"""
import math
import os
import sys
import time

import cv2
import numpy as np

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ME)
from r3common import local_gpu, REPO  # noqa: E402
sys.path.insert(0, REPO)
from utils.skip_init import skip_random_init  # noqa: E402
from gpu_stage import STRIDE_IN, STRIDE_V, STRIDE_OUT, DIL, IOU_T, STAB_T, DEDUP, MIN_PX, grid_points, load_cands  # noqa: E402,F401

POINT_BATCH = 128   # points per mask-decoder call (frozen value)


def _mask_counts(m):
    """Exact integer pixel counts without a full-resolution int64 temporary.

    Each count is bounded by H*W. Keep the original int64 numpy interface and
    fall back to int64 accumulation if a mask could overflow signed int32.
    """
    import torch
    dtype = torch.int32 if m.shape[-2] * m.shape[-1] <= 2**31 - 1 else torch.int64
    return torch.stack([(m > value).sum((1, 2), dtype=dtype)
                        for value in (1.0, -1.0, 0.0)]).to(torch.int64).cpu().numpy()


def _predict_gpu(model, st, coords, labels):
    """Sam3Image.predict_inst + SAM3InteractiveImagePredictor.predict, minus the numpy conversion.
    -> (mask logits float32 (n*3, H, W) on the GPU, iou numpy float32 (n*3,))."""
    pred = model.inst_interactive_predictor
    orig_h, orig_w = st["original_height"], st["original_width"]
    _, vision_feats, _, _ = pred.model._prepare_backbone_features(st["backbone_out"]["sam2_backbone_out"])
    vision_feats[-1] = vision_feats[-1] + pred.model.no_mem_embed
    feats = [feat.permute(1, 2, 0).view(1, -1, *fs)
             for feat, fs in zip(vision_feats[::-1], pred._bb_feat_sizes[::-1])][::-1]
    pred._features = {"image_embed": feats[-1], "high_res_feats": feats[:-1]}
    pred._is_image_set = True
    pred._orig_hw = [(orig_h, orig_w)]
    try:
        mask_input, unnorm_coords, lab, unnorm_box = pred._prep_prompts(coords, labels, None, None, True)
        masks, iou, _ = pred._predict(unnorm_coords, lab, unnorm_box, mask_input, True, return_logits=True)
    finally:
        pred._features = None
        pred._is_image_set = False
    m = masks.squeeze(0).float()
    return m.reshape(-1, orig_h, orig_w), iou.squeeze(0).float().detach().cpu().numpy().reshape(-1)


def _dedup_gpu(cands, half, chunk=1 << 16):
    """frozen gpu_stage.dedup on [(score, full mask (CPU bool), from_visible)] sorted by score; half[j]: the 2x
    downsampled mask of cands[j] (GPU bool, flattened). Intersections are accumulated over column chunks in float32:
    every partial sum stays an integer below 2^24 (a 2x-downsampled 4K frame has 2.1M pixels), so it is exact."""
    import torch
    if not cands:
        return []
    Fb = torch.stack(half)                                   # (K, D) bool
    K, Dn = Fb.shape
    inter = torch.zeros((K, K), dtype=torch.float32, device=Fb.device)
    area = torch.zeros(K, dtype=torch.float32, device=Fb.device)
    with torch.autocast("cuda", enabled=False):
        for c0 in range(0, Dn, chunk):
            F = Fb[:, c0:c0 + chunk].float()
            inter += F @ F.T
            area += F.sum(1)
    inter, area = inter.cpu().numpy(), area.cpu().numpy()
    sel = []
    for i in range(len(cands)):
        if sel:
            it = inter[sel, i]
            iou = it / (area[sel] + area[i] - it + 1e-6)     # frozen expression, numpy float32
            if iou.max() > DEDUP:
                continue
        sel.append(i)
    return sel


C2F_STRIDE = 16   # coarse grid of the "c2f" mode, in every region


def _prompt_points(model, st, pts, src, H, W, keep, half, cover=None, pad=False):
    """The frozen per-batch prompt + filter, appending (score, CPU bool mask, from_visible) to keep and the 2x
    downsampled GPU masks to half. cover (GPU bool H x W): OR-ed with every mask that passes the filter.
    pad: repeat the last point up to POINT_BATCH (fixed shapes for the compiled decoder; the extra outputs are dropped)."""
    import torch
    for s in range(0, len(pts), POINT_BATCH):
        c = pts[s:s + POINT_BATCH]
        n = len(c)
        if pad and n < POINT_BATCH:
            c = np.concatenate([c, np.repeat(c[-1:], POINT_BATCH - n, 0)])
        with torch.no_grad():
            m, iou = _predict_gpu(model, st, c[:, None, :], np.ones((len(c), 1)))
            m, iou = m[:3 * n], iou[:3 * n]
            cnt = _mask_counts(m)
        hi, lo, area = cnt[0], cnt[1], cnt[2]
        sv = np.repeat(src[s:s + POINT_BATCH], 3)
        stab = hi / np.maximum(lo, 1)
        ok = (iou >= IOU_T) & (stab >= STAB_T) & (area >= MIN_PX) & (area <= 0.5 * H * W)
        idx = np.nonzero(ok)[0]
        if len(idx):
            mk = m[torch.as_tensor(idx, device=m.device)] > 0
            if cover is not None:
                cover |= mk.any(0)
            mk_cpu = mk.cpu().numpy()
            for j, k in enumerate(idx):
                keep.append((float(iou[k] * stab[k]), mk_cpu[j], bool(sv[k])))
                half.append(mk[j, ::2, ::2].reshape(-1))
        del m


def _cands_mode(points, compile_decoder):
    return points + ("+compile" if compile_decoder else "")


def run_gpu(run, cache, owner="R3_fusion", points="full", compile_decoder=False):
    """points: "full" = the frozen point grid (bit-identical to gpu_stage); "c2f" = coarse-to-fine (see
    _c2f_points; not bit-identical, same F1 pass count on dev 18 and held-out test4, LEDGER 2026-10-14).
    compile_decoder: torch.compile the SAM mask decoder (not bit-identical; ~0.03% of mask pixels flip)."""
    import torch
    assert points in ("full", "c2f")
    os.makedirs(cache, exist_ok=True)
    T, H, W = len(run), run.H, run.W
    mode = _cands_mode(points, compile_decoder)
    if os.path.exists(f"{cache}/moge.npz") and "masked" not in np.load(f"{cache}/moge.npz").files:
        os.replace(f"{cache}/moge.npz", f"{cache}/moge_unmasked.npz")   # cache from an older version: recompute
    if os.path.exists(f"{cache}/cands.npz"):
        c = np.load(f"{cache}/cands.npz")
        old = str(c["mode"]) if "mode" in c.files else "full"            # caches before the modes: full grid
        if old != mode:                                                  # other mode: recompute candidates
            os.replace(f"{cache}/cands.npz", f"{cache}/cands_{old.replace('+', '_')}.npz")
            if os.path.exists(f"{cache}/evidence.pkl"):
                os.remove(f"{cache}/evidence.pkl")
    if os.path.exists(f"{cache}/moge.npz") and os.path.exists(f"{cache}/cands.npz"):
        return  # cached
    D = np.load(f"{cache}/mesh.npz")["D"]                                # prep's mesh depth (T,P,H,W)
    sils = [(D[i] > 0).any(0) for i in range(T)]
    del D
    t0 = time.time()
    with local_gpu(owner):
        if not os.path.exists(f"{cache}/moge.npz"):
            from moge.model.v2 import MoGeModel
            with skip_random_init():
                moge = MoGeModel.from_pretrained("C:/VScodeFiles/checkpoints/moge-2-vitl-normal/model.pt").cuda().eval()
            f = next(run.focal(p, i) for i in range(T) for p in run.pids if run.focal(p, i))
            fov_x = math.degrees(2 * math.atan(W / 2 / f))
            Z = np.zeros((T, H, W), np.float32)
            for i in range(T):
                rgb = cv2.cvtColor(run.image(i), cv2.COLOR_BGR2RGB)
                t = torch.tensor(rgb / 255.0, dtype=torch.float32, device="cuda").permute(2, 0, 1)
                with torch.no_grad():
                    o = moge.infer(t, fov_x=fov_x, apply_mask=False)
                z = o["points"][..., 2].float().cpu().numpy()
                z[~np.isfinite(z)] = 0
                z[~o["mask"].cpu().numpy().astype(bool)] = 0
                Z[i] = z
            np.savez_compressed(f"{cache}/moge.npz", Z=Z, fov_x=fov_x, masked=True)
            del moge
            torch.cuda.empty_cache()
            print(run.name, "moge", round(time.time() - t0, 1), flush=True)
        if not os.path.exists(f"{cache}/cands.npz"):
            sys.path.insert(0, f"{REPO}/models/sam3")
            from PIL import Image
            from sam3.model_builder import build_sam3_image_model
            from sam3.model.sam3_image_processor import Sam3Processor
            with skip_random_init():
                model = build_sam3_image_model(checkpoint_path="C:/VScodeFiles/checkpoints/sam3/sam3.pt",
                                               load_from_HF=False, enable_inst_interactivity=True)
            # Keep POINT_BATCH and all arithmetic unchanged; broadcast the shared
            # image/position inputs instead of copying them for each point.
            model.inst_interactive_predictor.model.sam_mask_decoder.expand_image_embeddings = True
            if compile_decoder:   # fixed shapes: every batch is padded to POINT_BATCH points (_prompt_points)
                dec = model.inst_interactive_predictor.model.sam_mask_decoder
                dec.forward = torch.compile(dec.forward, dynamic=False)
            proc = Sam3Processor(model, confidence_threshold=0.3)
            with torch.autocast("cuda", dtype=torch.bfloat16), torch.inference_mode():
                text_person = model.backbone.forward_text(["person"], device=proc.device)
            model.backbone.language_backbone.to("cpu")
            # SAM3 holds an outer autocast context open. Moving the encoder to CPU
            # does not release its cached BF16 weights; the saved text features
            # remain valid after clearing these unused weight copies.
            torch.clear_autocast_cache()
            torch.cuda.empty_cache()

            @torch.inference_mode()
            def person_prompt(state):
                """Sam3Processor.set_text_prompt("person", state) with the text encoding computed once."""
                state["backbone_out"].update(text_person)
                if "geometric_prompt" not in state:
                    state["geometric_prompt"] = model._get_dummy_prompt()
                return proc._forward_grounding(state)
            allm, frame_idx, scores, inV = [], [], [], []
            pm, pf, ps = [], [], []
            for i in range(T):
                img = cv2.cvtColor(run.image(i), cv2.COLOR_BGR2RGB)
                lab = run.mask(i)
                sil = sils[i]
                region = cv2.dilate(sil.astype(np.uint8), np.ones((2 * DIL + 1,) * 2, np.uint8)) > 0
                V = lab > 0
                Vd = cv2.dilate(V.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
                inner = sil & ~Vd
                inv = sil & V & ~(cv2.dilate((~V).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
                pts_in = grid_points(inner, STRIDE_IN)
                pts_v = grid_points(inv, STRIDE_V, off=2)
                pts_o = grid_points(region & ~sil & ~Vd, STRIDE_OUT)
                pts = np.concatenate([pts_in, pts_o, pts_v])
                src = np.concatenate([np.zeros(len(pts_in) + len(pts_o), bool), np.ones(len(pts_v), bool)])
                keep = []   # (score, CPU bool mask, from_visible), same order as the frozen list
                half = []   # 2x-downsampled masks on the GPU, for dedup
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    st = proc.set_image(Image.fromarray(img))
                    if points == "full":
                        _prompt_points(model, st, pts, src, H, W, keep, half, pad=compile_decoder)
                    else:
                        # coarse grid first; then only the fine points that no passing coarse mask covers
                        c_in, c_o = grid_points(inner, C2F_STRIDE), grid_points(region & ~sil & ~Vd, C2F_STRIDE)
                        c_v = grid_points(inv, C2F_STRIDE, off=2)
                        cp = np.concatenate([c_in, c_o, c_v])
                        csrc = np.concatenate([np.zeros(len(c_in) + len(c_o), bool), np.ones(len(c_v), bool)])
                        cover = torch.zeros((H, W), dtype=torch.bool, device="cuda")
                        _prompt_points(model, st, cp, csrc, H, W, keep, half, cover=cover, pad=compile_decoder)
                        pi = pts.astype(int)
                        unc = ~cover.cpu().numpy()[pi[:, 1], pi[:, 0]] if len(pts) else np.zeros(0, bool)
                        _prompt_points(model, st, pts[unc], src[unc], H, W, keep, half, pad=compile_decoder)
                    proc.reset_all_prompts(st)
                    r = person_prompt(st)
                    for mk, sc in zip(r["masks"][:, 0].cpu().numpy().astype(bool), r["scores"].float().cpu().numpy()):
                        pm.append(np.packbits(mk.reshape(-1)))
                        pf.append(i)
                        ps.append(float(sc))
                order = sorted(range(len(keep)), key=lambda j: -keep[j][0])   # = frozen keep.sort (stable)
                keep = [keep[j] for j in order]
                half = [half[j] for j in order]
                sel = _dedup_gpu(keep, half)
                for j in sel:
                    allm.append(np.packbits(keep[j][1].reshape(-1)))
                    frame_idx.append(i)
                    scores.append(keep[j][0])
                    inV.append(keep[j][2])
                if i % 10 == 0:
                    print(run.name, i, "pts", len(pts), "raw", len(keep), "kept", len(sel),
                          round(time.time() - t0, 1), flush=True)
                del keep, half
            nb = (H * W + 7) // 8
            np.savez_compressed(f"{cache}/cands.npz", masks=np.stack(allm) if allm else np.zeros((0, nb), np.uint8), frame=np.array(frame_idx),
                                score=np.array(scores), inV=np.array(inV), shape=np.array([H, W]), mode=np.array(mode))
            np.savez_compressed(f"{cache}/person.npz",
                                masks=np.stack(pm) if pm else np.zeros((0, (H * W + 7) // 8), np.uint8),
                                frame=np.array(pf, int), score=np.array(ps), shape=np.array([H, W]))
            del model, proc
            torch.cuda.empty_cache()
    print(run.name, "gpu stage", round(time.time() - t0, 1), "s", flush=True)
