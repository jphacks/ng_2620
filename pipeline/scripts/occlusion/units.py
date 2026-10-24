"""Occluder units for the editor (designdocs/architecture/pipeline-integration §3.2, contracts §4.1).

fusion v2 decides per (track, person, part); its evidence tracks are short (half of them one frame long on downtown_bar,
300 frames). A unit links the tracks of one real object over time: track b continues track a when b starts within
GAP frames (= FUSION_KF_MAX, the longest stretch between keyframes) after a ends and the masks of a's last frame and b's first frame overlap with
IoU >= IOU_LINK (downtown_bar, 300 frames: 1142 tracks -> 172 units). Units shorter than MIN_FRAMES that fusion put in
front of nobody are left out: nothing to correct there, and hundreds of flickering fragments make the editor unusable. Each frame then gets a 16-bit label image (0 = none,
value = unitId; where units overlap the smaller one wins) and units.json says, per unit and person, what fusion
decided (front / behind / none) and over which frames the unit touches that person.
"""
from __future__ import annotations

import json
import os

import numpy as np

GAP = 10
IOU_LINK = 0.3
MIN_FRAMES = 5


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def object_tracks(ev) -> dict[int, dict[int, int]]:
    """track -> {frame: candidate index}, from fusion's per-frame object evidence (people excluded)."""
    tracks: dict[int, dict[int, int]] = {}
    for u in ev["units"]:
        if u.get("person") is not None:
            continue
        tracks.setdefault(int(u["tr"]), {})[int(u["i"])] = int(u["ci"])
    return tracks


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter) / float(union) if union else 0.0


def link_tracks(tracks: dict[int, dict[int, int]], masks, gap: int = GAP, iou: float = IOU_LINK) -> list[list[int]]:
    """Groups of track ids, ordered by first frame; masks[ci] -> bool mask."""
    uf = _UF()
    span = {t: (min(f), max(f)) for t, f in tracks.items() if f}
    order = sorted(span, key=lambda t: span[t])
    for a in order:
        uf.find(a)
        a0, a1 = span[a]
        for b in order:
            b0, _ = span[b]
            if b == a or not (a1 < b0 <= a1 + gap):
                continue
            if _iou(masks[tracks[a][a1]], masks[tracks[b][b0]]) >= iou:
                uf.union(a, b)
    groups: dict[int, list[int]] = {}
    for t in order:
        groups.setdefault(uf.find(t), []).append(t)
    return sorted(groups.values(), key=lambda g: min(span[t][0] for t in g))


def _confidence(z, R) -> float:
    if z is not None and np.isfinite(z):
        return round(float(min(1.0, abs(z) / 3.0)), 3)
    return round(float(min(1.0, R)), 3) if R is not None and np.isfinite(R) else 0.0


def per_person(group: list[int], ev, dec, pids) -> dict:
    """Aggregates dec[(track, pid, part, 'o')] over the unit's tracks and the body parts: one front makes it front."""
    out = {}
    for pid in pids:
        frames = [int(u["i"]) for u in ev["units"] if u.get("person") is None and u["tr"] in group
                  and u["pid"] == pid]
        keys = [k for k in dec if len(k) == 4 and k[3] == "o" and k[0] in group and k[1] == pid]
        if not keys:
            out[str(pid)] = {"auto": "none", "why": "", "confidence": 0.0,
                             "overlapFrames": [min(frames), max(frames)] if frames else []}
            continue
        fronts = [k for k in keys if dec[k][0]]
        k = max(fronts or keys, key=lambda k: abs(dec[k][2]) if np.isfinite(dec[k][2]) else dec[k][3])
        front, why, z, R, _h = dec[k]
        out[str(pid)] = {"auto": "front" if fronts else "behind", "why": why, "confidence": _confidence(z, R),
                         "overlapFrames": [min(frames), max(frames)] if frames else []}
    return out


def build_units(ev, dec, masks, n_frames: int, pids) -> tuple[list[dict], dict[int, list[tuple[int, int]]]]:
    """-> (units for units.json, frame -> [(unitId, candidate index)] for the label images)."""
    tracks = object_tracks(ev)
    units, by_frame = [], {}
    uid = 0
    for group in link_tracks(tracks, masks):
        frames = sorted({f for t in group for f in tracks[t]})
        pp = per_person(group, ev, dec, pids)
        if frames[-1] - frames[0] + 1 < MIN_FRAMES and not any(v["auto"] == "front" for v in pp.values()):
            continue
        uid += 1
        areas = []
        for t in group:
            for f, ci in tracks[t].items():
                by_frame.setdefault(f, []).append((uid, ci))
                areas.append(int(masks[ci].sum()))
        units.append({"unitId": uid, "frames": [frames[0], frames[-1]], "label": "object", "tracks": group,
                      "areaPx": int(np.mean(areas)) if areas else 0, "perPerson": pp})
    return units, by_frame


def label_image(entries, masks, shape) -> np.ndarray:
    """16-bit labels; larger units are painted first so smaller ones stay on top."""
    lab = np.zeros(shape, np.uint16)
    for uid, ci in sorted(entries, key=lambda e: -int(masks[e[1]].sum())):
        lab[masks[ci]] = uid
    return lab


def write_units(out_dir: str, ev, dec, masks, n_frames: int, shape, pids) -> list[dict]:
    """<out_dir>/units/{f:05d}.png (16-bit) for every frame and <out_dir>/units.json."""
    import cv2
    units, by_frame = build_units(ev, dec, masks, n_frames, pids)
    d = os.path.join(out_dir, "units")
    os.makedirs(d, exist_ok=True)
    for f in range(n_frames):
        cv2.imwrite(os.path.join(d, f"{f:05d}.png"), label_image(by_frame.get(f, []), masks, shape))
    with open(os.path.join(out_dir, "units.json"), "w", encoding="utf-8") as fh:
        json.dump({"units": units}, fh, indent=1)
    return units
