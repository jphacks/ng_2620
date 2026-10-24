"""Submitting, inspecting and canceling jobs; artifacts and logs (contracts §2.1, §3)."""
from __future__ import annotations

from pathlib import Path

from ...domain.errors import VvlogError, not_found
from ...domain.job import Job
from ..deps import Deps
from ..stages.base import log_tail

MAX_TARGETS = 4
EXPORT_FORMATS = ("mp4_h264",)
RESERVED_FORMATS = ("prores4444", "png_seq", "layers", "vrma")


def job_view(d: Deps, job: Job) -> dict:
    v = job.to_dict()
    pos = d.queue.position(job.jobId) if job.status == "queued" else None
    if pos is not None:
        v["queuePosition"] = pos
    else:
        v.pop("queuePosition", None)
    return v


def _validate(d: Deps, project: dict, job_type: str, params: dict) -> dict:
    pid = project["projectId"]
    if job_type == "analyze":
        media_id = params.get("mediaId")
        if media_id not in project.get("media", {}):
            raise not_found("mediaId", str(media_id))
        mt = int(params.get("maxTargets") or MAX_TARGETS)
        if not 1 <= mt <= MAX_TARGETS:
            raise VvlogError("VALIDATION_FAILED", f"maxTargets は 1〜{MAX_TARGETS} です", {"maxTargets": mt})
        return {"mediaId": media_id, "maxTargets": mt}
    if job_type == "render":
        dep = _dep(d, pid, params.get("analyzeJobId"), "analyze")
        cast = params.get("cast") or []
        if not cast:
            raise VvlogError("VALIDATION_FAILED", "cast が空です", {"field": "cast"})
        seen = set()
        for c in cast:
            if c.get("action") != "replace":
                raise VvlogError("NOT_IMPLEMENTED", "置換以外の動作は今後対応です", {"action": c.get("action")})
            if c["personId"] in seen:
                raise VvlogError("VALIDATION_FAILED", "同じ人物が 2 回指定されています", {"personId": c["personId"]})
            seen.add(c["personId"])
            d.avatars.get(c["avatarId"])            # 404 -> the app uploads the VRM again
        return {"analyzeJobId": dep.jobId, "cast": cast, "finish": params.get("finish") or {}}
    if job_type == "export":
        dep = _dep(d, pid, params.get("renderJobId"), "render")
        settings = dict(params.get("settings") or {})
        fmt = settings.get("format", "mp4_h264")
        if fmt in RESERVED_FORMATS:
            raise VvlogError("NOT_IMPLEMENTED", "この形式は今後対応です", {"format": fmt})
        if fmt not in EXPORT_FORMATS:
            raise VvlogError("VALIDATION_FAILED", "不明な形式です", {"format": fmt})
        settings = {"format": fmt, "crf": int(settings.get("crf", 18)), "audio": bool(settings.get("audio", True)),
                    "resolution": settings.get("resolution", "full")}
        return {"renderJobId": dep.jobId, "overrides": params.get("overrides") or [], "settings": settings}
    raise VvlogError("VALIDATION_FAILED", f"不明なジョブ種別です: {job_type}", {"type": job_type})


def _dep(d: Deps, project_id: str, job_id: str | None, job_type: str) -> Job:
    if not job_id:
        raise VvlogError("VALIDATION_FAILED", f"{job_type} ジョブの ID が必要です", {})
    dep = d.jobs.get(job_id)
    if dep.projectId != project_id or dep.type != job_type:
        raise VvlogError("VALIDATION_FAILED", f"{job_id} はこのプロジェクトの {job_type} ジョブではありません",
                         {"jobId": job_id})
    if dep.status in ("failed", "canceled"):
        raise VvlogError("DEPENDENCY_NOT_READY", "依存するジョブが成功していません",
                         {"dependsOn": dep.jobId, "status": dep.status})
    return dep


def submit_job(d: Deps, project_id: str, job_type: str, params: dict) -> dict:
    project = d.projects.get(project_id)
    job = Job.create(project_id, job_type, _validate(d, project, job_type, params))
    d.jobs.save(job)
    d.queue.enqueue(job.jobId)
    pos = d.queue.position(job.jobId)
    job.queuePosition = pos
    d.jobs.save(job)
    d.bus.publish(job.jobId, "queued", {"position": pos})
    d.worker.wake()
    return job_view(d, job)


def get_job(d: Deps, job_id: str) -> dict:
    return job_view(d, d.jobs.get(job_id))


def list_jobs(d: Deps, project_id: str) -> list[dict]:
    d.projects.get(project_id)
    return [job_view(d, j) for j in d.jobs.list(project_id)]


def queue(d: Deps) -> dict:
    items = []
    if d.worker.current:
        j = d.worker.current[0]
        items.append({"jobId": j.jobId, "projectId": j.projectId, "type": j.type, "status": j.status, "position": -1})
    for i, jid in enumerate(d.queue.items()):
        j = d.jobs.get(jid)
        items.append({"jobId": j.jobId, "projectId": j.projectId, "type": j.type, "status": j.status, "position": i})
    return {"items": items}


async def cancel_job(d: Deps, job_id: str) -> dict:
    return job_view(d, await d.worker.cancel(job_id))


def list_artifacts(d: Deps, job_id: str) -> list[dict]:
    return [a.to_dict() for a in d.store.list(job_id)]


def open_artifact(d: Deps, job_id: str, name: str) -> tuple[Path, dict]:
    path, a = d.store.open(job_id, name)
    return path, a.to_dict()


def job_logs(d: Deps, job_id: str, tail: int) -> str:
    job = d.jobs.get(job_id)
    work = d.projects.root(job.projectId) / "work" / job.jobId
    logs = sorted(work.glob("*.log"), key=lambda p: p.stat().st_mtime) if work.is_dir() else []
    return "\n".join(f"==> {p.name} <==\n{log_tail(p, tail)}" for p in logs)


def capabilities(d: Deps) -> dict:
    s = d.settings
    gpu = d.gpu.info()
    return {"gpu": gpu or None, "limits": {"maxDurationSec": s.max_duration_sec, "maxLongSide": s.max_long_side,
                                           "maxFps": 30, "maxTargets": MAX_TARGETS,
                                           "formats": list(EXPORT_FORMATS)},
            "queueLength": len(d.queue.items()) + (1 if d.worker.current else 0),
            "backend": s.backend}
