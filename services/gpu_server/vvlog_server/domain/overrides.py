"""The editor's occlusion corrections for export (contracts §6.2, pipeline-integration §3.6). No I/O here."""
from __future__ import annotations

from .errors import VvlogError


def expand(units_index: dict, overrides: list[dict], n_frames: int) -> list[dict]:
    """Unit corrections -> {personId, tracks, range, mode} for avatar_app --overrides, in the order they were made
    (the last one covering a frame wins). Brush corrections (P1) are not applied by the server yet."""
    tracks = {int(u["unitId"]): [int(t) for t in u.get("tracks", [])] for u in units_index.get("units", [])}
    out = []
    for o in overrides:
        if o.get("kind", "unit") != "unit":
            continue
        uid = int(o["unitId"])
        if uid not in tracks:
            raise VvlogError("VALIDATION_FAILED", "存在しない遮蔽物ユニットです", {"unitId": uid, "overrideId": o.get("id")})
        f0, f1 = (int(x) for x in o["range"])
        if f0 > f1 or f1 < 0 or f0 >= n_frames:
            raise VvlogError("VALIDATION_FAILED", "手修正の区間が不正です", {"range": o["range"], "overrideId": o.get("id")})
        if o["mode"] not in ("front", "behind"):
            raise VvlogError("VALIDATION_FAILED", "mode は front か behind です", {"mode": o["mode"]})
        out.append({"personId": int(o["personId"]), "tracks": tracks[uid], "range": [max(0, f0), min(n_frames - 1, f1)],
                    "mode": o["mode"], "id": o.get("id")})
    return out
