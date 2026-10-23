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
  5. the mask decoder computes the image-side part of its first block once per image instead of once per prompt
     (_predict_masks_shared; bit-identical).
  6. model construction skips the random weight init that the checkpoint overwrites (utils/skip_init.py; all
     parameters and buffers verified identical by scripts/eval/check_skip_init.py).
Verified identical to gpu_stage.run_gpu (frozen): scripts/eval/check_fusion_fast.py.
"""
import contextlib
import gc
import math
import os
import sys
import threading
import time
import types

import cv2
import numpy as np

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ME)
from r3common import local_gpu, REPO, ckpt  # noqa: E402
import disk_arrays  # noqa: E402
sys.path.insert(0, REPO)
from utils.skip_init import skip_random_init  # noqa: E402
from utils.bf16_weights import bf16_weights  # noqa: E402
from gpu_stage import STRIDE_IN, STRIDE_V, STRIDE_OUT, DIL, IOU_T, STAB_T, DEDUP, MIN_PX, grid_points, load_cands  # noqa: E402,F401

POINT_BATCH = 128   # points per mask-decoder call (frozen value)
BF16_WEIGHTS = os.environ.get("FUSION_BF16_WEIGHTS", "1") == "1"   # see _sam3_stage
PARALLEL_LOAD = os.environ.get("FUSION_PARALLEL_LOAD", "1") == "1"  # see _SAM3Loader (0: build after MoGe, less VRAM)
ASYNC_GROUNDING = os.environ.get("FUSION_ASYNC_GROUNDING", "1") == "1"  # see _sam3_stage
# MoGe-2 resolution_level (its default and the frozen value: 9). 3 = ~0.54x MoGe time; not bit-identical. Kept the
# pass count on dev 51 (43 -> 44) and on the held-out test6 (7/11 = base, worst F1 -0.023), LEDGER 2026-10-17.
MOGE_LEVEL = int(os.environ.get("FUSION_MOGE_LEVEL", "3"))


# Shared image side of the mask decoder. With expand_image_embeddings, every prompt of a batch carries the same image
# embedding, positional encoding and no-mask dense embedding; the first two-way block reads only these on the image
# side before the image->token attention mixes in the prompts. So its image-side work (src + dense, keys + pe, the
# token->image k/v projections, the image->token q projection) is computed once for the image and broadcast to the
# batch (13% of _predict_gpu). Every output is bit-identical to MaskDecoder.predict_masks (logits, iou: checked on
# every batch size 1..128 and 33 batches over a clip, and end to end by scripts/eval/check_fusion_fast.py).


def _attn(A, q, k, v, qp=None, kp=None, vp=None):
    """Attention.forward with optional precomputed projections (non-fa3, eval)."""
    import torch.nn.functional as F
    q = A.q_proj(q) if qp is None else qp
    k = A.k_proj(k) if kp is None else kp
    v = A.v_proj(v) if vp is None else vp
    q, k, v = (A._separate_heads(x, A.num_heads) for x in (q, k, v))
    out = F.scaled_dot_product_attention(q, k, v, dropout_p=0.0)
    return A.out_proj(A._recombine_heads(out))


def _transformer_shared(tr, img1, pe1, point_embedding):
    """TwoWayTransformer.forward for B prompts on one image: img1 / pe1 (1, C, h, w) are the same for every prompt."""
    B = point_embedding.shape[0]
    keys1 = img1.flatten(2).permute(0, 2, 1)
    pe = pe1.flatten(2).permute(0, 2, 1)
    queries = point_embedding
    L = tr.layers[0]
    assert L.skip_first_layer_pe
    queries = L.norm1(L.self_attn(q=queries, k=queries, v=queries))
    q = queries + point_embedding
    k1 = keys1 + pe
    t2i = L.cross_attn_token_to_image
    queries = L.norm2(queries + _attn(t2i, q, None, None, kp=t2i.k_proj(k1).expand(B, -1, -1),
                                      vp=t2i.v_proj(keys1).expand(B, -1, -1)))
    queries = L.norm3(queries + L.mlp(queries))
    q = queries + point_embedding
    i2t = L.cross_attn_image_to_token
    keys = L.norm4(keys1 + _attn(i2t, None, q, queries, qp=i2t.q_proj(k1).expand(B, -1, -1)))
    key_pe = pe.expand(B, -1, -1)
    for layer in tr.layers[1:]:
        queries, keys = layer(queries=queries, keys=keys, query_pe=point_embedding, key_pe=key_pe)
    q = queries + point_embedding
    k = keys + key_pe
    queries = tr.norm_final_attn(queries + tr.final_attn_token_to_image(q=q, k=k, v=keys))
    return queries, keys


def _predict_masks_shared(self, image_embeddings, image_pe, sparse_prompt_embeddings, dense_prompt_embeddings,
                          repeat_image, high_res_features=None):
    """MaskDecoder.predict_masks, line for line, with _transformer_shared. A single prompt (repeat_image False) takes
    the original method: there the image embedding is used in its own memory layout, and the results differ."""
    import torch
    if not repeat_image:
        return type(self).predict_masks(self, image_embeddings, image_pe, sparse_prompt_embeddings,
                                        dense_prompt_embeddings, repeat_image, high_res_features)
    s = 0
    if self.pred_obj_scores:
        output_tokens = torch.cat([self.obj_score_token.weight, self.iou_token.weight, self.mask_tokens.weight], 0)
        s = 1
    else:
        output_tokens = torch.cat([self.iou_token.weight, self.mask_tokens.weight], 0)
    output_tokens = output_tokens.unsqueeze(0).expand(sparse_prompt_embeddings.size(0), -1, -1)
    tokens = torch.cat((output_tokens, sparse_prompt_embeddings), dim=1)
    assert image_embeddings.size(0) == 1 and image_pe.size(0) == 1
    d = dense_prompt_embeddings
    assert d.stride(0) == 0 or d.size(0) == 1          # the no-mask embedding, the same for every prompt
    src1 = image_embeddings.contiguous() + d[:1]
    b, (c, h, w) = tokens.shape[0], src1.shape[1:]
    hs, src = _transformer_shared(self.transformer, src1, image_pe.contiguous(), tokens)
    iou_token_out = hs[:, s, :]
    mask_tokens_out = hs[:, s + 1:(s + 1 + self.num_mask_tokens), :]
    src = src.transpose(1, 2).view(b, c, h, w)
    if not self.use_high_res_features:
        upscaled_embedding = self.output_upscaling(src)
    else:
        dc1, ln1, act1, dc2, act2 = self.output_upscaling
        feat_s0, feat_s1 = high_res_features
        upscaled_embedding = act1(ln1(dc1(src) + feat_s1))
        upscaled_embedding = act2(dc2(upscaled_embedding) + feat_s0)
    hyper_in = torch.stack([self.output_hypernetworks_mlps[i](mask_tokens_out[:, i, :])
                            for i in range(self.num_mask_tokens)], dim=1)
    b, c, h, w = upscaled_embedding.shape
    masks = (hyper_in @ upscaled_embedding.view(b, c, h * w)).view(b, -1, h, w)
    iou_pred = self.iou_prediction_head(iou_token_out)
    if self.pred_obj_scores:
        object_score_logits = self.pred_obj_score_head(hs[:, 0, :])
    else:
        object_score_logits = 10.0 * iou_pred.new_ones(iou_pred.shape[0], 1)
    return masks, iou_pred, mask_tokens_out, object_score_logits


def _mask_counts(m):
    """Exact integer counts; fuse the three comparisons when Triton is available."""
    from mask_count_reduce import count_masks
    return count_masks(m)


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
    pad: repeat the last point up to a multiple of 32 (fixed shapes for the compiled decoder; the extra outputs are
    dropped)."""
    import torch
    for s in range(0, len(pts), POINT_BATCH):
        c = pts[s:s + POINT_BATCH]
        n = len(c)
        if pad and n % 32:   # sizes 32/64/96/128: four compiled graphs, not one per batch size
            c = np.concatenate([c, np.repeat(c[-1:], 32 - n % 32, 0)])
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


