import hashlib
import json
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gpu_server" / "tests"))
from mock_server.__main__ import build  # noqa: E402
from test_adapters import make_vrm  # noqa: E402

AUTH = {"Authorization": "Bearer mock"}


def run_flow(c):
    pid = c.post("/v1/projects", headers=AUTH, json={"name": "m"}).json()["projectId"]
    a = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "analyze", "params": {"mediaId": "x"}})
    assert a.status_code == 404                       # media must exist even on the mock
    data = b"\x00" * 1000
    up = c.post("/v1/uploads", headers=AUTH, json={"projectId": pid, "kind": "source", "filename": "a.mp4",
                                                   "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return pid, up.json()["uploadId"], data


def wait(c, jid, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/v1/jobs/{jid}", headers=AUTH).json()
        if j["status"] in ("succeeded", "failed", "canceled"):
            return j
        time.sleep(0.05)
    raise AssertionError("timeout")


@pytest.fixture
def mock(tmp_path):
    def make(**env):
        e = {"VVLOG_DATA_ROOT": str(tmp_path / "d"), "VVLOG_MOCK_SPEED": "200", **env}
        return TestClient(build(e))
    return make


def _seed_media(c, pid):
    """The mock still validates uploads with ffprobe; tests put a media entry in directly."""
    deps = c.app.state.deps
    p = deps.projects.get(pid)
    p["media"]["med_1"] = {"mediaId": "med_1", "filename": "a.mp4", "size": 1, "sha256": "0" * 64, "path": "x",
                           "meta": {"durationSec": 2.0, "frameCount": 60, "fps": 30.0,
                                    "full": {"width": 1080, "height": 1920}, "proxy": {"width": 540, "height": 960},
                                    "proxyScale": 2, "hasAudio": True, "sourceCodec": "h264"}, "createdAt": "x"}
    deps.projects.save(p)


def test_synthetic_full_flow(mock):
    with mock() as c:
        pid, _, _ = run_flow(c)
        _seed_media(c, pid)
        a = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "analyze",
                                                                   "params": {"mediaId": "med_1"}}).json()
        av = c.post("/v1/avatars", headers=AUTH, files={"file": ("a.vrm", make_vrm(), "x")}).json()["avatarId"]
        r = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "render", "params": {
            "analyzeJobId": a["jobId"], "cast": [{"personId": 1, "action": "replace", "avatarId": av}]}}).json()
        e = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "export", "params": {
            "renderJobId": r["jobId"], "overrides": [], "settings": {"format": "mp4_h264"}}}).json()
        assert r["status"] == "queued" and r["dependsOn"] == a["jobId"]
        for j in (a, r, e):
            assert wait(c, j["jobId"])["status"] == "succeeded"
        names = [x["name"] for x in c.get(f"/v1/jobs/{a['jobId']}/artifacts", headers=AUTH).json()]
        assert set(names) == {"meta", "proxy_frames", "persons", "person_thumbs", "track_masks", "mesh_preview",
                              "units_index", "units"}
        persons = c.get(f"/v1/jobs/{a['jobId']}/artifacts/persons", headers=AUTH).json()
        assert [p["personId"] for p in persons] == [1, 2]
        z = zipfile.ZipFile(BytesIO(c.get(f"/v1/jobs/{a['jobId']}/artifacts/proxy_frames", headers=AUTH).content))
        assert len(z.namelist()) == 60 and z.namelist()[0] == "frames/00000.jpg"
        lj = c.get(f"/v1/jobs/{r['jobId']}/artifacts/layers_index", headers=AUTH).json()
        assert lj["persons"] == [1] and lj["avatarIds"] == {"1": av}
        assert c.get(f"/v1/jobs/{e['jobId']}/artifacts/export_video", headers=AUTH).status_code == 200
        assert c.get("/v1/capabilities", headers=AUTH).json()["backend"] == "replay"


def test_mock_fail(mock):
    with mock(VVLOG_MOCK_FAIL="track:NO_PERSON_FOUND") as c:
        pid, _, _ = run_flow(c)
        _seed_media(c, pid)
        a = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "analyze",
                                                                   "params": {"mediaId": "med_1"}}).json()
        j = wait(c, a["jobId"])
        assert j["status"] == "failed" and j["error"]["code"] == "NO_PERSON_FOUND"
        assert [s["status"] for s in j["stages"]] == ["done", "failed", "pending", "pending"]
        assert "meta" in j["artifacts"]


def test_replay_fixture(mock, tmp_path):
    fx = tmp_path / "fx"
    d = fx / "jobs" / "analyze"
    (d / "artifacts").mkdir(parents=True)
    (d / "artifacts" / "meta.json").write_text(json.dumps({"x": 1}))
    ev = [("stage_started", {"stage": "ingest"}), ("artifact_ready", {"name": "meta"}),
          ("stage_finished", {"stage": "ingest"})]
    (d / "events.jsonl").write_text("\n".join(json.dumps({"id": i + 1, "event": k, "data": v,
                                                          "ts": f"2026-10-24T00:00:0{i}.000Z"})
                                              for i, (k, v) in enumerate(ev)))
    with mock(VVLOG_MOCK_FIXTURE=str(fx)) as c:
        pid, _, _ = run_flow(c)
        _seed_media(c, pid)
        a = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "analyze",
                                                                   "params": {"mediaId": "med_1"}}).json()
        assert wait(c, a["jobId"])["status"] == "succeeded"
        assert c.get(f"/v1/jobs/{a['jobId']}/artifacts/meta", headers=AUTH).json() == {"x": 1}
