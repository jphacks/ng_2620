"""VVLOG_RECORD_FIXTURE=<dir>: every succeeded job is written as a mock-server fixture
(designdocs/features/mock-server-fixtures):

  <dir>/manifest.json                 {name, source, license, jobs: {analyze, render, export}}
  <dir>/jobs/<type>/events.jsonl      the job's events as published (with their times)
  <dir>/jobs/<type>/artifacts/...     its artifacts under their contract file names
The latest job of each type wins. VRM files are never copied (only rendered layers are)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..domain.artifact import CATALOG
from ..domain.job import Job


class FixtureRecorder:
    def __init__(self, root: Path, bus, store, projects):
        self.root, self.bus, self.store, self.projects = Path(root), bus, store, projects

    def record(self, job: Job) -> None:
        d = self.root / "jobs" / job.type
        if d.exists():
            shutil.rmtree(d)
        (d / "artifacts").mkdir(parents=True)
        with open(d / "events.jsonl", "w", encoding="utf-8") as f:
            for e in self.bus.history(job.jobId):
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        for a in self.store.list(job.jobId):
            path, _ = self.store.open(job.jobId, a.name)
            shutil.copyfile(path, d / "artifacts" / CATALOG[a.name][2])
        mf = self.root / "manifest.json"
        m = json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else {
            "name": self.root.name, "license": "team recording; VRM not included", "jobs": {}}
        m["jobs"][job.type] = {"jobId": job.jobId, "params": job.params}
        if job.type == "analyze":
            media = self.projects.get(job.projectId)["media"].get(job.params["mediaId"], {})
            m["source"] = media.get("meta")
        mf.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
