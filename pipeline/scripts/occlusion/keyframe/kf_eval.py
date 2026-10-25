"""Run the production fusion v2 (base) or the keyframe-tracked candidates (kf) on pipeline outputs, with timings.

usage: python kf_eval.py base|kf <out_root> <pipeline_out> [<pipeline_out> ...]
  base: run_fusion_v2.run_fusion as avatar_app calls it (c2f + compiled decoder, persistent compile cache);
        writes <out_root>/base/{_r3cache,hide}/<clip> and base_times.json
  kf:   same mesh.npz / moge.npz / person.npz as base (hard links), cands from kf_stage.run_kf, evidence linked by the
        tracker's object ids, fusion unchanged; writes <out_root>/kf/... and kf_times.json
Set KF_TAG to write <out_root>/<tag> instead of <out_root>/kf (parameter variants).
"""
import glob
import json
import os
import sys
import time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))).replace("\\", "/")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, f"{REPO}/scripts/occlusion")
sys.path.insert(0, f"{REPO}/scripts/occlusion/fusion_v2")
sys.path.insert(0, REPO)


def timer(mod, name, acc):
    import torch
    orig = getattr(mod, name)

    def wrapped(*a, **k):
        torch.cuda.synchronize()
        t = time.time()
        r = orig(*a, **k)
        torch.cuda.synchronize()
        acc[name] = acc.get(name, 0.0) + time.time() - t
        return r
    setattr(mod, name, wrapped)