def _sam3_build(compile_decoder):
    """SAM3 model + processor, ready for the frame loop. No matmul runs here, so it can run in a thread while MoGe
    runs (_SAM3Loader); the text encoding, which does compute, stays in _sam3_stage."""
    import torch
    sys.path.insert(0, f"{REPO}/models/sam3")
    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor
    with skip_random_init():
        model = build_sam3_image_model(checkpoint_path=ckpt("sam3/sam3.pt"),
                                       load_from_HF=False, enable_inst_interactivity=True)
    if BF16_WEIGHTS:
        # Every model call below runs under bf16 autocast: one bf16 copy of the Linear / Conv weights instead of fp32
        # plus autocast's cached bf16 copy (same values). Kept fp32: the text encoder (moved to the CPU after one
        # call), the DETR decoder FFNs, which TransformerDecoderLayer.forward_ffn runs with autocast disabled, and the
        # SAM mask decoder (small): torch.compile fuses autocast's casts into its kernels, so bf16 weights there give a
        # different compiled graph (eager: bit-identical either way).
        from sam3.model.decoder import TransformerDecoderLayer
        ffn = [f"{n}.{c}" for n, m in model.named_modules() if isinstance(m, TransformerDecoderLayer)
               for c in ("linear1", "linear2")]
        bf16_weights(model, skip=("backbone.language_backbone", "inst_interactive_predictor.model.sam_mask_decoder",
                                  *ffn))
        torch.cuda.empty_cache()
    # Keep POINT_BATCH and all arithmetic unchanged; broadcast the shared
    # image/position inputs instead of copying them for each point.
    model.inst_interactive_predictor.model.sam_mask_decoder.expand_image_embeddings = True
    dec = model.inst_interactive_predictor.model.sam_mask_decoder
    dec.predict_masks = types.MethodType(_predict_masks_shared, dec)   # same values, image side once
    if compile_decoder:   # fixed shapes: every batch is padded to POINT_BATCH points (_prompt_points)
        dec.forward = torch.compile(dec.forward, dynamic=False)
        # Inductor hashes torch's own sources once per process (~9 s) on the first compile; pure CPU, done here
        from torch._inductor.codecache import torch_key
        torch_key()
    return model, Sam3Processor(model, confidence_threshold=0.3), dec


