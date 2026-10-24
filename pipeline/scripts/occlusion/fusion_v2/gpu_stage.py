"""R3_fusion GPU stage (local GPU, under the shared lock): per frame
  1. MoGe-2 metric depth with the pipeline's own FoV (so pixel rays match the meshes)      -> cache/<run>/moge.npz
  2. class-agnostic SAM3 object candidates from single positive point prompts on a grid that covers every projected
     mesh (dilated), INCLUDING the people's visible masks (SAM3's visible person mask can contain the occluder, e.g. a
     guitar or a plate), 3 multimask hypotheses per point, filtered by predicted IoU and stability, de-duplicated  -> cands.npz
  3. SAM3 concept detection "person" (fresh per frame, no tracker memory) - a second opinion on the visible masks -> person.npz
No category list for objects. Reused/adapted from C_object_layers/scripts/seg_sam3.py and F_3d_proxies/stage1_gpu.py.
"""
import math
import os
import sys
import time

import cv2
import numpy as np

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ME)
from r3common import Run, local_gpu, REPO  # noqa: E402

STRIDE_IN, STRIDE_V, STRIDE_OUT, DIL = 4, 8, 8, 12
IOU_T, STAB_T, DEDUP, MIN_PX = 0.30, 0.50, 0.80, 30


def grid_points(region, stride, off=0):
    H, W = region.shape
    ys, xs = np.mgrid[stride // 2 + off:H:stride, stride // 2 + off:W:stride]
    sel = region[ys, xs]
    return np.stack([xs[sel], ys[sel]], 1).astype(np.float32)


def dedup(keep):
    """greedy de-duplication by mask IoU (on 2x-downsampled masks, vectorised)."""
    sel, flat = [], []
    for sc, mk, sv in keep:
        f = mk[::2, ::2].reshape(-1).astype(np.float32)
        if flat:
            F = np.stack(flat)
            inter = F @ f
            iou = inter / (F.sum(1) + f.sum() - inter + 1e-6)
            if iou.max() > DEDUP:
                continue
        sel.append((sc, mk, sv))
        flat.append(f)
    return sel


def mesh_sils(run, i):
    sil = np.zeros((run.H, run.W), bool)
    for p in run.pids:
        sil |= run.mesh_depth(p, i) > 0
    return sil


def run_gpu(run, cache, owner="R3_fusion"):
    import torch
    os.makedirs(cache, exist_ok=True)
    T, H, W = len(run), run.H, run.W
    if os.path.exists(f"{cache}/moge.npz") and "masked" not in np.load(f"{cache}/moge.npz").files:
        os.replace(f"{cache}/moge.npz", f"{cache}/moge_unmasked.npz")   # cache from an older version: recompute
    if os.path.exists(f"{cache}/moge.npz") and os.path.exists(f"{cache}/cands.npz"):
        return  # cached: do not take the GPU lock
    sils = [mesh_sils(run, i) for i in range(T)]  # CPU, before taking the lock
    t0 = time.time()
    with local_gpu(owner):
        if not os.path.exists(f"{cache}/moge.npz"):
            from moge.model.v2 import MoGeModel
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
                z[~o["mask"].cpu().numpy().astype(bool)] = 0   # MoGe validity mask: sky / infinitely far -> invalid
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
            model = build_sam3_image_model(checkpoint_path="C:/VScodeFiles/checkpoints/sam3/sam3.pt",
                                           load_from_HF=False, enable_inst_interactivity=True)
            proc = Sam3Processor(model, confidence_threshold=0.3)
            allm, frame_idx, scores, inV = [], [], [], []
            pm, pf, ps = [], [], []
            for i in range(T):
                img = cv2.cvtColor(run.image(i), cv2.COLOR_BGR2RGB)
                lab = run.mask(i)
                sil = sils[i]
                region = cv2.dilate(sil.astype(np.uint8), np.ones((2 * DIL + 1,) * 2, np.uint8)) > 0
                V = lab > 0
                Vd = cv2.dilate(V.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
                inner = sil & ~Vd                      # mesh drawn, nobody visible: occluder or misfit
                inv = sil & V & ~(cv2.dilate((~V).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
                pts_in = grid_points(inner, STRIDE_IN)
                pts_v = grid_points(inv, STRIDE_V, off=2)
                pts_o = grid_points(region & ~sil & ~Vd, STRIDE_OUT)
                pts = np.concatenate([pts_in, pts_o, pts_v])
                src = np.concatenate([np.zeros(len(pts_in) + len(pts_o), bool), np.ones(len(pts_v), bool)])
                keep = []
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    st = proc.set_image(Image.fromarray(img))
                    for s in range(0, len(pts), 128) if len(pts) else []:
                        c = pts[s:s + 128]
                        m, iou, _ = model.predict_inst(st, point_coords=c[:, None, :],
                                                       point_labels=np.ones((len(c), 1)),
                                                       multimask_output=True, return_logits=True)
                        m = m.reshape(-1, H, W)
                        iou = iou.reshape(-1)
                        sv = np.repeat(src[s:s + 128], 3)
                        hi = (m > 1.0).sum((1, 2))
                        lo = (m > -1.0).sum((1, 2))
                        stab = hi / np.maximum(lo, 1)
                        area = (m > 0).sum((1, 2))
                        ok = (iou >= IOU_T) & (stab >= STAB_T) & (area >= MIN_PX) & (area <= 0.5 * H * W)
                        for k in np.nonzero(ok)[0]:
                            keep.append((float(iou[k] * stab[k]), m[k] > 0, bool(sv[k])))
                    # concept detection of people (second opinion on visible masks)
                    proc.reset_all_prompts(st)
                    r = proc.set_text_prompt("person", st)
                    for mk, sc in zip(r["masks"][:, 0].cpu().numpy().astype(bool), r["scores"].float().cpu().numpy()):
                        pm.append(np.packbits(mk.reshape(-1)))
                        pf.append(i)
                        ps.append(float(sc))
                keep.sort(key=lambda x: -x[0])
                sel = dedup(keep)
                for sc, mk, sv in sel:
                    allm.append(np.packbits(mk.reshape(-1)))
                    frame_idx.append(i)
                    scores.append(sc)
                    inV.append(sv)
                if i % 10 == 0:
                    print(run.name, i, "pts", len(pts), "raw", len(keep), "kept", len(sel),
                          round(time.time() - t0, 1), flush=True)
            nb = (H * W + 7) // 8
            np.savez_compressed(f"{cache}/cands.npz", masks=np.stack(allm) if allm else np.zeros((0, nb), np.uint8), frame=np.array(frame_idx),
                                score=np.array(scores), inV=np.array(inV), shape=np.array([H, W]))
            np.savez_compressed(f"{cache}/person.npz",
                                masks=np.stack(pm) if pm else np.zeros((0, (H * W + 7) // 8), np.uint8),
                                frame=np.array(pf, int), score=np.array(ps), shape=np.array([H, W]))
            del model, proc
            torch.cuda.empty_cache()
    print(run.name, "gpu stage", round(time.time() - t0, 1), "s", flush=True)


def load_cands(cache):
    c = np.load(f"{cache}/cands.npz")
    H, W = c["shape"]
    masks = np.unpackbits(c["masks"], axis=1)[:, :H * W].reshape(-1, H, W).astype(bool)
    return masks, c["frame"], c["score"], c["inV"]


if __name__ == "__main__":
    for name in sys.argv[1:]:
        r = Run(name)
        run_gpu(r, f"{ME}/cache/{name}")
