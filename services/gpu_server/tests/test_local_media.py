"""/v1/local/media/probe and /v1/local/hash/sha256 (#24)."""
import hashlib
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH, TOKEN
from vvlog_server.adapters.local_media import LocalMedia, parse_probe
from vvlog_server.config import Settings, find_ffprobe
from vvlog_server.domain.errors import VvlogError
from vvlog_server.main import create_app

PORT = 54323
SELF = f"http://127.0.0.1:{PORT}"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe is required")


def make_client(tmp_path, **env):
    (tmp_path / "dist").mkdir(exist_ok=True)
    base = {"VVLOG_API_TOKEN": TOKEN, "VVLOG_DATA_ROOT": str(tmp_path / "data"), "VVLOG_LOCAL": "1",
            "VVLOG_PORT": str(PORT), "VVLOG_SPA_DIR": str(tmp_path / "dist"),
            "VVLOG_LOCAL_PROJECTS_DIR": str(tmp_path / "p"), "VVLOG_LOCAL_APPDATA_DIR": str(tmp_path / "a"),
            "VVLOG_LOCAL_CONFIG_DIR": str(tmp_path / "c")}
    c = TestClient(create_app(Settings.from_env({**base, **env})), base_url=SELF)
    c.headers.update(AUTH)
    return c


@pytest.fixture
def local(tmp_path):
    with make_client(tmp_path) as c:
        yield c


def stream(video, **kw):
    return {"codec_type": "video", "codec_name": "h264", "width": 360, "height": 640,
            "avg_frame_rate": "30/1", "r_frame_rate": "30/1", **video, **kw}


# --- parse_probe (the cases of media.rs) ---------------------------------------------------------------------------------
def test_parse_basic():
    d = {"streams": [stream({}), {"codec_type": "audio"}], "format": {"duration": "2.000000"}}
    assert parse_probe(d, 123) == {"durationSec": 2.0, "width": 360, "height": 640, "rotation": 0, "codec": "h264",
                                   "fps": 30.0, "hasAudio": True, "sizeBytes": 123}


def test_parse_fps_variants():
    f = lambda **kw: parse_probe({"streams": [stream({}, **kw)]}, 1)["fps"]  # noqa: E731
    assert f(avg_frame_rate="30000/1001") == pytest.approx(29.97, abs=0.01)
    assert f(avg_frame_rate="0/0", r_frame_rate="25/1") == 25.0          # falls back to r_frame_rate
    assert f(avg_frame_rate="0/0", r_frame_rate="0/0") is None
    assert f(avg_frame_rate="N/A", r_frame_rate="30/1") == 30.0


@pytest.mark.parametrize("video,expected", [
    ({"tags": {"rotate": "90"}}, 90),
    ({"tags": {"rotate": "-90"}}, 270),
    ({"side_data_list": [{"rotation": -90}]}, 90),          # display matrix is counter-clockwise: -90 means 90 cw
    ({"side_data_list": [{"rotation": 90}]}, 270),
    ({"side_data_list": [{"side_data_type": "Other"}, {"rotation": -180.0}]}, 180),
    ({"side_data_list": [{"rotation": "-90.0"}]}, 90),
    ({"tags": {"rotate": "90"}, "side_data_list": [{"rotation": 90}]}, 90),   # the tag wins
    ({}, 0),
])
def test_parse_rotation(video, expected):
    assert parse_probe({"streams": [stream(video)]}, 1)["rotation"] == expected


def test_parse_duration_falls_back_to_the_stream():
    assert parse_probe({"streams": [stream({"duration": "3.5"})], "format": {}}, 1)["durationSec"] == 3.5
    assert parse_probe({"streams": [stream({})], "format": {}}, 1)["durationSec"] == 0.0


def test_parse_without_a_video_track():
    with pytest.raises(VvlogError) as e:
        parse_probe({"streams": [{"codec_type": "audio"}]}, 1)
    assert e.value.code == "MEDIA_UNSUPPORTED"


# --- the endpoint with a real ffprobe ------------------------------------------------------------------------------------
@needs_ffmpeg
def test_probe_a_real_video(local, sample_video):
    r = local.post("/v1/local/media/probe", json={"path": str(sample_video)})
    assert r.status_code == 200
    b = r.json()
    assert (b["width"], b["height"], b["rotation"], b["codec"], b["hasAudio"]) == (360, 640, 0, "h264", True)
    assert b["durationSec"] == pytest.approx(2.0, abs=0.2) and b["fps"] == pytest.approx(30.0)
    assert b["sizeBytes"] == sample_video.stat().st_size


@needs_ffmpeg
def test_probe_reads_the_display_matrix_rotation(local, sample_video, tmp_path):
    out = tmp_path / "rot90.mp4"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(sample_video), "-c", "copy",
                        str(out)], capture_output=True)
    if r.returncode:
        pytest.skip("this ffmpeg has no -display_rotation")
    assert local.post("/v1/local/media/probe", json={"path": str(out)}).json()["rotation"] == 270


@needs_ffmpeg
def test_probe_of_a_non_video_file(local, tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"this is not a video")
    r = local.post("/v1/local/media/probe", json={"path": str(f)})
    assert r.status_code == 422 and r.json()["error"]["code"] == "MEDIA_UNSUPPORTED"


@needs_ffmpeg
def test_probe_audio_only_has_no_video_track(local, tmp_path):
    f = tmp_path / "a.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", str(f)], check=True)
    r = local.post("/v1/local/media/probe", json={"path": str(f)})
    assert r.status_code == 422 and "映像トラック" in r.json()["error"]["message"]


