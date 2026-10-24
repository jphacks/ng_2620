"""Server-side projects: create, restore after a reconnect, delete."""
from __future__ import annotations

from ...domain.errors import VvlogError
from ..deps import Deps
from .jobs import job_view


def create_project(d: Deps, name: str) -> dict:
    name = (name or "").strip()
    if not name or len(name) > 200:
        raise VvlogError("VALIDATION_FAILED", "プロジェクト名は 1〜200 文字です", {"field": "name"})
    p = d.projects.create(name)
    return {"projectId": p["projectId"]}


def get_project(d: Deps, project_id: str) -> dict:
    p = d.projects.get(project_id)
    return {"projectId": p["projectId"], "name": p["name"], "createdAt": p["createdAt"],
            "media": list(p.get("media", {}).values()),
            "jobs": [job_view(d, j) for j in d.jobs.list(project_id)]}


async def delete_project(d: Deps, project_id: str) -> None:
    d.projects.get(project_id)
    for j in d.jobs.list(project_id):
        if not j.finished:
            await d.worker.cancel(j.jobId)
    d.projects.delete(project_id)
