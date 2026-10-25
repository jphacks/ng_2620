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
        assert cmd[2].endswith("scripts/offline_app_lite.py") and cmd[cmd.index("--max_targets") + 1] == "3"
        assert cmd[cmd.index("--hmr_stride") + 1] == "3" and cmd[cmd.index("--tracker_size") + 1] == "672"
        assert env["USE_TF"] == "0" and env["MPLBACKEND"] == "Agg"


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


def test_render(tmp_path, sample_video):
    from test_adapters import make_vrm
    FakePipelineRunner.calls.clear()
    with make_client(tmp_path, runner_factory=FakePipelineRunner) as c:
        pid, aid = analyze(c, sample_video)
        av = c.post("/v1/avatars", headers=AUTH, files={"file": ("a.vrm", make_vrm(), "x")}).json()["avatarId"]
        bad = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "render", "params": {
            "analyzeJobId": aid, "cast": [{"personId": 7, "action": "replace", "avatarId": av}]}}).json()
        r = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "render", "params": {
            "analyzeJobId": aid, "cast": [{"personId": 2, "action": "replace", "avatarId": av}],
            "finish": {"footLock": False}}}).json()
        assert wait_job(c, aid)["status"] == "succeeded"
        jb = wait_job(c, bad["jobId"])
        assert jb["status"] == "failed" and jb["error"]["code"] == "VALIDATION_FAILED"
        j = wait_job(c, r["jobId"])
        assert j["status"] == "succeeded", j
        assert j["artifacts"] == ["layers_index", "layers", "preview_video", "stats"]
        z = zipfile.ZipFile(io.BytesIO(c.get(f"/v1/jobs/{r['jobId']}/artifacts/layers", headers=AUTH).content))
        assert "clean/00000.webp" in z.namelist() and "avatar/2/00059.webp" in z.namelist()
        assert "hide/2/00000.png" in z.namelist() and "layers.json" not in z.namelist()
        lj = c.get(f"/v1/jobs/{r['jobId']}/artifacts/layers_index", headers=AUTH).json()
        assert lj["avatarIds"] == {"2": av}
        pv = tmp_path / "preview.mp4"
        pv.write_bytes(c.get(f"/v1/jobs/{r['jobId']}/artifacts/preview_video", headers=AUTH).content)
        import subprocess
        wh = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height,nb_frames",
                             "-of", "csv=p=0", str(pv)], capture_output=True, text=True).stdout.strip()
        assert wh == "360,640,60"                       # the proxy size, every frame
        cmd = [x for x, _ in FakePipelineRunner.calls if x[2].endswith("avatar_app.py")][-1]
        assert cmd[cmd.index("--targets") + 1] == "2" and "--no_foot_lock" in cmd and "--no_spring" not in cmd
        assert cmd[cmd.index("--fusion_state") + 1].endswith(f"{aid}/occ/fusion_state.pkl")
        assert cmd[cmd.index("--vrm_map") + 1].startswith("2=") and cmd[cmd.index("--fps") + 1] == "30.0"
        assert "--no_compare" in cmd and "--no_frames" not in cmd   # frames/ is the preview's source
        stages = [s["status"] for s in j["stages"]]
        assert stages == ["done", "done", "done"]