class _SAM3Loader(threading.Thread):
    """Builds SAM3 (checkpoint read, GPU upload, bf16 cast) in the background while MoGe runs on the main thread.

    Costs VRAM (MoGe and SAM3 resident together: peak ~+3 GB) for time (the build was 20-50 s of serial start-up).
    Outputs stay bit-identical because the process-wide state the sequential order gave each stage is kept:
      - TF32: sam3.model_builder switches it on at import. It is imported on the main thread before MoGe with the
        flags restored right after, so MoGe still runs without TF32; enable_sam3_globals() switches it on after MoGe,
        where the import used to happen.
      - autocast: Sam3TrackingPredictor enters a bf16 autocast it never exits. Autocast state is per thread, so the
        builder thread's context does not reach the main thread; enable_sam3_globals() enters the same context there
        (it stays open for the rest of the process, as before)."""

    def __init__(self, compile_decoder):
        import torch
        super().__init__(daemon=True)
        sys.path.insert(0, f"{REPO}/models/sam3")
        tf = torch.backends.cuda.matmul.allow_tf32, torch.backends.cudnn.allow_tf32
        import sam3.model_builder  # noqa: F401  (import-time TF32 switch, undone below until enable_sam3_globals)
        torch.backends.cuda.matmul.allow_tf32, torch.backends.cudnn.allow_tf32 = tf
        self.compile_decoder, self.built, self.err = compile_decoder, None, None
        self.start()

    def run(self):
        try:
            self.built = _sam3_build(self.compile_decoder)
        except BaseException as e:   # re-raised on the main thread by result()
            self.err = e

    def result(self):
        self.join()
        if self.err is not None:
            raise self.err
        built, self.built = self.built, None
        return built


