"""Reads the pipeline's output folders and turns them into contract artifacts (contracts §4).

The pipeline writes pout/masks/%08d.png (palette PNG, value = obj id = personId), pout/images/, pout/4d_*.mp4.
Frame f of these is frame f of the ingest proxy (both are the sorted proxy frames)."""
from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from .h264 import video_args

THUMB = 256


def _masks(pout: Path) -> list[Path]:
    return sorted((pout / "masks").glob("*.png"))


def persons_from_masks(pout: Path, proxy: Path, out: Path) -> list[dict]:
    """persons.json, person_thumbs.zip, track_masks.zip in `out`; returns the persons (empty: nobody tracked)."""
    out.mkdir(parents=True, exist_ok=True)
    masks = _masks(pout)
    frames = sorted(proxy.glob("*.jpg"))
    seen: dict[int, dict] = {}
    with zipfile.ZipFile(out / "track_masks.zip", "w", zipfile.ZIP_STORED) as zm:
        for f, mp in enumerate(masks):
            lab = np.array(Image.open(mp))                 # palette indices = obj ids
            if lab.ndim == 3:
                lab = lab[..., 0]
            lab = lab.astype(np.uint8)
            buf = _png_bytes(lab)
            zm.writestr(f"masks/{f:05d}.png", buf)
            for pid in np.unique(lab):
                if pid == 0:
                    continue
                ys, xs = np.nonzero(lab == pid)
                s = seen.setdefault(int(pid), {"frames": [], "best": (-1, 0, None)})
                s["frames"].append(f)
                if len(xs) > s["best"][0]:
                    s["best"] = (len(xs), f, [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1),
                                              int(ys.max() - ys.min() + 1)])
    persons = []
    with zipfile.ZipFile(out / "person_thumbs.zip", "w", zipfile.ZIP_STORED) as zt:
        for pid in sorted(seen):
            s = seen[pid]
            first, last = s["frames"][0], s["frames"][-1]
            _, best, bbox = s["best"]
            persons.append({"personId": pid, "firstFrame": first, "lastFrame": last,
                            "visibleRatio": round(len(s["frames"]) / (last - first + 1), 4), "bestFrame": best,
                            "bbox": bbox, "thumb": f"person_thumbs/{pid}.jpg"})
            if best < len(frames):
                zt.writestr(f"person_thumbs/{pid}.jpg", _thumb(frames[best], bbox))
    (out / "persons.json").write_text(json.dumps(persons, indent=1), encoding="utf-8")
    return persons


def _png_bytes(a: np.ndarray) -> bytes:
    import io
    b = io.BytesIO()
    Image.fromarray(a, mode="L").save(b, format="PNG", optimize=False, compress_level=1)
    return b.getvalue()


def _thumb(frame: Path, bbox: list[int]) -> bytes:
    import io
    img = Image.open(frame).convert("RGB")
    x, y, w, h = bbox
    side = int(max(w, h) * 1.15)
    cx, cy = x + w / 2, y + h / 2
    box = (int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))
    crop = img.crop(box).resize((THUMB, THUMB), Image.LANCZOS)        # outside the frame stays black
    b = io.BytesIO()
    crop.save(b, format="JPEG", quality=90)
    return b.getvalue()


def latest(pattern_dir: Path, pattern: str) -> Path | None:
    c = sorted(pattern_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    return c[-1] if c else None


def to_h264(src: Path, dest: Path, crf: int = 23, size: tuple[int, int] | None = None) -> Path:
    """The pipeline's mp4s are OpenCV mp4v or imageio's (height stretched to a multiple of 16), which WebViews may
    not play or which do not match the proxy: re-encode to H.264, back at `size` (w, h) when given."""
    if not shutil.which("ffmpeg"):
        shutil.copyfile(src, dest)
        return dest
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(src), *video_args(crf),
                    "-pix_fmt", "yuv420p", "-vf", f"scale={size[0]}:{size[1]}" if size else "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-movflags", "+faststart",
                    str(dest)], check=True, capture_output=True)
    return dest


def _concat_entry(p: Path) -> str:
    return "file '" + p.resolve().as_posix().replace("'", "'\\''") + "'\n"


def frames_to_h264(frames_dir: Path, fps: float, dest: Path, crf: int = 20) -> Path | None:
    """avatar_app's frames/<stem>.jpg at their exact size (its own mp4 goes through imageio, which stretches the
    height to a multiple of 16: 854 -> 864). None without ffmpeg or frames.
    Read through the concat demuxer: `-pattern_type glob` is not in Windows builds, and the stems need not be
    0-based and gapless for %08d (image2 stops silently at a gap). The last file is listed twice so that its
    duration counts on older ffmpeg; newer ones emit it as an extra frame, hence -frames:v."""
    files = sorted(frames_dir.glob("*.jpg"))
    if not files or not shutil.which("ffmpeg"):
        return None
    lst = dest.with_name(dest.stem + ".frames.txt")
    lst.write_text("".join(_concat_entry(p) + f"duration {1 / fps}\n" for p in files) + _concat_entry(files[-1]),
                   encoding="utf-8")
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-r", str(fps), "-frames:v", str(len(files)), *video_args(crf), "-pix_fmt", "yuv420p",
                        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-movflags", "+faststart", str(dest)],
                       check=True, capture_output=True)
    finally:
        lst.unlink(missing_ok=True)
    return dest


class FsPipelineOutputs:
    def persons_from_masks(self, pout: Path, proxy: Path, out: Path) -> list[dict]:
        return persons_from_masks(pout, proxy, out)

    def latest_mesh_video(self, pout: Path) -> Path | None:
        return latest(pout, "4d_*.mp4")

    def to_h264(self, src: Path, dest: Path, size: tuple[int, int] | None = None) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        return to_h264(src, dest, size=size)

    def zip_layers(self, layers: Path, dest: Path) -> Path:
        """clean/, avatar/<pid>/, hide/<pid>/ as stored (contracts §4.2); layers.json is its own artifact."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as z:
            for f in sorted(layers.rglob("*")):
                if f.is_file() and f.name != "layers.json":
                    z.write(f, f.relative_to(layers).as_posix())
        return dest

    def frames_to_h264(self, frames_dir: Path, fps: float, dest: Path) -> Path | None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        return frames_to_h264(frames_dir, fps, dest)
