"""Per-stage and per-model time of the production pipeline on one clip, with a snapshot of every stage's output.

Runs the same code as offline_app_lite.py (mesh) and avatar_app.py's occlusion step (fusion v2), each phase in a fresh
process like production (SAM3's builder leaves a process-wide bf16 autocast behind), with the model calls inside
timed by scripts/eval/stage_profiler.py:

  mesh    detect -> SAM3 tracker (image encoder / memory attention / mask decoder / memory encoder)
          -> SAM-3D-Body (backbone / body decoder / hand decoder, per pass)          stages from offline_app_lite.Stage
  fusion  prep -> MoGe-2 -> SAM3 image model (image encoder / point-prompt decoder / dedup / 'person' grounding)
          -> evidence -> decision -> render                                          (async grounding off: serial)
  snap    snapshots/<stem>.png for the frames with the most hidden mesh pixels (and the middle frame): input,
          SAM3 tracking masks, SAM-3D-Body meshes, MoGe-2 depth, SAM3 point prompts + kept candidates, 'person'
          grounding, fusion's hide masks; each panel titled with its stage's time per frame
writes <out>/profile/{mesh,fusion}.json, <out>/profile/points.npz, <out>/snapshots/*.png, <out>/profile/summary.json

usage (repo root):
  python scripts/eval/profile_stages.py --input_video <frames dir | .mp4> --out <dir> [--phase all|mesh|fusion|snap]
"""
import argparse
import json
import os
import subprocess
import sys
import time

# diffusers (imported by offline_app) pulls in TensorFlow / JAX where they are installed (Colab), and pyrender's EGL
# context creation then segfaults. Nothing here uses them.
for _k in ("USE_TF", "USE_JAX", "USE_FLAX"):
    os.environ.setdefault(_k, "0")
if sys.platform.startswith("linux"):
    os.environ.setdefault("PYOPENGL_PLATFORM", "egl")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(REPO, "scripts")
FUSION = os.path.join(SCRIPTS, "occlusion", "fusion_v2")


def gpu_info():
    import torch
    p = torch.cuda.get_device_properties(0)
    return dict(gpu=p.name, vram_GB=round(p.total_memory / 2**30, 1), torch=torch.__version__)


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1)


def phase_mesh(a):
    sys.path[:0] = [SCRIPTS, HERE]
    import offline_app_lite as lite
    import stage_profiler as sp
    from utils.compile_cache import use_persistent_compile_cache
    oa = lite.oa

    build = lite.build_sam3_tracker

    def build_timed(path):
        tr = build(path)
        cls = type(tr)
        sp.patch(cls, "forward_image", "sam3_track/image_encoder")
        sp.patch(cls, "_prepare_memory_conditioned_features", "sam3_track/memory_attention")
        sp.patch(cls, "_forward_sam_heads", "sam3_track/mask_decoder")
        sp.patch(cls, "_encode_new_memory", "sam3_track/memory_encoder")
        sp.patch(cls, "_run_single_frame_inference", "sam3_track/frame_total")
        return tr
    lite.build_sam3_tracker = build_timed

    load = oa.load_sam_3d_body

    def load_timed(*args, **kw):
        model, cfg = load(*args, **kw)
        sp.patch(model.backbone, "forward", "hmr/backbone",
                 key=lambda ar, k: f"{tuple(ar[0].shape[-2:])}" if ar and hasattr(ar[0], "shape") else "?")
        sp.patch(model, "forward_decoder", "hmr/decoder_body")
        sp.patch(model, "forward_decoder_hand", "hmr/decoder_hand")
        sp.patch(model, "forward_step", "hmr/forward_step",
                 key=lambda ar, k: k.get("decoder_type", ar[1] if len(ar) > 1 else "body"))
        return model, cfg
    oa.load_sam_3d_body = load_timed
    sp.patch(oa.SAM3DBodyEstimator, "process_frames", "hmr/process_frames")

    use_persistent_compile_cache()
    la = argparse.Namespace(input_video=a.input_video, output_dir=a.pout, offload_state=False, capture=None,
                            save_workers=2, hmr_input_size=384, compile_backbone=False, keep_rejected_hands=False,
                            autocast_cache=False, resident_backbone=False, preload_frames=False,
                            keep_tracker_memory=False)
    t0 = time.perf_counter()
    lite.inference_lite(la)
    total = time.perf_counter() - t0
    import glob
    n = len(glob.glob(f"{a.pout}/images/*.jpg"))
    pids = sorted(os.listdir(f"{a.pout}/mesh_4d_individual")) if os.path.isdir(f"{a.pout}/mesh_4d_individual") else []
    save(f"{a.prof}/mesh.json", dict(total_s=total, frames=n, persons=len(pids), stages=lite.CAPTURE["stages"],
                                     models=sp.records(), **gpu_info()))


