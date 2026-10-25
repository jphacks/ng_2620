"""H.264 encoder choice: the installer's LGPL ffmpeg has no libx264, so the first encoder that works is used."""
import shutil
import subprocess

import pytest

from vvlog_server.adapters import h264


@pytest.fixture(autouse=True)
def fresh_cache():
    h264.pick_encoder.cache_clear()
    yield
    h264.pick_encoder.cache_clear()


def fake_ffmpeg(monkeypatch, working: set[str]):
    tried = []

    def run(cmd, **kw):
        enc = cmd[cmd.index("-c:v") + 1]
        tried.append(enc)
        return subprocess.CompletedProcess(cmd, 0 if enc in working else 1, b"", b"")
    monkeypatch.setattr(h264.subprocess, "run", run)
    return tried


def test_libx264_first_when_present(monkeypatch):
    fake_ffmpeg(monkeypatch, {"libx264", "h264_mf"})
    assert h264.video_args(18, high_profile=True) == ["-c:v", "libx264", "-crf", "18", "-profile:v", "high"]


def test_lgpl_build_falls_back_in_order_and_caches(monkeypatch):
    tried = fake_ffmpeg(monkeypatch, {"h264_mf", "libopenh264"})   # the installer's LGPL build
    assert h264.video_args(23, high_profile=True) == ["-c:v", "h264_mf", "-rate_control", "quality", "-quality", "54",
                                                      "-profile", "100"]
    assert tried == ["libx264", "h264_mf"]
    h264.video_args(18)
    assert len(tried) == 2   # one probe per ffmpeg


def test_openh264_is_the_last_resort(monkeypatch):
    fake_ffmpeg(monkeypatch, {"libopenh264"})
    assert h264.video_args(20, high_profile=True) == ["-c:v", "libopenh264", "-rc_mode", "quality", "-qmin", "20",
                                                      "-qmax", "26"]


def test_nothing_works_keeps_libx264(monkeypatch):
    fake_ffmpeg(monkeypatch, set())
    assert h264.pick_encoder() == "libx264"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_the_chosen_encoder_really_encodes(tmp_path):
    out = tmp_path / "x.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=0.5",
                    *h264.video_args(23, high_profile=True), "-pix_fmt", "yuv420p", str(out)], check=True)
    assert out.stat().st_size > 0
