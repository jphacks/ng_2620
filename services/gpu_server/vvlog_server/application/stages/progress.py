"""Reading progress out of the pipeline's output lines (designdocs/architecture/pipeline-integration §2)."""
from __future__ import annotations

import re

STAGE_LINE = re.compile(r"^\[STAGE\]\[(\w+)\] time=([\d.]+) s")
TQDM = re.compile(r"(\d+)/(\d+) \[")             # "  42%|####      | 21/50 [00:03<00:04, ..."
FUSION_FRAME = re.compile(r"^\S+ (\d+) pts ")    # fusion v2 per-frame line: "<name> <frame> pts ... kept ..."
FUSION_DONE = re.compile(r"^\[fusion_v2\] .*total (\d+)s")
AVATAR_FRAME = re.compile(r"^\[avatar\] frame (\d+)/(\d+)")
ETA = re.compile(r"<(\d+):(\d+)(?::(\d+))?[,\]]")


def stage_done(line: str) -> str | None:
    m = STAGE_LINE.match(line.strip())
    return m[1] if m else None


def tqdm_fraction(line: str) -> tuple[float, float | None] | None:
    m = TQDM.search(line)
    if not m or int(m[2]) == 0:
        return None
    eta = None
    e = ETA.search(line)
    if e:
        a, b, c = int(e[1]), int(e[2]), e[3]
        eta = a * 3600 + b * 60 + int(c) if c else a * 60 + b
    return int(m[1]) / int(m[2]), eta
