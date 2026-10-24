"""Stages against FakePipelineRunner (real ingest with ffmpeg, recorded pipeline output for the rest)."""
import io
import json
import zipfile

import numpy as np
import pytest
from PIL import Image

from conftest import AUTH, make_client, upload
from fake_pipeline import FakePipelineRunner, fake_offline_app_lite
from test_api import new_project, sse, wait_job


def analyze(c, video):
    pid = new_project(c)
    media = c.post(f"/v1/uploads/{upload(c, pid, video)}/complete", headers=AUTH).json()
    job = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH,
                 json={"type": "analyze", "params": {"mediaId": media["mediaId"], "maxTargets": 3}}).json()
    return pid, job["jobId"]


def test_track_mesh(tmp_path, sample_video):
    FakePipelineRunner.calls.clear()
    with make_client(tmp_path, runner_factory=FakePipelineRunner) as c:
        _, jid = analyze(c, sample_video)
        j = wait_job(c, jid)
        assert j["status"] == "succeeded", j
        assert j["artifacts"] == ["meta", "proxy_frames", "persons", "person_thumbs", "track_masks",
                                  "mesh_preview", "units_index", "units"]
        assert c.get(f"/v1/jobs/{jid}/artifacts/units_index", headers=AUTH).json()["units"][0]["unitId"] == 1
        z = zipfile.ZipFile(io.BytesIO(c.get(f"/v1/jobs/{jid}/artifacts/units", headers=AUTH).content))
        u0 = np.array(Image.open(io.BytesIO(z.read("units/00000.png"))))
        assert u0.dtype == np.uint16 and u0.max() == 1 and len(z.namelist()) == 60
        persons = c.get(f"/v1/jobs/{jid}/artifacts/persons", headers=AUTH).json()
        assert [p["personId"] for p in persons] == [1, 2]
        assert persons[0]["firstFrame"] == 0 and persons[0]["lastFrame"] == 59 and persons[0]["visibleRatio"] == 1
        x, y, w, h = persons[0]["bbox"]
        assert y == 160 and h == 320 and w == 90
        z = zipfile.ZipFile(io.BytesIO(c.get(f"/v1/jobs/{jid}/artifacts/track_masks", headers=AUTH).content))
        m = np.array(Image.open(io.BytesIO(z.read("masks/00000.png"))))
        assert set(np.unique(m)) == {0, 1, 2}
        t = zipfile.ZipFile(io.BytesIO(c.get(f"/v1/jobs/{jid}/artifacts/person_thumbs", headers=AUTH).content))
        assert Image.open(io.BytesIO(t.read("person_thumbs/2.jpg"))).size == (256, 256)

        events = sse(c, jid)
        kinds = [(e["event"], json.loads(e["data"]).get("name" if e["event"] == "artifact_ready" else "stage"))
                 for e in events[1:]]
        # persons are out before the mesh stage starts
        assert kinds.index(("artifact_ready", "persons")) < kinds.index(("stage_started", "mesh"))
        prog = [json.loads(e["data"])["progress"] for e in events if e["event"] == "progress"
                and json.loads(e["data"])["stage"] == "track"]
        assert prog == sorted(prog) and prog[-1] == 1.0
        cmd, env = [c for c in FakePipelineRunner.calls if c[0][2].endswith("offline_app_lite.py")][-1]
        assert cmd[2].endswith("scripts/offline_app_lite.py") and env["BODY4D_MAX_TARGETS"] == "3"
        assert env["USE_TF"] == "0"


def test_no_person(tmp_path, sample_video):
    runner = lambda: FakePipelineRunner({"offline_app_lite.py": lambda cmd: fake_offline_app_lite(cmd, persons=())})
    with make_client(tmp_path, runner_factory=runner) as c:
        _, jid = analyze(c, sample_video)
        j = wait_job(c, jid)
        assert j["status"] == "failed" and j["error"]["code"] == "NO_PERSON_FOUND"
        assert [s["status"] for s in j["stages"]][:2] == ["done", "failed"]


def test_pipeline_crash(tmp_path, sample_video):
    def crash(cmd):
        return ["[STAGE][detect] time=1.00 s, peak_alloc=1.00 GB", "torch.OutOfMemoryError: CUDA out of memory."], 1
    with make_client(tmp_path, runner_factory=lambda: FakePipelineRunner({"offline_app_lite.py": crash})) as c:
        _, jid = analyze(c, sample_video)
        j = wait_job(c, jid)
        assert j["error"]["code"] == "GPU_OOM"
        failed = [e for e in sse(c, jid) if e["event"] == "job_failed"][0]
        assert "CUDA out of memory" in json.loads(failed["data"])["logTail"]
        assert "CUDA out of memory" in c.get(f"/v1/jobs/{jid}/logs", headers=AUTH).text
