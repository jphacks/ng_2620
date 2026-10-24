"""Synthetic fixture v0 for the editor (designdocs/features/mock-server-fixtures, editor-viewer): a complete *.vvlog
project, as the app has it after fetching the analyze and render artifacts, with no GPU and no real footage.

  python fixtures/synth/make_synth.py [--out fixtures/synth/synth.vvlog] [--frames 96]

540x960, 2 avatars (moving discs, person 1 blue, person 2 orange) and 2 occluder units (squares):
  unit 1  a box in front of person 1's path     auto "front" for person 1 (the avatar is hidden behind it)
  unit 2  a board that person 2 passes over     auto "behind" for person 2 (the avatar is drawn over it)
expected/ holds the composites of frame 48 by the contract rule (contracts §5) with no correction and with
"unit 2 front for person 2", to check the WebGL compositor and the CPU reference against.
Needs numpy and Pillow (services/gpu_server/.venv has both).
"""
import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 540, 960
FPS = 30
ANALYZE, RENDER = "job_synth_analyze", "job_synth_render"
UNITS = {1: (60, 520, 260, 720), 2: (300, 300, 500, 380)}          # unitId -> x0, y0, x1, y1
PERSON_RGB = {1: (79, 140, 255), 2: (240, 136, 62)}
# = castHash(cast, finish) of apps/desktop/src/domain/cast.ts for the cast below (checked by
# apps/desktop/src/application/usecases/editor.test.ts): the editor would otherwise ask to render again
CAST_HASH = "02fb7031513986"


def background(f):
    y, x = np.mgrid[0:H, 0:W]
    img = np.stack([40 + 30 * np.sin(x / 50 + f / 20), 60 + 20 * np.cos(y / 70), 80 + 0 * x], -1)
    return img.clip(0, 255).astype(np.uint8)


def disc(cx, cy, r):
    y, x = np.mgrid[0:H, 0:W]
    d = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    return np.clip(r - d + 0.5, 0, 1)                      # anti-aliased edge: alpha in 0..1


def person_center(p, f, n):
    t = f / max(1, n - 1)
    if p == 1:
        return 100 + 240 * t, 600, 70                     # walks right behind unit 1
    return 420, 200 + 300 * t, 60                          # walks down over unit 2


def box_mask(u):
    x0, y0, x1, y1 = UNITS[u]
    m = np.zeros((H, W), bool)
    m[y0:y1, x0:x1] = True
    return m


def composite(frame, clean, avatars, hides, units, modes):
    out = clean.astype(np.float64)
    for p in (1, 2):                                        # order: 1 then 2 (far -> near)
        rgba = avatars[p].astype(np.float64)
        a = rgba[..., 3:4] / 255
        h = hides[p] >= 128
        for u in UNITS:
            m = modes.get((p, u))
            if m == "front":
                h = np.where(units == u, rgba[..., 3] > 0, h)
            elif m == "behind":
                h = np.where(units == u, False, h)
        c = np.where(h[..., None], frame, rgba[..., :3])
        out = out * (1 - a) + c * a
    return np.round(out).clip(0, 255).astype(np.uint8)


