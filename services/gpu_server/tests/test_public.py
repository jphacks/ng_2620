"""Running the server where other people can reach it (#36): limits, queue cap, TTL purge, CORS, token strength."""
import asyncio
import hashlib
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import AUTH, TOKEN, make_client, upload
from vvlog_server.application.usecases.maintenance import purge_expired
from vvlog_server.config import Settings
from vvlog_server.main import create_app, weak_token_reason

PAGES = "https://me.github.io"


def new_project(c):
    return c.post("/v1/projects", headers=AUTH, json={"name": "p"}).json()["projectId"]


def start_upload(c, pid, size):
    return c.post("/v1/uploads", headers=AUTH, json={"projectId": pid, "kind": "source", "filename": "a.mp4",
                                                     "size": size, "sha256": hashlib.sha256(b"x").hexdigest()})


# --- upload size ------------------------------------------------------------------------------------------------------
def test_upload_size_limit_comes_from_the_settings(tmp_path):
    with make_client(tmp_path, max_upload_bytes=1000) as c:
        pid = new_project(c)
        assert start_upload(c, pid, 1000).status_code == 201
        r = start_upload(c, pid, 1001)
        assert r.status_code == 422 and r.json()["error"]["code"] == "MEDIA_TOO_LARGE"
        assert r.json()["error"]["detail"] == {"size": 1001, "limit": 1000}
        assert start_upload(c, pid, 0).status_code == 422


def test_default_upload_limit_is_unchanged(tmp_path):
    with make_client(tmp_path) as c:
        assert Settings.from_env({}).max_upload_bytes == 4 * 1024 ** 3
        assert start_upload(c, new_project(c), 4 * 1024 ** 3 + 1).status_code == 422


# --- queue cap --------------------------------------------------------------------------------------------------------
class Hang:
    """A pipeline that never finishes on its own, so jobs stay running / queued."""
    def __init__(self, *_a, **_k):
        self.stopped = asyncio.Event()

    async def run(self, cmd, env, cwd, on_line, log_path):
        await self.stopped.wait()
        return -15

    async def terminate(self):
        self.stopped.set()


def test_queue_cap_refuses_extra_jobs_with_429(tmp_path, sample_video):
    with make_client(tmp_path, runner_factory=Hang, max_queued_jobs=2) as c:
        pid = new_project(c)
        media = c.post(f"/v1/uploads/{upload(c, pid, sample_video)}/complete", headers=AUTH).json()
        body = {"type": "analyze", "params": {"mediaId": media["mediaId"]}}
        ids = []
        for _ in range(2):
            r = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json=body)
            assert r.status_code == 201, r.text
            ids.append(r.json()["jobId"])
        r = c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json=body)
        assert r.status_code == 429 and r.json()["error"]["code"] == "QUEUE_FULL"
        assert r.json()["error"]["detail"]["limit"] == 2
        # a slot frees up when a job is canceled
        assert c.post(f"/v1/jobs/{ids[1]}/cancel", headers=AUTH).status_code == 200
        assert c.post(f"/v1/projects/{pid}/jobs", headers=AUTH, json=body).status_code == 201


def test_queue_cap_zero_means_unlimited(tmp_path, sample_video):
    with make_client(tmp_path, runner_factory=Hang, max_queued_jobs=0) as c:
        pid = new_project(c)
        media = c.post(f"/v1/uploads/{upload(c, pid, sample_video)}/complete", headers=AUTH).json()
        for _ in range(4):
            assert c.post(f"/v1/projects/{pid}/jobs", headers=AUTH,
                          json={"type": "analyze", "params": {"mediaId": media["mediaId"]}}).status_code == 201


# --- TTL purge --------------------------------------------------------------------------------------------------------
def age(path, hours):
    t = time.time() - hours * 3600
    os.utime(path, (t, t))


def test_purge_removes_old_finished_projects_with_their_jobs_and_old_uploads(tmp_path, sample_video):
    from fake_pipeline import FakePipelineRunner
    from test_api import wait_job
    with make_client(tmp_path, runner_factory=FakePipelineRunner) as c:
        deps = c.app.state.deps
        old, fresh = new_project(c), new_project(c)
        media = c.post(f"/v1/uploads/{upload(c, old, sample_video)}/complete", headers=AUTH).json()
        job = c.post(f"/v1/projects/{old}/jobs", headers=AUTH,
                     json={"type": "analyze", "params": {"mediaId": media["mediaId"]}}).json()
        wait_job(c, job["jobId"])
        stale_upload = upload(c, fresh, sample_video)       # chunks lying around, never completed
        # make "old" and the stale upload look 3 days old
        later = datetime.now(timezone.utc) + timedelta(days=3)
        res = purge_expired(deps, 48, now=later)
        assert set(res["projects"]) == {old, fresh}                                # both are > 48h old by `later`
        assert stale_upload in res["uploads"]
        assert not (deps.projects.base / old).exists()
        assert not (deps.jobs.root / job["jobId"]).exists()
        assert c.get(f"/v1/projects/{old}", headers=AUTH).status_code == 404
        assert c.get(f"/v1/jobs/{job['jobId']}", headers=AUTH).status_code == 404


