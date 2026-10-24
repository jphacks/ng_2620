import json
import time

from conftest import AUTH, make_client, upload
from test_adapters import make_vrm


def new_project(client, name="demo"):
    r = client.post("/v1/projects", headers=AUTH, json={"name": name})
    assert r.status_code == 201
    return r.json()["projectId"]


def wait_job(client, job_id, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        if j["status"] in ("succeeded", "failed", "canceled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def sse(client, job_id, last_id=None):
    h = dict(AUTH, **({"Last-Event-ID": str(last_id)} if last_id else {}))
    out = []
    with client.stream("GET", f"/v1/jobs/{job_id}/events", headers=h) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        ev = {}
        for line in r.iter_lines():
            if not line:
                if ev:
                    out.append(ev)
                ev = {}
                continue
            k, _, v = line.partition(":")
            ev[k] = v.strip()
    return out


def test_health_and_auth(client):
    assert client.get("/v1/health").json() == {"status": "ok", "version": "0.1.0", "apiVersion": 1}
    r = client.get("/v1/capabilities")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    assert client.get("/v1/capabilities", headers={"Authorization": "Bearer nope"}).status_code == 401
    caps = client.get("/v1/capabilities", headers=AUTH).json()
    assert caps["limits"]["maxDurationSec"] == 30 and caps["queueLength"] == 0


def test_upload_and_job_flow(client, sample_video):
    pid = new_project(client)
    uid = upload(client, pid, sample_video, chunk=None)
    st = client.get(f"/v1/uploads/{uid}", headers=AUTH).json()
    assert st["receivedChunks"] == [0]
    r = client.post(f"/v1/uploads/{uid}/complete", headers=AUTH)
    assert r.status_code == 200, r.text
    media = r.json()
    assert media["meta"]["full"] == {"width": 360, "height": 640} and media["meta"]["hasAudio"]
    assert client.post(f"/v1/uploads/{uid}/complete", headers=AUTH).json() == media      # idempotent

    r = client.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "analyze",
                                                                   "params": {"mediaId": media["mediaId"]}})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] in ("queued", "running", "succeeded") and job["params"]["maxTargets"] == 4
    assert wait_job(client, job["jobId"])["status"] == "succeeded"

    events = sse(client, job["jobId"])
    assert events[0]["event"] == "snapshot" and "id" not in events[0]
    assert events[-1]["event"] == "job_finished" and json.loads(events[-1]["data"]) == {"status": "succeeded"}
    first_id = int(events[1]["id"])
    resumed = sse(client, job["jobId"], last_id=first_id)
    assert [e["id"] for e in resumed[1:]] == [e["id"] for e in events[2:]]

    proj = client.get(f"/v1/projects/{pid}", headers=AUTH).json()
    assert proj["media"][0]["mediaId"] == media["mediaId"] and proj["jobs"][0]["jobId"] == job["jobId"]
    assert [a["name"] for a in client.get(f"/v1/jobs/{job['jobId']}/artifacts", headers=AUTH).json()] == \
        ["meta", "proxy_frames"]
    assert client.get("/v1/queue", headers=AUTH).json() == {"items": []}


def test_upload_errors(client, sample_video, tmp_path):
    pid = new_project(client)
    data = sample_video.read_bytes()
    r = client.post("/v1/uploads", headers=AUTH, json={"projectId": pid, "kind": "source", "filename": "a.mp4",
                                                       "size": len(data), "sha256": "0" * 64})
    uid = r.json()["uploadId"]
    assert client.post(f"/v1/uploads/{uid}/complete", headers=AUTH).json()["error"]["code"] == "UPLOAD_INCOMPLETE"
    client.put(f"/v1/uploads/{uid}/chunks/0", headers=AUTH, content=data)
    r = client.post(f"/v1/uploads/{uid}/complete", headers=AUTH)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHECKSUM_MISMATCH"
    r = client.put(f"/v1/uploads/{uid}/chunks/0", headers=AUTH, content=data[:10])
    assert r.status_code == 422