def test_export(tmp_path, sample_video):
    from test_adapters import make_vrm
    FakePipelineRunner.calls.clear()
    with make_client(tmp_path, runner_factory=FakePipelineRunner) as c:
        pid, aid = analyze(c, sample_video)
        av = c.post("/v1/avatars", headers=AUTH, files={"file": ("a.vrm", make_vrm(), "x")}).json()["avatarId"]
        r = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "render", "params": {
            "analyzeJobId": aid, "cast": [{"personId": 1, "action": "replace", "avatarId": av}]}}).json()
        ovr = [{"id": "ovr_01", "kind": "unit", "personId": 1, "unitId": 1, "range": [5, 20], "mode": "front"},
               {"id": "ovr_02", "kind": "brush", "personId": 1, "frame": 3, "mask": "x", "mode": "behind"}]
        e = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "export", "params": {
            "renderJobId": r["jobId"], "overrides": ovr, "settings": {"format": "mp4_h264", "crf": 20}}}).json()
        assert e["dependsOn"] == r["jobId"]
        j = wait_job(c, e["jobId"])
        assert j["status"] == "succeeded", j
        assert j["artifacts"] == ["export_video"]
        work = c.app.state.deps.projects.root(pid) / "work" / e["jobId"]
        items = json.loads((work / "ex" / "overrides.json").read_text())["items"]
        assert items == [{"personId": 1, "tracks": [3], "range": [5, 20], "mode": "front", "id": "ovr_01"}]
        cmd = [x for x, _ in FakePipelineRunner.calls if x[2].endswith("avatar_app.py") and "--overrides" in x][-1]
        assert "--hq" not in cmd                       # the sample is 360x640: its proxy is the full resolution
        assert "--no_compare" in cmd and "--no_frames" in cmd   # only avatar_<job>.mp4 is used
        data = c.get(f"/v1/jobs/{e['jobId']}/artifacts/export_video", headers=AUTH).content
        out = tmp_path / "export.mp4"
        out.write_bytes(data)
        import subprocess
        probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(out)],
                                          capture_output=True, text=True).stdout)
        kinds = {s["codec_type"]: s for s in probe["streams"]}
        assert kinds["video"]["codec_name"] == "h264" and kinds["video"]["pix_fmt"] == "yuv420p"
        assert kinds["audio"]["codec_name"] == "aac"


def test_export_rejects_unknown_unit(tmp_path, sample_video):
    from test_adapters import make_vrm
    with make_client(tmp_path, runner_factory=FakePipelineRunner) as c:
        pid, aid = analyze(c, sample_video)
        av = c.post("/v1/avatars", headers=AUTH, files={"file": ("a.vrm", make_vrm(), "x")}).json()["avatarId"]
        r = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "render", "params": {
            "analyzeJobId": aid, "cast": [{"personId": 1, "action": "replace", "avatarId": av}]}}).json()
        e = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "export", "params": {
            "renderJobId": r["jobId"], "overrides": [{"id": "o", "kind": "unit", "personId": 1, "unitId": 99,
                                                     "range": [0, 1], "mode": "front"}]}}).json()
        j = wait_job(c, e["jobId"])
        assert j["status"] == "failed" and j["error"]["code"] == "VALIDATION_FAILED"


def test_record_fixture_replays_on_the_mock(tmp_path, sample_video):
    import sys
    from pathlib import Path
    fx = tmp_path / "fx"
    with make_client(tmp_path, runner_factory=FakePipelineRunner, record_fixture=fx) as c:
        _, jid = analyze(c, sample_video)
        assert wait_job(c, jid)["status"] == "succeeded"
    m = json.loads((fx / "manifest.json").read_text())
    assert m["jobs"]["analyze"]["jobId"] == jid and m["source"]["frameCount"] == 60
    assert (fx / "jobs" / "analyze" / "artifacts" / "persons.json").is_file()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mock_server"))
    from mock_server.__main__ import build
    from fastapi.testclient import TestClient
    with TestClient(build({"VVLOG_DATA_ROOT": str(tmp_path / "mock"), "VVLOG_MOCK_SPEED": "1000",
                           "VVLOG_MOCK_FIXTURE": str(fx)})) as mc:
        mauth = {"Authorization": "Bearer mock"}
        pid = mc.post("/v1/projects", headers=mauth, json={"name": "m"}).json()["projectId"]
        media = mc.post(f"/v1/uploads/{upload(mc, pid, sample_video, headers=mauth)}/complete", headers=mauth).json()
        j = mc.post(f"/v1/projects/{pid}/jobs", headers=mauth,
                    json={"type": "analyze", "params": {"mediaId": media["mediaId"]}}).json()
        import time
        for _ in range(200):
            s = mc.get(f"/v1/jobs/{j['jobId']}", headers=mauth).json()
            if s["status"] == "succeeded":
                break
            time.sleep(0.05)
        assert s["status"] == "succeeded" and "units" in s["artifacts"]
        assert mc.get(f"/v1/jobs/{j['jobId']}/artifacts/persons", headers=mauth).json()[0]["personId"] == 1