def test_purge_keeps_recent_work_and_anything_unfinished(tmp_path, sample_video):
    with make_client(tmp_path, runner_factory=Hang) as c:
        deps = c.app.state.deps
        busy, idle = new_project(c), new_project(c)
        media = c.post(f"/v1/uploads/{upload(c, busy, sample_video)}/complete", headers=AUTH).json()
        c.post(f"/v1/projects/{busy}/jobs", headers=AUTH, json={"type": "analyze", "params": {"mediaId": media["mediaId"]}})
        far = datetime.now(timezone.utc) + timedelta(days=30)
        res = purge_expired(deps, 24, now=far)
        assert busy not in res["projects"]                    # a job is queued / running
        assert (deps.projects.base / busy).exists()
        # not old enough yet
        assert purge_expired(deps, 24, now=datetime.now(timezone.utc) + timedelta(hours=23))["projects"] == []
        assert idle not in purge_expired(deps, 24, now=datetime.now(timezone.utc) + timedelta(hours=23))["projects"]


def test_purge_disabled_by_default(tmp_path):
    with make_client(tmp_path) as c:
        pid = new_project(c)
        far = datetime.now(timezone.utc) + timedelta(days=3650)
        assert purge_expired(c.app.state.deps, 0, now=far) == {"projects": [], "uploads": []}
        assert Settings.from_env({}).artifact_ttl_hours == 0
        assert (c.app.state.deps.projects.base / pid).exists()


def test_housekeeping_runs_at_startup_when_a_ttl_is_set(tmp_path):
    with make_client(tmp_path) as c:
        pid = new_project(c)
        age(c.app.state.deps.projects.base / pid / "project.json", 100)
    with make_client(tmp_path, artifact_ttl_hours=48) as c2:
        for _ in range(100):
            if c2.get(f"/v1/projects/{pid}", headers=AUTH).status_code == 404:
                break
            time.sleep(0.05)
        assert c2.get(f"/v1/projects/{pid}", headers=AUTH).status_code == 404


# --- CORS -------------------------------------------------------------------------------------------------------------
def make_remote(tmp_path, origins):
    env = {"VVLOG_API_TOKEN": TOKEN, "VVLOG_DATA_ROOT": str(tmp_path / "data"), "VVLOG_ALLOWED_ORIGINS": origins}
    from fastapi.testclient import TestClient
    return TestClient(create_app(Settings.from_env(env)))


def test_cors_allows_the_configured_origin_and_caches_the_preflight(tmp_path):
    with make_remote(tmp_path, f"{PAGES}, https://other.example") as c:
        pre = c.options("/v1/projects", headers={"Origin": PAGES, "Access-Control-Request-Method": "POST",
                                                 "Access-Control-Request-Headers": "authorization,content-type"})
        assert pre.status_code == 200
        assert pre.headers["access-control-allow-origin"] == PAGES
        assert "authorization" in pre.headers["access-control-allow-headers"].lower()
        assert pre.headers["access-control-max-age"] == "600"
        r = c.get("/v1/health", headers={"Origin": "https://other.example"})
        assert r.headers["access-control-allow-origin"] == "https://other.example"


def test_cors_refuses_origins_that_are_not_listed(tmp_path):
    with make_remote(tmp_path, PAGES) as c:
        pre = c.options("/v1/projects", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        assert pre.status_code == 400 and "access-control-allow-origin" not in pre.headers
        assert "access-control-allow-origin" not in c.get("/v1/health", headers={"Origin": "https://evil.example"}).headers


def test_cors_exposes_range_headers_for_cross_origin_downloads(tmp_path):
    with make_remote(tmp_path, PAGES) as c:
        r = c.get("/v1/health", headers={"Origin": PAGES})
        exposed = r.headers["access-control-expose-headers"].lower()
        assert "content-range" in exposed and "accept-ranges" in exposed


# --- token strength ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("token", ["", "short", "123456789012345", "a" * 40, "password", "test-token",
                                   "abababababababababab", "changeme"])
def test_weak_tokens_are_rejected(token):
    assert weak_token_reason(token)


@pytest.mark.parametrize("token", ["0123456789abcdef0123456789abcdef", "e7f3b1c4a9d2e8f60a1b2c3d4e5f6071", "correct-horse-battery-staple-9"])
def test_strong_tokens_pass(token):
    assert weak_token_reason(token) is None


def test_main_refuses_a_weak_token_unless_overridden(monkeypatch, capsys):
    import uvicorn
    from vvlog_server import main as M
    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(k))
    monkeypatch.setattr(M, "create_app", lambda s: object())
    monkeypatch.setenv("VVLOG_API_TOKEN", "short")
    monkeypatch.delenv("VVLOG_LOCAL", raising=False)
    monkeypatch.delenv("VVLOG_ALLOW_WEAK_TOKEN", raising=False)
    with pytest.raises(SystemExit) as e:
        M.main()
    assert "弱い" in str(e.value) and started == []
    monkeypatch.setenv("VVLOG_ALLOW_WEAK_TOKEN", "1")
    M.main()
    assert started and started[0]["host"] == "127.0.0.1"
    monkeypatch.setenv("VVLOG_ALLOW_WEAK_TOKEN", "")
    monkeypatch.setenv("VVLOG_API_TOKEN", "0123456789abcdef0123456789abcdef")
    started.clear()
    M.main()
    assert started
    monkeypatch.delenv("VVLOG_API_TOKEN")
    with pytest.raises(SystemExit):
        M.main()