def test_media_too_long(tmp_path, sample_video):
    with make_client(tmp_path, max_duration_sec=1.0) as c:
        pid = new_project(c)
        uid = upload(c, pid, sample_video)
        r = c.post(f"/v1/uploads/{uid}/complete", headers=AUTH)
        assert r.status_code == 422 and r.json()["error"]["code"] == "MEDIA_TOO_LONG"
        assert r.json()["error"]["detail"]["limitSec"] == 1.0


def test_avatars_and_job_validation(client):
    pid = new_project(client)
    files = {"file": ("a.vrm", make_vrm(), "application/octet-stream")}
    r = client.post("/v1/avatars", headers=AUTH, files=files)
    assert r.status_code == 201, r.text
    av = r.json()["avatarId"]
    assert client.get(f"/v1/avatars/{av}", headers=AUTH).json()["meta"]["name"] == "Sample"
    bad = client.post("/v1/avatars", headers=AUTH, files={"file": ("x.vrm", b"nope", "application/octet-stream")})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "AVATAR_INVALID"
    assert client.get("/v1/avatars/av_0000000000000000", headers=AUTH).status_code == 404

    r = client.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "analyze", "params": {"mediaId": "x"}})
    assert r.status_code == 404
    r = client.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json={"type": "render", "params": {"cast": []}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    r = client.post(f"/v1/projects/{pid}/jobs", headers=AUTH,
                    json={"type": "export", "params": {"renderJobId": "job_x", "settings": {"format": "prores4444"}}})
    assert r.status_code == 404                     # unknown render job comes before the format check
    assert client.post(f"/v1/projects/{pid}/units:prompt", headers=AUTH,
                       json={"frame": 1, "x": 0.5, "y": 0.5, "range": [0, 10]}).status_code == 501
    assert client.get("/v1/me", headers=AUTH).status_code == 501


def test_artifact_range(client, tmp_path):
    from vvlog_server.domain.job import Job
    deps = client.app.state.deps
    pid = new_project(client)
    job = Job.create(pid, "analyze", {"mediaId": "m"})
    deps.jobs.save(job)
    f = tmp_path / "preview.json"
    f.write_bytes(b"0123456789")
    deps.store.publish(job, "meta", f)
    url = f"/v1/jobs/{job.jobId}/artifacts/meta"
    assert client.get(url, headers=AUTH).content == b"0123456789"
    r = client.get(url, headers=dict(AUTH, Range="bytes=2-4"))
    assert r.status_code == 206 and r.content == b"234" and r.headers["content-range"] == "bytes 2-4/10"
    assert client.get(url, headers=dict(AUTH, Range="bytes=-3")).content == b"789"
    assert client.get(url, headers=dict(AUTH, Range="bytes=20-")).status_code == 416


def test_ingest_stage(client, sample_video):
    pid = new_project(client)
    uid = upload(client, pid, sample_video)
    media = client.post(f"/v1/uploads/{uid}/complete", headers=AUTH).json()
    job = client.post(f"/v1/projects/{pid}/jobs", headers=AUTH,
                      json={"type": "analyze", "params": {"mediaId": media["mediaId"]}}).json()
    j = wait_job(client, job["jobId"])
    assert j["status"] == "succeeded", j
    assert j["artifacts"] == ["meta", "proxy_frames"]
    meta = client.get(f"/v1/jobs/{job['jobId']}/artifacts/meta", headers=AUTH).json()
    assert meta["frameCount"] == 60 and meta["proxy"] == {"width": 360, "height": 640} and meta["proxyScale"] == 1
    work = client.app.state.deps.projects.root(pid) / "work" / job["jobId"]
    assert len(list((work / "full").glob("*.jpg"))) == 60 and (work / "audio.m4a").stat().st_size > 0
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/v1/jobs/{job['jobId']}/artifacts/proxy_frames", headers=AUTH).content))
    assert z.namelist()[:2] == ["frames/00000.jpg", "frames/00001.jpg"] and len(z.namelist()) == 60
