"""R3_fusion v2 occlusion on a pipeline output directory (the frozen pipeline of run_frozen.py, minus the study's
run lookup by name).

usage (repo root):
  .venv/Scripts/python.exe scripts/occlusion/run_fusion_v2.py --pipeline_out <dir>/pipeline_out --out <dir>/occlusion
writes <out>/hide/<name>/<pid>/<stem>.png (255 = a real surface is in front of the person's mesh) and caches the
intermediates in <out>/_r3cache/<name>/ (mesh depth, MoGe-2 depth, SAM3 candidates, evidence).

Stages: prep (CPU) -> gpu_stage (MoGe-2 depth, class-agnostic SAM3 candidates, SAM3 'person') -> evidence (CPU)
-> fusion decision + rendering (CPU). All parameters: fusion.P (frozen defaults).

The GPU stage and the evidence run through gpu_stage_fast / evidence_fast, which produce bit-identical caches and
evidence with less time and VRAM (see their docstrings), and shared_cache memoizes the cache loads and ground masks that
the CPU stages repeat; --reference runs the frozen gpu_stage / evidence instead.
By default the SAM3 point grid runs on keyframes only and frames in between make no SAM3 call: the keyframe's
candidates and its 'person' refinement of the visible masks are carried by optical flow (--points kf_lite,
fusion_v2/keyframes.py; 3 clips of testdata/2026-10-18 on an RTX 5060 Ti: fusion 119 -> 41 s, 88 -> 38 s, 527 -> 226 s,
clip hide IoU vs c2f 0.993 / 0.969 / 0.782 (the last on ~45 hidden px per 1080x1920 frame); downtown_bar avatar
154 -> 77 s). --points kf_flow carries the candidates but still runs set_image and the grounding on every frame
(fusion GPU stage -28..-54% vs c2f on 4 occluded 3DPW clips on an L4, testdata/2026-10-23). --points c2f prompts every
frame coarse-to-fine (~2.8x fewer decoder calls than the frozen grid, LEDGER 2026-10-14); --points full keeps the
frozen grid.
"""
import argparse
import os
import sys
import time

ME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ME, "fusion_v2"))

from r3common import Run  # noqa: E402
import prep  # noqa: E402
import prep_fast  # noqa: E402
import gpu_stage  # noqa: E402
import gpu_stage_fast  # noqa: E402
import evidence  # noqa: E402
import evidence_fast  # noqa: E402
import fusion  # noqa: E402
import shared_cache  # noqa: E402


def run_fusion(pipeline_out, out_dir, name=None, save=True, reference=False, points="kf_lite", compile_decoder=True,
               render=True):
    """-> dict(run, ev, dec, cache, hide={(i, pid): hide mask}). points / compile_decoder: gpu_stage_fast.run_gpu
    (ignored with reference=True). render=False skips fusion.render (hide=None; ~3 s per 120 frames): for callers
    that draw the decisions themselves (avatar_app's AvatarHider)."""
    gs, evm = (gpu_stage, evidence) if reference else (gpu_stage_fast, evidence_fast)
    if not reference:
        shared_cache.install()   # evidence / render / AvatarHider share cache loads and ground masks (same values)
    t0 = time.time()
    run = Run(pipeline_out, name)
    cache = os.path.join(out_dir, "_r3cache", run.name)
    os.makedirs(cache, exist_ok=True)
    (prep if reference else prep_fast).prep(run, cache)   # prep_fast: same mesh.npz, arrays on disk
    t1 = time.time()
    if reference:
        gs.run_gpu(run, cache, owner="fusion_v2")
    else:
        gs.run_gpu(run, cache, owner="fusion_v2", points=points, compile_decoder=compile_decoder)
    t2 = time.time()
    ev = evm.compute(run, cache)
    dec = fusion.decide(ev, fusion.P)
    hide = fusion.render(run, ev, cache, dec, out_dir, fusion.P, save=save) if render or save else None
    t3 = time.time()
    print(f"[fusion_v2] {run.name}: {len(run)} frames, pids {run.pids}; prep {t1 - t0:.0f}s, gpu {t2 - t1:.0f}s, "
          f"cpu {t3 - t2:.0f}s, total {t3 - t0:.0f}s", flush=True)
    return dict(run=run, ev=ev, dec=dec, cache=cache, hide=hide)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline_out", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--reference", action="store_true", help="run the frozen gpu_stage / evidence")
    ap.add_argument("--points", choices=["full", "c2f", "kf_flow", "kf_lite", "kf_sam3"], default="kf_lite",
                    help="SAM3 point prompts: kf_lite (default) = coarse-to-fine on keyframes, no SAM3 call in between "
                         "(candidates and the 'person' refinement carried by optical flow; fusion_v2/keyframes.py); "
                         "kf_flow = the same carrying with set_image and grounding still on every frame; c2f = "
                         "coarse-to-fine on every frame; full = the frozen grid; kf_sam3 = keyframes carried by the "
                         "SAM3 tracker (slower)")
    ap.add_argument("--no_compile_decoder", dest="compile_decoder", action="store_false",
                    help="eager SAM mask decoder (the default torch.compiles it; not bit-identical)")
    a = ap.parse_args()
    run_fusion(a.pipeline_out, a.out, a.name, reference=a.reference, points=a.points,
               compile_decoder=a.compile_decoder)
