"""File-system repositories under VVLOG_DATA_ROOT.

  jobs/<jobId>/job.json, events.jsonl          FsJobRepository, JsonlEventBus
  projects/<projectId>/project.json            FsProjectRepository
  projects/<projectId>/artifacts/<jobId>/      FsArtifactStore (index.json + files)
  projects/<projectId>/work/<jobId>/           stage scratch
  uploads/<uploadId>/upload.json, chunks/      FsUploadRepository
  avatars/<avatarId>.vrm, <avatarId>.json      FsAvatarStore
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from pathlib import Path

from ..domain.artifact import CATALOG, Artifact
from ..domain.errors import VvlogError, not_found
from ..domain.job import Job, new_id, now_iso
from ..domain.upload import Upload
from . import vrm

_lock = threading.RLock()


def write_json(path: Path, obj) -> None:
    """Atomic: a crash leaves the old file or the new one, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class FsJobRepository:
    def __init__(self, root: Path):
        self.root = root / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Job] = {}

    def save(self, job: Job) -> None:
        with _lock:
            self._cache[job.jobId] = job
            write_json(self.root / job.jobId / "job.json", job.to_dict())

    def get(self, job_id: str) -> Job:
        with _lock:
            if job_id in self._cache:
                return self._cache[job_id]
            p = self.root / job_id / "job.json"
            if not p.is_file():
                raise not_found("jobId", job_id)
            job = Job.from_dict(read_json(p))
            self._cache[job_id] = job
            return job

    def list(self, project_id: str | None = None) -> list[Job]:
        out = []
        for p in sorted(self.root.glob("*/job.json")):
            job = self.get(p.parent.name)
            if project_id is None or job.projectId == project_id:
                out.append(job)
        return sorted(out, key=lambda j: j.createdAt)


class FsProjectRepository:
    def __init__(self, root: Path):
        self.base = root / "projects"
        self.base.mkdir(parents=True, exist_ok=True)

    def root(self, project_id: str) -> Path:
        return self.base / project_id

    def create(self, name: str) -> dict:
        p = {"projectId": new_id("prj"), "name": name, "createdAt": now_iso(), "media": {}}
        self.save(p)
        return p

    def get(self, project_id: str) -> dict:
        f = self.root(project_id) / "project.json"
        if not f.is_file():
            raise not_found("projectId", project_id)
        return read_json(f)

    def save(self, project: dict) -> None:
        with _lock:
            write_json(self.root(project["projectId"]) / "project.json", project)

    def delete(self, project_id: str) -> None:
        self.get(project_id)
        shutil.rmtree(self.root(project_id), ignore_errors=True)


class FsArtifactStore:
    def __init__(self, projects: FsProjectRepository, jobs: FsJobRepository):
        self.projects, self.jobs = projects, jobs

    def _dir(self, job: Job) -> Path:
        return self.projects.root(job.projectId) / "artifacts" / job.jobId

    def _index(self, job: Job) -> dict:
        f = self._dir(job) / "index.json"
        return read_json(f) if f.is_file() else {}

    def publish(self, job: Job, name: str, path: Path) -> Artifact:
        kind, stage, fname, ctype = CATALOG[name]
        d = self._dir(job)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / fname
        if Path(path).resolve() != dest.resolve():
            if _publish_only(path):
                os.replace(path, dest)
            else:
                shutil.copyfile(path, dest)
        a = Artifact(name=name, kind=kind, stage=stage or job.stages[-1].name, size=dest.stat().st_size,
                     sha256=sha256_file(dest), contentType=ctype, url=f"/v1/jobs/{job.jobId}/artifacts/{name}")
        with _lock:
            idx = self._index(job)
            idx[name] = a.to_dict()
            write_json(d / "index.json", idx)
        return a

    def list(self, job_id: str) -> list[Artifact]:
        job = self.jobs.get(job_id)
        return [Artifact(**v) for v in self._index(job).values()]

    def open(self, job_id: str, name: str) -> tuple[Path, Artifact]:
        job = self.jobs.get(job_id)
        idx = self._index(job)
        if name not in idx:
            raise not_found("artifact", name)
        return self._dir(job) / CATALOG[name][2], Artifact(**idx[name])


def _publish_only(src: Path) -> bool:
    """Stage outputs written for publishing only (under work/_publish/) are moved, everything else copied."""
    return "_publish" in Path(src).parts


class FsUploadRepository:
    def __init__(self, root: Path):
        self.base = root / "uploads"
        self.base.mkdir(parents=True, exist_ok=True)

    def save(self, upload: Upload) -> None:
        write_json(self.base / upload.uploadId / "upload.json", upload.to_dict())

    def get(self, upload_id: str) -> Upload:
        f = self.base / upload_id / "upload.json"
        if not f.is_file():
            raise not_found("uploadId", upload_id)
        return Upload(**read_json(f))

    def write_chunk(self, upload: Upload, index: int, data: bytes) -> None:
        d = self.base / upload.uploadId / "chunks"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{index:06d}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, d / f"{index:06d}")

    def assemble(self, upload: Upload, dest: Path) -> str:
        dest.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        with open(dest, "wb") as out:
            for i in range(upload.chunk_count):
                b = (self.base / upload.uploadId / "chunks" / f"{i:06d}").read_bytes()
                h.update(b)
                out.write(b)
        return h.hexdigest()

    def discard_chunks(self, upload: Upload) -> None:
        shutil.rmtree(self.base / upload.uploadId / "chunks", ignore_errors=True)


class FsAvatarStore:
    def __init__(self, root: Path):
        self.base = root / "avatars"
        self.base.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, filename: str) -> tuple[str, dict]:
        digest = hashlib.sha256(data).hexdigest()
        avatar_id = f"av_{digest[:16]}"
        if (self.base / f"{avatar_id}.json").is_file():
            return avatar_id, read_json(self.base / f"{avatar_id}.json")["meta"]
        meta = vrm.read_meta(data)                 # raises AVATAR_INVALID
        tmp = self.base / f"{avatar_id}.vrm.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, self.base / f"{avatar_id}.vrm")
        write_json(self.base / f"{avatar_id}.json", {"avatarId": avatar_id, "sha256": digest, "filename": filename,
                                                     "meta": meta, "createdAt": now_iso()})
        return avatar_id, meta

    def get(self, avatar_id: str) -> tuple[Path, dict]:
        f = self.base / f"{avatar_id}.json"
        if not f.is_file():
            raise not_found("avatarId", avatar_id)
        return self.base / f"{avatar_id}.vrm", read_json(f)["meta"]


__all__ = ["FsJobRepository", "FsProjectRepository", "FsArtifactStore", "FsUploadRepository", "FsAvatarStore",
           "write_json", "read_json", "sha256_file", "VvlogError"]