def enable_sam3_globals():
    """The process-wide state that building SAM3 on the main thread used to leave behind (see _SAM3Loader)."""
    import torch
    from sam3.model_builder import _setup_tf32
    _setup_tf32()
    torch.autocast(device_type="cuda", dtype=torch.bfloat16).__enter__()


def _sam3_stage(run, cache, sils, points, compile_decoder, mode, t0, built=None):
    """SAM3 point-prompt candidates and person masks -> cands.npz, person.npz. built: a list holding _sam3_build()'s
    result (from _SAM3Loader; the caller has called enable_sam3_globals), emptied here so the model is freed at the
    end; else the model is built here."""
    import torch
    T, H, W = len(run), run.H, run.W
    from PIL import Image
    model, proc, dec = built.pop() if built else _sam3_build(compile_decoder)
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
    # The 'person' grounding is launch-bound: ~50 ms/frame of Python and small kernels with the GPU mostly idle
    # (golf 120 f: 52 ms call, 0.1 ms left to wait after it), while the point prompts keep the GPU busy (5 ms to
    # issue a 128-point batch, 62 ms on the GPU). So grounding runs in a worker thread on its own stream, issued
    # while the main thread's prompt batches run. Same kernels on the same inputs: outputs are bit-identical.
    # Moving it before the prompts is equivalent: reset_all_prompts only deletes grounding keys, which a fresh
    # set_image state does not have yet, and the point prompts neither read nor write them.
    g_stream = torch.cuda.Stream() if ASYNC_GROUNDING else None
    g_pool = None
    if ASYNC_GROUNDING:
        from concurrent.futures import ThreadPoolExecutor
        g_pool = ThreadPoolExecutor(1)

    def ground_async(st):
        with torch.cuda.stream(g_stream), torch.autocast("cuda", dtype=torch.bfloat16):   # autocast is per thread
            r = person_prompt(st)
            out = r["masks"][:, 0].cpu().numpy().astype(bool), r["scores"].float().cpu().numpy()
        g_stream.synchronize()   # everything it allocated on g_stream is finished before the main thread frees it
        return out

    # the kept candidates' packed rows go to a file as they come (np.stack of the list below reads them back as one
    # array: same bytes, no whole-video copy in RAM; 4.6 GB for a 5-minute clip)
    rows_path = f"{cache}/_cands_rows.bin"
    rows_f, n_rows = open(rows_path, "wb"), 0
    frame_idx, scores, inV = [], [], []
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
            fut = None
            if g_pool is not None:
                g_stream.wait_stream(torch.cuda.current_stream())   # the image features are ready
                fut = g_pool.submit(ground_async, st)
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
            if fut is not None:
                r = None
                gm, gs = fut.result()
            else:
                proc.reset_all_prompts(st)
                r = person_prompt(st)
                gm, gs = r["masks"][:, 0].cpu().numpy().astype(bool), r["scores"].float().cpu().numpy()
            for mk, sc in zip(gm, gs):
                pm.append(np.packbits(mk.reshape(-1)))
                pf.append(i)
                ps.append(float(sc))
        order = sorted(range(len(keep)), key=lambda j: -keep[j][0])   # = frozen keep.sort (stable)
        keep = [keep[j] for j in order]
        half = [half[j] for j in order]
        sel = _dedup_gpu(keep, half)
        for j in sel:
            rows_f.write(np.packbits(keep[j][1].reshape(-1)).tobytes())
            n_rows += 1
            frame_idx.append(i)
            scores.append(keep[j][0])
            inV.append(keep[j][2])
        if i % 10 == 0:
            print(run.name, i, "pts", len(pts), "raw", len(keep), "kept", len(sel),
                  round(time.time() - t0, 1), flush=True)
        # r aliases st, including this image's backbone features. Both
        # must be released before set_image allocates the next state.
        # All retained candidates/person masks above are CPU arrays.
        del keep, half, st, r
    nb = (H * W + 7) // 8
    rows_f.close()
    allm = np.memmap(rows_path, np.uint8, "r", shape=(n_rows, nb)) if n_rows else np.zeros((0, nb), np.uint8)
    np.savez_compressed(f"{cache}/cands.npz", masks=allm, frame=np.array(frame_idx),
                        score=np.array(scores), inV=np.array(inV), shape=np.array([H, W]), mode=np.array(mode))
    del allm
    disk_arrays.release(rows_path)
    np.savez_compressed(f"{cache}/person.npz",
                        masks=np.stack(pm) if pm else np.zeros((0, (H * W + 7) // 8), np.uint8),
                        frame=np.array(pf, int), score=np.array(ps), shape=np.array([H, W]))
    if g_pool is not None:
        g_pool.shutdown()
    del model, proc, dec, text_person
    # The bound predict_masks method forms a decoder self-cycle. Also
    # release BF16 weight copies held by SAM3's outer autocast context
    # before the following CPU/avatar stages. No per-frame cache flush.
    gc.collect()
    torch.clear_autocast_cache()
    torch.cuda.empty_cache()


def _save_moge(path, Z, fov_x, level):
    """np.savez_compressed(path, ...) through a temporary name, so an interrupted save never looks like a cache."""
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        np.savez_compressed(f, Z=Z, fov_x=fov_x, masked=True, level=level)
    os.replace(tmp, path)


@contextlib.contextmanager
def _vram_cap(margin=0.5 * 2**30):
    """Cap PyTorch's CUDA memory at what the GPU physically has free for this process.

    Each prompt batch has its own size, so the caching allocator keeps reserving new segments (example1, 854x480:
    6.2 GB allocated, >14 GB reserved on a 12 GB card). Windows' driver backs the excess with system memory, and every
    decoder batch that lands there runs 5-10x slower (a 88-point batch: 90 ms -> 0.6-1 s). Under the cap the
    allocator frees its unused cached blocks and retries instead. Where memory is allocated does not change what is
    computed: outputs are bit-identical (check_fusion_fast.py)."""
    import torch
    free, total = torch.cuda.mem_get_info()
    frac = (free + torch.cuda.memory_reserved() - margin) / total
    torch.cuda.set_per_process_memory_fraction(min(1.0, max(frac, 0.1)))
    try:
        yield
    finally:
        torch.cuda.set_per_process_memory_fraction(1.0)


def run_gpu(run, cache, owner="R3_fusion", points="full", compile_decoder=False):
    """points: "full" = the frozen point grid (bit-identical to gpu_stage); "c2f" = coarse-to-fine (see
    _c2f_points; not bit-identical, same F1 pass count on dev 18 and held-out test4, LEDGER 2026-10-14).
    compile_decoder: torch.compile the SAM mask decoder (not bit-identical; ~0.03% of mask pixels flip)."""
    import torch
    assert points in ("full", "c2f")
    os.makedirs(cache, exist_ok=True)
    T, H, W = len(run), run.H, run.W
    mode = _cands_mode(points, compile_decoder)
    # np.load keeps the file open until closed, and Windows cannot rename an open file: read, close, then replace
    masked, level = True, MOGE_LEVEL
    if os.path.exists(f"{cache}/moge.npz"):
        with np.load(f"{cache}/moge.npz") as z:
            masked = "masked" in z.files
            level = int(z["level"]) if "level" in z.files else 9        # caches before the level key: level 9
    if not masked:
        os.replace(f"{cache}/moge.npz", f"{cache}/moge_unmasked.npz")   # cache from an older version: recompute
    elif level != MOGE_LEVEL:                                            # other resolution level: recompute
        os.replace(f"{cache}/moge.npz", f"{cache}/moge_level{level}.npz")
        if os.path.exists(f"{cache}/evidence.pkl"):
            os.replace(f"{cache}/evidence.pkl", f"{cache}/evidence_level{level}.pkl")
    if os.path.exists(f"{cache}/cands.npz"):
        with np.load(f"{cache}/cands.npz") as c:
            old = str(c["mode"]) if "mode" in c.files else "full"        # caches before the modes: full grid
        if old != mode:                                                  # other mode: recompute candidates
            os.replace(f"{cache}/cands.npz", f"{cache}/cands_{old.replace('+', '_')}.npz")
            if os.path.exists(f"{cache}/evidence.pkl"):
                os.remove(f"{cache}/evidence.pkl")
    if os.path.exists(f"{cache}/moge.npz") and os.path.exists(f"{cache}/cands.npz"):
        return  # cached
    D = disk_arrays.npz_member(f"{cache}/mesh.npz", "D")                 # prep's mesh depth (T,P,H,W), memory-mapped
    sils = [(D[i] > 0).any(0) for i in range(T)]
    del D
    t0 = time.time()
    saver = None
    with local_gpu(owner):
        need_moge = not os.path.exists(f"{cache}/moge.npz")
        loader = None
        if need_moge and not os.path.exists(f"{cache}/cands.npz") and PARALLEL_LOAD:
            loader = _SAM3Loader(compile_decoder)   # builds SAM3 while MoGe runs
        if need_moge:
            from moge.model.v2 import MoGeModel
            with skip_random_init():
                moge = MoGeModel.from_pretrained(ckpt("moge-2-vitl-normal/model.pt")).cuda().eval()
            # Only points and mask are used. MoGe's forward skips a head the model lacks, and the normal head reads
            # the shared neck features without writing to them: the other outputs are unchanged (~10% of a frame).
            del moge.normal_head
            f = next(run.focal(p, i) for i in range(T) for p in run.pids if run.focal(p, i))
            fov_x = math.degrees(2 * math.atan(W / 2 / f))
            Z = disk_arrays.scratch(f"{cache}/_moge_Z.npy", (T, H, W), np.float32)   # np.zeros, on disk
            for i in range(T):
                rgb = cv2.cvtColor(run.image(i), cv2.COLOR_BGR2RGB)
                t = torch.tensor(rgb / 255.0, dtype=torch.float32, device="cuda").permute(2, 0, 1)
                with torch.no_grad():
                    o = moge.infer(t, fov_x=fov_x, apply_mask=False, resolution_level=MOGE_LEVEL)
                z = o["points"][..., 2].float().cpu().numpy()
                z[~np.isfinite(z)] = 0
                z[~o["mask"].cpu().numpy().astype(bool)] = 0
                Z[i] = z
                # Z now owns the CPU result; the input and other GPU outputs
                # must not survive into the next image or the SAM3 stage.
                del o, t
            # The same file, compressed in a thread while SAM3 runs (4.4 s per 100 frames at 854x480); joined below.
            saver = threading.Thread(target=_save_moge, args=(f"{cache}/moge.npz", Z, fov_x, MOGE_LEVEL))
            saver.start()
            del moge
            torch.cuda.empty_cache()
            print(run.name, "moge", round(time.time() - t0, 1), flush=True)
        if not os.path.exists(f"{cache}/cands.npz"):
            over = False
            built = []
            if loader is not None:
                built.append(loader.result())
                del loader
                enable_sam3_globals()
                print(run.name, "sam3 ready", round(time.time() - t0, 1), flush=True)
            try:
                with _vram_cap():
                    _sam3_stage(run, cache, sils, points, compile_decoder, mode, t0, built)
            except torch.cuda.OutOfMemoryError:
                over = True
            built.clear()   # normally emptied by _sam3_stage already
            if over:   # outside the except: its traceback no longer holds the failed attempt's tensors
                # the cap only makes the allocator release its cache sooner; a frame that needs more than the cap
                # runs uncapped (as before: slower, through the driver's system-memory fallback, same results)
                gc.collect()
                torch.clear_autocast_cache()
                torch.cuda.empty_cache()
                print(run.name, "SAM3 stage over the VRAM cap: rerun without it", flush=True)
                _sam3_stage(run, cache, sils, points, compile_decoder, mode, t0)
        if saver is not None:
            saver.join()
            saver = Z = None
            disk_arrays.release(f"{cache}/_moge_Z.npy")
    print(run.name, "gpu stage", round(time.time() - t0, 1), "s", flush=True)