def test_probe_without_ffprobe_tells_the_app_to_fall_back(tmp_path, sample_video):
    with make_client(tmp_path, VVLOG_FFPROBE=str(tmp_path / "no-such-ffprobe")) as c:
        r = c.post("/v1/local/media/probe", json={"path": str(sample_video)})
        assert r.status_code == 503 and r.json()["error"]["code"] == "FFPROBE_UNAVAILABLE"


@needs_ffmpeg
def test_a_configured_ffprobe_is_used(tmp_path, sample_video):
    wrapper = tmp_path / "ffprobe-wrapper"
    wrapper.write_text(f"#!/bin/sh\necho used >> {tmp_path / 'calls.txt'}\nexec {shutil.which('ffprobe')} \"$@\"\n")
    wrapper.chmod(0o755)
    with make_client(tmp_path, VVLOG_FFPROBE=str(wrapper)) as c:
        assert c.post("/v1/local/media/probe", json={"path": str(sample_video)}).status_code == 200
    assert (tmp_path / "calls.txt").read_text().strip() == "used"


def test_find_ffprobe_prefers_the_bundled_copy(tmp_path, monkeypatch):
    import os
    name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    (tmp_path / name).write_text("")
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ffprobe")
    assert find_ffprobe(tmp_path) == str(tmp_path / name)
    assert find_ffprobe(tmp_path / "empty") == "/usr/bin/ffprobe"        # falls back to PATH
    assert find_ffprobe(None, str(tmp_path / "missing")) is None          # an explicit setting is not second-guessed


@needs_ffmpeg
def test_probe_does_not_follow_non_file_protocols(local, tmp_path):
    playlist = tmp_path / "evil.m3u8"
    playlist.write_text("#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:9/never.ts\n#EXT-X-ENDLIST\n")
    r = local.post("/v1/local/media/probe", json={"path": str(playlist)})
    assert r.status_code in (422, 503) and r.status_code != 200


# --- path rules (shared by probe and hash) -------------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["", "relative.mp4", "http://example.com/a.mp4", "concat:a.mp4|b.mp4", "-version"])
@pytest.mark.parametrize("url", ["/v1/local/media/probe", "/v1/local/hash/sha256"])
def test_only_absolute_file_paths_are_accepted(local, url, path):
    assert local.post(url, json={"path": path}).status_code == 403


@pytest.mark.parametrize("url", ["/v1/local/media/probe", "/v1/local/hash/sha256"])
def test_missing_file_and_directory_are_404(local, tmp_path, url):
    assert local.post(url, json={"path": str(tmp_path / "nope.mp4")}).status_code == 404
    assert local.post(url, json={"path": str(tmp_path)}).status_code == 404


@pytest.mark.parametrize("url", ["/v1/local/media/probe", "/v1/local/hash/sha256"])
def test_token_required(local, tmp_path, url):
    assert local.post(url, json={"path": str(tmp_path)}, headers={"Authorization": ""}).status_code == 401


# --- sha256 ----------------------------------------------------------------------------------------------------------------
def test_sha256_known_answer(local, tmp_path):
    f = tmp_path / "abc.bin"
    f.write_bytes(b"abc")
    r = local.post("/v1/local/hash/sha256", json={"path": str(f)})
    assert r.json() == {"sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}


def test_sha256_of_a_large_file_matches_hashlib(local, tmp_path):
    import os
    f = tmp_path / "big.bin"
    data = os.urandom(9 << 20)
    f.write_bytes(data)
    assert local.post("/v1/local/hash/sha256", json={"path": str(f)}).json()["sha256"] == hashlib.sha256(data).hexdigest()


def test_sha256_streams_progress_then_done(local, tmp_path):
    import os
    f = tmp_path / "big.bin"
    data = os.urandom(20 << 20)
    f.write_bytes(data)
    r = local.post("/v1/local/hash/sha256", json={"path": str(f)}, headers={"Accept": "text/event-stream"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events, cur = [], {}
    for line in r.text.splitlines():
        if line.startswith("event:"):
            cur = {"event": line.split(":", 1)[1].strip()}
        elif line.startswith("data:"):
            cur["data"] = json.loads(line.split(":", 1)[1])
            events.append(cur)
    ratios = [e["data"]["ratio"] for e in events if e["event"] == "progress"]
    assert len(ratios) >= 3 and ratios == sorted(ratios) and 0 < ratios[0] and ratios[-1] <= 1.0
    assert events[-1] == {"event": "done", "data": {"sha256": hashlib.sha256(data).hexdigest()}}
    assert [e["event"] for e in events].count("done") == 1


def test_sha256_error_is_a_normal_json_error_even_when_streaming(local, tmp_path):
    r = local.post("/v1/local/hash/sha256", json={"path": str(tmp_path / "nope")}, headers={"Accept": "text/event-stream"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_sha256_is_cached_until_the_file_changes(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    m = LocalMedia(None)
    first = list(m.sha256(str(f)))[-1]
    # same path, size and mtime -> served from the cache even if we sneak new bytes in behind its back
    st = f.stat()
    f.write_bytes(b"xyz")
    import os
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert list(m.sha256(str(f)))[-1] == first
    f.write_bytes(b"xyz-longer")                                          # size changes -> recomputed
    assert list(m.sha256(str(f)))[-1] == hashlib.sha256(b"xyz-longer").hexdigest()


def test_remote_mode_does_not_expose_local_endpoints(client):  # conftest's regular (non-local) server
    assert client.post("/v1/local/media/probe", headers=AUTH, json={"path": "/x"}).status_code == 404
    assert client.post("/v1/local/hash/sha256", headers=AUTH, json={"path": "/x"}).status_code == 404