def phase_fusion(a):
    os.environ["FUSION_ASYNC_GROUNDING"] = "0"   # serial, so grounding has its own time
    sys.path[:0] = [FUSION, os.path.join(REPO, "models", "sam3"), HERE, REPO]
    import numpy as np
    import torch
    import stage_profiler as sp
    from utils.compile_cache import use_persistent_compile_cache
    import prep_fast
    import gpu_stage_fast as gs
    import evidence_fast
    import fusion
    import shared_cache
    from r3common import Run
    tf = torch.backends.cuda.matmul.allow_tf32, torch.backends.cudnn.allow_tf32
    from sam3.model.sam3_image_processor import Sam3Processor   # for the class patches (no TF32 side effect kept)
    torch.backends.cuda.matmul.allow_tf32, torch.backends.cudnn.allow_tf32 = tf
    from moge.model.v2 import MoGeModel

    sp.patch(MoGeModel, "infer", "fusion_moge/infer")
    sp.patch(Sam3Processor, "set_image", "fusion_sam3/image_encoder")
    sp.patch(Sam3Processor, "_forward_grounding", "fusion_sam3/person_grounding")
    sp.patch(gs, "_predict_gpu", "fusion_sam3/point_decoder")
    sp.patch(gs, "_dedup_gpu", "fusion_sam3/dedup")
    sp.patch(gs, "_sam3_build", "fusion_sam3/build")
    import keyframes
    sp.patch(keyframes._Flow, "step", "fusion_kf/carry_flow")
    sp.patch(keyframes._SAM3, "step", "fusion_kf/carry_sam3")
    sp.patch(keyframes._SAM3, "start", "fusion_kf/start_sam3")
    sp.patch(keyframes, "relevant", "fusion_kf/select")

    frame = [-1]
    points = {}   # frame -> {"coarse"/"fine"/"full": (n, 2)}
    set_image = Sam3Processor.set_image

    def set_image_count(self, *ar, **k):
        frame[0] += 1
        return set_image(self, *ar, **k)
    Sam3Processor.set_image = set_image_count
    prompt = gs._prompt_points

    def prompt_rec(model, st, pts, src, H, W, keep, half, cover=None, pad=False):
        kind = "coarse" if cover is not None else ("fine" if frame[0] in points else "full")
        points.setdefault(frame[0], {})[kind if kind != "full" or a.points == "full" else "fine"] = np.asarray(pts)
        return prompt(model, st, pts, src, H, W, keep, half, cover=cover, pad=pad)
    gs._prompt_points = prompt_rec
    sp.patch(gs, "_prompt_points", "fusion_sam3/prompt_batches")

    use_persistent_compile_cache()
    shared_cache.install()
    run = Run(a.pout, a.name)
    cache = os.path.join(a.occ, "_r3cache", run.name)
    os.makedirs(cache, exist_ok=True)
    st = {}
    t0 = time.perf_counter()
    with sp.Stage(st, "prep"):
        prep_fast.prep(run, cache)
    with sp.Stage(st, "gpu (moge + sam3)"):
        gs.run_gpu(run, cache, owner="fusion_v2", points=a.points, compile_decoder=True)
    with sp.Stage(st, "evidence"):
        ev = evidence_fast.compute(run, cache)
    with sp.Stage(st, "decide"):
        dec = fusion.decide(ev, fusion.P)
    with sp.Stage(st, "render"):
        fusion.render(run, ev, cache, dec, a.occ, fusion.P, save=True)
    total = time.perf_counter() - t0
    np.savez_compressed(f"{a.prof}/points.npz", **{f"{i}_{k}": v for i, d in points.items() for k, v in d.items()})
    npts = [sum(len(v) for v in d.values()) for d in points.values()]
    with np.load(f"{cache}/cands.npz") as cz:
        kfs = cz["keyframes"].tolist() if "keyframes" in cz.files else []
    save(f"{a.prof}/fusion.json", dict(total_s=total, frames=len(run), persons=len(run.pids), stages=st,
                                       models=sp.records(), points_mode=a.points, keyframes=kfs,
                                       points_per_frame_mean=float(np.sum(npts) / len(run)) if npts else 0,
                                       points_per_frame_max=int(max(npts)) if npts else 0, **gpu_info()))


