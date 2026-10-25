"""Housekeeping for servers that stay up (VVLOG_ARTIFACT_TTL_HOURS): drop what nobody will come back for."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..deps import Deps

log = logging.getLogger(__name__)


def _when(iso: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None
    return t if t is None or t.tzinfo else t.replace(tzinfo=timezone.utc)


def purge_expired(d: Deps, ttl_hours: float, now: datetime | None = None) -> dict:
    """Removes projects whose jobs are all finished and whose last activity is older than the TTL (with their jobs and
    artifacts), and upload leftovers older than the TTL. Anything queued or running is left alone. TTL <= 0: nothing."""
    out = {"projects": [], "uploads": []}
    if ttl_hours <= 0:
        return out
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=ttl_hours)

    for up in d.uploads.list():
        created = _when(up.createdAt)
        if created and created < cutoff:
            d.uploads.delete(up.uploadId)
            out["uploads"].append(up.uploadId)

    for pdir in sorted(d.projects.base.iterdir()) if d.projects.base.is_dir() else []:
        f = pdir / "project.json"
        if not f.is_file():
            continue
        pid = pdir.name
        jobs = d.jobs.list(pid)
        if any(not j.finished for j in jobs):
            continue
        last = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)
        for j in jobs:
            for t in (_when(j.finishedAt), _when(j.startedAt), _when(j.createdAt)):
                if t and t > last:
                    last = t
        if last >= cutoff:
            continue
        for j in jobs:
            d.jobs.delete(j.jobId)
        d.projects.delete(pid)
        out["projects"].append(pid)
    if out["projects"] or out["uploads"]:
        log.info("purged %d projects, %d uploads older than %gh", len(out["projects"]), len(out["uploads"]), ttl_hours)
    return out
