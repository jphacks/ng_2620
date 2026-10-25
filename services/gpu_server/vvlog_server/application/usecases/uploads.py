"""Chunked upload of the source video, then its validation (contracts §2.1, F02)."""
from __future__ import annotations

from pathlib import Path

from ...domain import media as M
from ...domain.errors import VvlogError
from ...domain.job import new_id, now_iso
from ...domain.upload import Upload
from ..deps import Deps


def start_upload(d: Deps, project_id: str, kind: str, filename: str, size: int, sha256: str) -> dict:
    d.projects.get(project_id)
    u = Upload.create(project_id, kind, filename, size, sha256, limit=d.settings.max_upload_bytes)
    d.uploads.save(u)
    return {"uploadId": u.uploadId, "chunkSize": u.chunkSize}


def put_chunk(d: Deps, upload_id: str, index: int, data: bytes) -> dict:
    u = d.uploads.get(upload_id)
    u.receive(index, len(data))                      # validates the length before writing
    d.uploads.write_chunk(u, index, data)
    d.uploads.save(u)
    return {"receivedChunks": u.receivedChunks, "size": u.size}


def get_upload(d: Deps, upload_id: str) -> dict:
    u = d.uploads.get(upload_id)
    return {"receivedChunks": u.receivedChunks, "size": u.size}


def complete_upload(d: Deps, upload_id: str) -> dict:
    u = d.uploads.get(upload_id)
    project = d.projects.get(u.projectId)
    if u.mediaId:                                    # completed before: idempotent
        m = project["media"][u.mediaId]
        return {"mediaId": u.mediaId, "meta": m["meta"]}
    missing = u.missing()
    if missing:
        raise VvlogError("UPLOAD_INCOMPLETE", "断片が足りません", {"missingChunks": missing})
    media_id = new_id("med")
    ext = Path(u.filename).suffix.lower() or ".mp4"
    dest = d.projects.root(u.projectId) / "media" / media_id / f"source{ext}"
    got = d.uploads.assemble(u, dest)
    if got != u.sha256:
        dest.unlink(missing_ok=True)
        raise VvlogError("CHECKSUM_MISMATCH", "ファイルが壊れています（sha256 が一致しません）",
                         {"expected": u.sha256, "actual": got})
    probe = d.media.probe(dest)
    try:
        M.validate(probe, d.settings.max_duration_sec, d.settings.max_long_side)
    except VvlogError:
        dest.unlink(missing_ok=True)
        raise
    meta = M.plan_meta(probe).to_dict()
    project["media"][media_id] = {"mediaId": media_id, "filename": u.filename, "size": u.size, "sha256": u.sha256,
                                  "path": str(dest.relative_to(d.projects.root(u.projectId))), "meta": meta,
                                  "createdAt": now_iso()}
    d.projects.save(project)
    u.mediaId = media_id
    d.uploads.save(u)
    d.uploads.discard_chunks(u)
    return {"mediaId": media_id, "meta": meta}