def per_frame_ms(js, n):
    """ms per frame of each timed call, steady state (the first call's one-time work replaced by the median call),
    and the same summed per group/name prefix (e.g. hmr/backbone over its crop sizes)."""
    out = {}
    for k, v in js["models"].items():
        ms = 1000 * v.get("steady_s", v["time_s"]) / max(n, 1)
        out[k] = ms
        parts = k.split("/")
        for j in range(2, len(parts)):
            p = "/".join(parts[:j])
            if p not in js["models"]:
                out[p] = out.get(p, 0.0) + ms
    return out


def phase_snap(a):
    import glob
    import cv2
    import numpy as np
    sys.path[:0] = [FUSION, REPO]
    from r3common import Run
    from utils import DAVIS_PALETTE
    run = Run(a.pout, a.name)
    mesh = json.load(open(f"{a.prof}/mesh.json"))
    fus = json.load(open(f"{a.prof}/fusion.json"))
    n = len(run)
    ms = {**per_frame_ms(mesh, n), **per_frame_ms(fus, n)}
    hdir = f"{a.occ}/hide/{run.name}"
    hidden = np.zeros(n)
    for i, s in enumerate(run.stems):
        for p in run.pids:
            f = f"{hdir}/{p}/{s}.png"
            if os.path.exists(f):
                hidden[i] += (cv2.imread(f, 0) > 127).sum()
    order = [int(i) for i in np.argsort(-hidden) if hidden[i] > 0]
    pick = []
    for i in order:   # the most-hidden frames, at least a second apart
        if all(abs(i - j) >= 30 for j in pick):
            pick.append(i)
        if len(pick) == a.snapshots - 1:
            break
    pick = sorted(set(pick + [n // 2]))
    cz = np.load(f"{a.occ}/_r3cache/{run.name}/cands.npz")
    H, W = int(cz["shape"][0]), int(cz["shape"][1])
    pz = np.load(f"{a.occ}/_r3cache/{run.name}/person.npz")
    Z = np.load(f"{a.occ}/_r3cache/{run.name}/moge.npz")["Z"]
    pts = np.load(f"{a.prof}/points.npz")
    rng = np.random.default_rng(0)
    pal = np.frombuffer(DAVIS_PALETTE, np.uint8).reshape(-1, 3)[:, ::-1].astype(np.float32)

    def title(img, text, sub=""):
        h = 44
        bar = np.full((h, img.shape[1], 3), 255, np.uint8)
        cv2.putText(bar, text, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(bar, sub, (6, 37), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 60, 200), 1, cv2.LINE_AA)
        return np.vstack([bar, img])

    def unpack(rows):
        return np.unpackbits(rows, axis=1)[:, :H * W].reshape(-1, H, W).astype(bool)

    def fmt(*keys):
        return " + ".join(f"{k.split('/')[-1]} {ms.get(k, 0):.0f}" for k in keys) + " ms/frame"
    os.makedirs(f"{a.out}/snapshots", exist_ok=True)
    for i in pick:
        s = run.stems[i]
        img = run.image(i)
        lab = run.mask(i)
        trk = img.copy()
        for p in map(int, np.unique(lab)[1:]):
            trk[lab == p] = (0.5 * trk[lab == p] + 0.5 * pal[p % len(pal)]).astype(np.uint8)
        rend = cv2.imread(f"{a.pout}/rendered_frames/{s}.jpg")   # meshes on white
        if rend is not None:
            rend = cv2.resize(rend, (W, H))
            body = (rend.astype(int).sum(2) < 3 * 245)
            rend = img.copy()
            rend[body] = (0.3 * img[body] + 0.7 * cv2.resize(cv2.imread(f"{a.pout}/rendered_frames/{s}.jpg"),
                                                             (W, H))[body]).astype(np.uint8)
        else:
            rend = np.zeros_like(img)
        z = Z[i]
        v = z > 0
        zc = np.zeros_like(img)
        if v.any():
            lo, hi = np.percentile(z[v], [2, 98])
            zc = cv2.applyColorMap((255 * np.clip((z - lo) / max(hi - lo, 1e-6), 0, 1)).astype(np.uint8),
                                   cv2.COLORMAP_TURBO)
            zc[~v] = 0
        cand = (img * 0.45).astype(np.uint8)
        sel = cz["frame"] == i
        for m in unpack(cz["masks"][sel]) if sel.any() else []:
            c = rng.integers(60, 255, 3)
            cand[m] = (0.4 * cand[m] + 0.6 * c).astype(np.uint8)
        npt = 0
        for kind, col in (("coarse", (0, 0, 255)), ("fine", (0, 255, 255)), ("full", (0, 255, 255))):
            k = f"{i}_{kind}"
            if k in pts.files:
                npt += len(pts[k])
                for x, y in pts[k].astype(int):
                    cv2.circle(cand, (int(x), int(y)), 1, col, -1)
        per = img.copy()
        psel = pz["frame"] == i
        for m in unpack(pz["masks"][psel]) if psel.any() else []:
            per[m] = (0.5 * per[m] + np.array([0, 160, 0])).astype(np.uint8)
        hid = img.copy()
        for p in run.pids:
            f = f"{hdir}/{p}/{s}.png"
            if os.path.exists(f):
                hm = cv2.imread(f, 0) > 127
                hid[hm] = (0.4 * hid[hm] + np.array([0, 0, 150])).astype(np.uint8)
        panels = [
            title(img, f"input #{i}", f"{W}x{H}"),
            title(trk, "SAM3 tracker (mesh stage)",
                  fmt("sam3_track/image_encoder", "sam3_track/memory_attention", "sam3_track/mask_decoder")),
            title(rend, "SAM-3D-Body meshes", fmt("hmr/backbone", "hmr/decoder_body", "hmr/decoder_hand")),
            title(zc, "MoGe-2 depth (fusion)", fmt("fusion_moge/infer")),
            title(cand, f"SAM3 points ({npt}; red coarse, yellow fine) + {int(sel.sum())} cands",
                  fmt("fusion_sam3/image_encoder", "fusion_sam3/point_decoder", "fusion_sam3/dedup")),
            title(per, "SAM3 'person' grounding (fusion)", fmt("fusion_sam3/person_grounding")),
            title(hid, f"fusion hide (red), {int(hidden[i])} px", ""),
        ]
        cols = 4 if W < H else 3
        rows = [panels[k:k + cols] for k in range(0, len(panels), cols)]
        ph, pw = panels[0].shape[:2]
        blank = np.full((ph, pw, 3), 255, np.uint8)
        grid = np.vstack([np.hstack(r + [blank] * (cols - len(r))) for r in rows])
        cv2.imwrite(f"{a.out}/snapshots/{s}.jpg", grid, [cv2.IMWRITE_JPEG_QUALITY, 88])
    summary = dict(clip=run.name, frames=n, persons=len(run.pids), size=[W, H], gpu=mesh["gpu"],
                   stages_s={**{f"mesh/{k}": v["time_s"] for k, v in mesh["stages"].items()},
                             **{f"fusion/{k}": v["time_s"] for k, v in fus["stages"].items()}},
                   models_ms_per_frame=ms, models=dict(mesh["models"], **fus["models"]),
                   peak_reserved_GB={**{f"mesh/{k}": v["peak_reserved_GB"] for k, v in mesh["stages"].items()},
                                     **{f"fusion/{k}": v["peak_reserved_GB"] for k, v in fus["stages"].items()}},
                   points_per_frame_mean=fus["points_per_frame_mean"], snapshot_frames=[run.stems[i] for i in pick],
                   hidden_px_per_frame=hidden.tolist(), total_s=mesh["total_s"] + fus["total_s"])
    save(f"{a.prof}/summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("clip", "frames", "gpu", "stages_s", "total_s")}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input_video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default=None, help="run name (default: basename of --out)")
    ap.add_argument("--phase", choices=["all", "mesh", "fusion", "snap"], default="all")
    ap.add_argument("--points", choices=["full", "c2f", "kf_flow", "kf_lite", "kf_sam3"], default="kf_lite")
    ap.add_argument("--snapshots", type=int, default=4)
    a = ap.parse_args()
    a.out = os.path.abspath(a.out)
    a.name = a.name or os.path.basename(a.out)
    a.pout, a.occ, a.prof = f"{a.out}/pipeline_out", f"{a.out}/occlusion", f"{a.out}/profile"
    if a.phase == "all":
        for ph in ("mesh", "fusion", "snap"):
            r = subprocess.call([sys.executable, "-u", __file__, "--phase", ph, "--input_video", a.input_video,
                                 "--out", a.out, "--name", a.name, "--points", a.points,
                                 "--snapshots", str(a.snapshots)])
            if r:
                sys.exit(r)
    else:
        {"mesh": phase_mesh, "fusion": phase_fusion, "snap": phase_snap}[a.phase](a)
