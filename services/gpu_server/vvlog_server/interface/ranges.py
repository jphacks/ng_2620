"""File responses with a single byte Range (bytes=a-b, a-, -n), for video seeking and resumable downloads."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.responses import FileResponse, Response, StreamingResponse

_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def _iter(path: Path, start: int, length: int, block: int = 1 << 20):
    with open(path, "rb") as f:
        f.seek(start)
        while length > 0:
            b = f.read(min(block, length))
            if not b:
                break
            length -= len(b)
            yield b


def range_response(path: Path, content_type: str, range_header: str | None) -> Response:
    size = path.stat().st_size
    m = _RANGE.match((range_header or "").strip())
    if not m or (not m[1] and not m[2]):
        return FileResponse(path, media_type=content_type, headers={"Accept-Ranges": "bytes"})
    if m[1]:
        start, end = int(m[1]), int(m[2]) if m[2] else size - 1
    else:
        start, end = max(0, size - int(m[2])), size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    length = end - start + 1
    return StreamingResponse(_iter(path, start, length), status_code=206, media_type=content_type,
                             headers={"Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes",
                                      "Content-Length": str(length)})