def main():
    mode, out_root, pouts = sys.argv[1], sys.argv[2], sys.argv[3:]
    from utils.compile_cache import use_persistent_compile_cache
    use_persistent_compile_cache()
    import torch
    import r3common
    import fusion
    import evidence_fast
    import shared_cache
    if mode == "base":
        import run_fusion_v2
        import gpu_stage_fast
        import prep_fast
        tf = f"{out_root}/base_times.json"
        times = json.load(open(tf)) if os.path.exists(tf) else {}
        acc = {}
        for m, n in ((prep_fast, "prep"), (gpu_stage_fast, "run_gpu"), (gpu_stage_fast, "_sam3_stage"),
                     (evidence_fast, "compute"), (fusion, "render")):
            timer(m, n, acc)
        for pout in pouts:
            run = r3common.Run(pout)
            if run.name in times:
                continue
            acc.clear()
            t = time.time()
            run_fusion_v2.run_fusion(pout, f"{out_root}/base", run.name, save=True, points="c2f",
                                     compile_decoder=True)
            acc["total"] = time.time() - t
            times[run.name] = dict(frames=len(run), **{k: round(v, 2) for k, v in acc.items()})
            json.dump(times, open(tf, "w"), indent=1)
            print("base", run.name, times[run.name], flush=True)
            shared_cache.clear()
            torch.cuda.empty_cache()
    elif mode in ("prod", "prodfull"):
        # the production integration: run_fusion_v2.run_fusion(keyframe=True). prod: on base's mesh.npz / moge.npz
        # (hard links: prep and MoGe are skipped; the keyframe stage, evidence, decision and render run as in
        # production); prodfull: every stage from scratch, as base runs (timings comparable with base_times.json)
        import run_fusion_v2
        tag = os.environ.get("KF_TAG", "prod")
        tf = f"{out_root}/{tag}_times.json"
        times = json.load(open(tf)) if os.path.exists(tf) else {}
        for pout in pouts:
            run = r3common.Run(pout)
            if run.name in times:
                continue
            bc = f"{out_root}/base/_r3cache/{run.name}"
            cache = f"{out_root}/{tag}/_r3cache/{run.name}"
            os.makedirs(cache, exist_ok=True)
            for f in (glob.glob(f"{bc}/mesh.npz") + glob.glob(f"{bc}/moge.npz")) if mode == "prod" else []:
                dst = f"{cache}/{os.path.basename(f)}"
                if not os.path.exists(dst):
                    os.link(f, dst)
            for f in ("evidence.pkl", "cands.npz", "person.npz"):
                if os.path.exists(f"{cache}/{f}"):
                    os.remove(f"{cache}/{f}")
            t = time.time()
            run_fusion_v2.run_fusion(pout, f"{out_root}/{tag}", run.name, save=True, keyframe=True)
            st = json.load(open(f"{cache}/kf_stats.json"))
            times[run.name] = dict(frames=len(run), total=round(time.time() - t, 2),
                                   **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in st.items()
                                      if k in ("t_build", "t_backbone", "t_track", "t_keyframe", "t_signal",
                                               "t_total", "points", "obj_frames", "n_objects", "n_rows")},
                                   n_keyframes=len(st["keyframes"]))
            json.dump(times, open(tf, "w"), indent=1)
            print(tag, run.name, times[run.name], flush=True)
            shared_cache.clear()
            torch.cuda.empty_cache()
    elif mode == "redecide":
        # evidence / decision / render again on another tag's tracked candidates, with the decision unit split into
        # windows of KF_WINDOW frames per tracked object (track id = object id * 100000 + frame // window)
        src, tag, win = os.environ["KF_SRC"], os.environ["KF_TAG"], int(os.environ.get("KF_WINDOW", "0"))
        tf = f"{out_root}/{tag}_times.json"
        times = json.load(open(tf)) if os.path.exists(tf) else {}
        src_times = json.load(open(f"{out_root}/{src}_times.json"))
        shared_cache.install()
        for pout in pouts:
            run = r3common.Run(pout)
            if run.name in times or run.name not in src_times:
                continue
            sc = f"{out_root}/{src}/_r3cache/{run.name}"
            cache = f"{out_root}/{tag}/_r3cache/{run.name}"
            os.makedirs(cache, exist_ok=True)
            for f in glob.glob(f"{sc}/*.npz*") + glob.glob(f"{sc}/kf_stats.json"):
                dst = f"{cache}/{os.path.basename(f)}"
                if not os.path.exists(dst):
                    os.link(f, dst)
            if os.path.exists(f"{cache}/evidence.pkl"):
                os.remove(f"{cache}/evidence.pkl")
            if os.environ.get("KF_PERSON", "all") == "keyframes":
                # 'person' grounding only on the keyframes (where SAM3 encodes the frame anyway)
                kfs = json.load(open(f"{cache}/kf_stats.json"))["keyframes"]
                with np.load(f"{sc}/person.npz") as d:
                    sel = np.isin(d["frame"], kfs)
                    arrs = {k: d[k] for k in d.files}
                os.remove(f"{cache}/person.npz")                  # the hard link to the source's file
                np.savez_compressed(f"{cache}/person.npz", masks=arrs["masks"][sel], frame=arrs["frame"][sel],
                                    score=arrs["score"][sel], shape=arrs["shape"])
            with np.load(f"{cache}/cands.npz") as c:
                track, fr = c["track"].copy(), c["frame"].copy()
            if win:
                track = track * 100000 + fr // win
            evidence_fast.link = lambda masks, fr, _t=track: _t.copy()
            t = time.time()
            ev = evidence_fast.compute(run, cache)
            dec = fusion.decide(ev, fusion.P)
            fusion.render(run, ev, cache, dec, f"{out_root}/{tag}", fusion.P, save=True)
            times[run.name] = dict(src_times[run.name], redecide=round(time.time() - t, 2), window=win)
            json.dump(times, open(tf, "w"), indent=1)
            print(tag, run.name, "window", win, flush=True)
            shared_cache.clear()
    else:
        import kf_stage
        # as offline_app_lite: no cached bf16 copies of fp32 weights under SAM3's process-wide autocast
        torch.set_autocast_cache_enabled(False)
        tag = os.environ.get("KF_TAG", "kf")
        tf = f"{out_root}/{tag}_times.json"
        times = json.load(open(tf)) if os.path.exists(tf) else {}
        shared_cache.install()
        for pout in pouts:
            run = r3common.Run(pout)
            if run.name in times:
                continue
            bc = f"{out_root}/base/_r3cache/{run.name}"
            cache = f"{out_root}/{tag}/_r3cache/{run.name}"
            os.makedirs(cache, exist_ok=True)
            for f in glob.glob(f"{bc}/mesh.npz*") + glob.glob(f"{bc}/moge.npz*") + [f"{bc}/person.npz"]:
                dst = f"{cache}/{os.path.basename(f)}"
                if not os.path.exists(dst):
                    os.link(f, dst)
            for f in ("evidence.pkl", "cands.npz"):
                if os.path.exists(f"{cache}/{f}"):
                    os.remove(f"{cache}/{f}")
            t = time.time()
            st = kf_stage.run_kf(run, cache, log=lambda s: print(s, flush=True))
            t_kf = time.time() - t
            if os.environ.get("KF_PERSON", "all") == "keyframes":
                # the 'person' grounding only where SAM3 already encodes the frame (keyframes): evidence's
                # refine_visible then refines the visible masks on those frames only
                with np.load(f"{bc}/person.npz") as d:
                    sel = np.isin(d["frame"], st["keyframes"])
                    arrs = {k: d[k] for k in d.files}
                os.remove(f"{cache}/person.npz")                  # the hard link to base's file
                np.savez_compressed(f"{cache}/person.npz", masks=arrs["masks"][sel], frame=arrs["frame"][sel],
                                    score=arrs["score"][sel], shape=arrs["shape"])
            with np.load(f"{cache}/cands.npz") as c:
                track = c["track"].copy()
            evidence_fast.link = lambda masks, fr, _t=track: _t.copy()
            t = time.time()
            ev = evidence_fast.compute(run, cache)
            t_ev = time.time() - t
            t = time.time()
            dec = fusion.decide(ev, fusion.P)
            fusion.render(run, ev, cache, dec, f"{out_root}/{tag}", fusion.P, save=True)
            t_render = time.time() - t
            times[run.name] = dict(frames=len(run), kf_total=round(t_kf, 2), compute=round(t_ev, 2),
                                   render=round(t_render, 2),
                                   **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in st.items()
                                      if k in ("t_build", "t_backbone", "t_track", "t_keyframe", "t_signal",
                                               "t_total", "points", "obj_frames", "n_objects", "n_rows")},
                                   n_keyframes=len(st["keyframes"]))
            json.dump(times, open(tf, "w"), indent=1)
            print(tag, run.name, times[run.name], flush=True)
            shared_cache.clear()
            torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
