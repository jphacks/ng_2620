import hashlib
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from vvlog_server.config import Settings
from vvlog_server.main import create_app

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    """2 s, 360x640 portrait H.264 + AAC (made with ffmpeg)."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required")
    out = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


def make_client(tmp_path, planner=None, runner_factory=None, **settings):
    s = Settings.from_env({"VVLOG_API_TOKEN": TOKEN, "VVLOG_DATA_ROOT": str(tmp_path / "data")}, **settings)
    return TestClient(create_app(s, planner=planner, runner_factory=runner_factory))


@pytest.fixture
def client(tmp_path):
    from fake_pipeline import FakePipelineRunner
    with make_client(tmp_path, runner_factory=FakePipelineRunner) as c:
        yield c


def upload(client, project_id, path, chunk=None):
    data = path.read_bytes()
    r = client.post("/v1/uploads", headers=AUTH, json={"projectId": project_id, "kind": "source", "filename": path.name,
                                                       "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 201, r.text
    up = r.json()
    size = chunk or up["chunkSize"]
    for i in range(0, len(data), size):
        r = client.put(f"/v1/uploads/{up['uploadId']}/chunks/{i // size}", headers=AUTH, content=data[i:i + size])
        assert r.status_code == 200, r.text
    return up["uploadId"]