def main(a):
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    n = a.frames
    an, rd = out / "cache" / ANALYZE, out / "cache" / RENDER
    for d in [an / "frames", an / "units", an / "masks", an / "person_thumbs", rd / "clean", rd / "avatar/1",
              rd / "avatar/2", rd / "hide/1", rd / "hide/2", out / "expected"]:
        d.mkdir(parents=True, exist_ok=True)
    order = {}
    for f in range(n):
        name = f"{f:05d}"
        clean = background(f)
        for u, (x0, y0, x1, y1) in UNITS.items():            # the real occluders are part of the scene
            clean[y0:y1, x0:x1] = (150, 110, 70) if u == 1 else (90, 160, 90)
        frame = clean.copy()
        labels = np.zeros((H, W), np.uint16)
        for u in sorted(UNITS, key=lambda u: -box_mask(u).sum()):
            labels[box_mask(u)] = u
        masks = np.zeros((H, W), np.uint8)
        avatars, hides = {}, {}
        for p in (1, 2):
            cx, cy, r = person_center(p, f, n)
            al = disc(cx, cy, r)
            body = disc(cx, cy, r * 0.8) > 0.5             # the "real person" in the source frame
            visible = body & ~(box_mask(1) if p == 1 else np.zeros_like(body))
            frame[visible] = (200, 200, 200)
            masks[visible] = p
            rgba = np.zeros((H, W, 4), np.uint8)
            rgba[..., :3] = PERSON_RGB[p]
            rgba[..., 3] = np.round(al * 255).astype(np.uint8)
            avatars[p] = rgba
            hide = (box_mask(1) & (al > 0)) if p == 1 else np.zeros((H, W), bool)   # auto: unit 1 in front of 1
            hides[p] = np.where(hide, 255, 0).astype(np.uint8)
            Image.fromarray(rgba, "RGBA").save(rd / "avatar" / str(p) / f"{name}.webp", lossless=True)
            Image.fromarray(hides[p], "L").save(rd / "hide" / str(p) / f"{name}.png")
        Image.fromarray(frame).save(an / "frames" / f"{name}.jpg", quality=95)
        Image.fromarray(clean).save(rd / "clean" / f"{name}.webp", lossless=True)
        Image.fromarray(masks, "L").save(an / "masks" / f"{name}.png")
        Image.fromarray(labels).save(an / "units" / f"{name}.png")            # uint16 -> 16-bit grayscale PNG
        order[str(f)] = [1, 2]
        if f == n // 2:
            frame_rgb = np.asarray(Image.open(an / "frames" / f"{name}.jpg").convert("RGB"))
            for tag, modes in (("none", {}), ("unit2front", {(2, 2): "front"})):
                Image.fromarray(composite(frame_rgb, clean, avatars, hides, labels, modes)).save(
                    out / "expected" / f"{tag}_{name}.png")
    for p in (1, 2):
        cx, cy, r = person_center(p, n // 2, n)
        Image.new("RGB", (256, 256), PERSON_RGB[p]).save(an / "person_thumbs" / f"{p}.jpg")
    meta = {"durationSec": n / FPS, "frameCount": n, "fps": FPS, "full": {"width": W, "height": H},
            "proxy": {"width": W, "height": H}, "proxyScale": 1, "hasAudio": False, "sourceCodec": "h264"}
    persons = [{"personId": p, "label": f"人物 {p}", "firstFrame": 0, "lastFrame": n - 1, "visibleRatio": 1.0,
                "bestFrame": n // 2, "thumb": f"cache/{ANALYZE}/person_thumbs/{p}.jpg"} for p in (1, 2)]
    units = {"units": [
        {"unitId": 1, "frames": [0, n - 1], "label": "object", "tracks": [1], "areaPx": int(box_mask(1).sum()),
         "perPerson": {"1": {"auto": "front", "why": "D+", "confidence": 0.9, "overlapFrames": [0, int(n * 0.7)]},
                       "2": {"auto": "none", "why": "", "confidence": 0.0, "overlapFrames": []}}},
        {"unitId": 2, "frames": [0, n - 1], "label": "object", "tracks": [2], "areaPx": int(box_mask(2).sum()),
         "perPerson": {"1": {"auto": "none", "why": "", "confidence": 0.0, "overlapFrames": []},
                       "2": {"auto": "behind", "why": "S-", "confidence": 0.4, "overlapFrames": [int(n * 0.2), int(n * 0.6)]}}},
    ]}
    (an / "meta.json").write_text(json.dumps(meta, indent=1))
    (an / "persons.json").write_text(json.dumps([{k: v for k, v in p.items() if k != "label"} for p in persons], indent=1))
    (an / "units.json").write_text(json.dumps(units, ensure_ascii=False, indent=1))
    (rd / "layers.json").write_text(json.dumps({"persons": [1, 2], "order": order,
                                                "avatarIds": {"1": "av_synth000000001", "2": "av_synth000000002"}}))
    now = "2026-10-24T00:00:00.000Z"
    project = {
        "schemaVersion": 1, "id": "loc_synth_v0", "name": "合成フィクスチャ v0", "createdAt": now, "updatedAt": now,
        "source": {"path": "synth.mp4", "copied": False, "durationSec": n / FPS, "width": W, "height": H, "fps": FPS,
                   "hasAudio": False},
        "compute": {"profileId": None},
        "jobs": {"analyze": {"jobId": ANALYZE, "status": "succeeded", "finishedAt": now},
                 "render": {"jobId": RENDER, "status": "succeeded", "finishedAt": now, "castHash": CAST_HASH},
                 "exports": []},
        "meta": meta, "persons": persons,
        "cast": [{"personId": p, "action": "replace", "avatarId": f"av_synth00000000{p}"} for p in (1, 2)],
        "finish": {"smooth": 0.05, "footLock": True, "spring": True},
        "avatars": {f"av_synth00000000{p}": {"name": f"円 {p}", "file": "", "sha256": ""} for p in (1, 2)},
        "ui": {"playhead": 0, "zoom": 1, "panelLayout": "default"},
        "sample": {"id": "synth_v0", "license": "合成データ（テスト用）"}, "owner": None, "createdBy": None,
    }
    (out / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=1))
    (out / "overrides.json").write_text(json.dumps({"schemaVersion": 1, "items": []}))
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"wrote {out} ({n} frames, {size / 1e6:.1f} MB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "synth.vvlog"))
    ap.add_argument("--frames", type=int, default=96)
    main(ap.parse_args())
