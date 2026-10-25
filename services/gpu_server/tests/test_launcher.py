"""Launcher (#31): one instance, stale state cleanup, port fallback, signal stop, POST /v1/local/shutdown auth."""
import json
import os
import signal
import socket
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH, TOKEN
from vvlog_server import launcher
from vvlog_server.config import Settings
from vvlog_server.main import create_app

PORT = 54322
SELF = f"http://127.0.0.1:{PORT}"
posix_only = pytest.mark.skipif(os.name == "nt", reason="sends SIGINT to a process group (POSIX)")


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# --- state file / liveness -------------------------------------------------------------------------------------------
def test_stale_state_with_dead_pid_is_removed(tmp_path):
    launcher.write_state(tmp_path, {"pid": dead_pid(), "port": 8765, "url": "http://127.0.0.1:8765/"})
    assert launcher.find_running(tmp_path) is None
    assert launcher.read_state(tmp_path) is None


def test_state_with_live_pid_but_no_server_is_removed(tmp_path):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()   # nothing listens here
    launcher.write_state(tmp_path, {"pid": os.getppid(), "port": port, "url": f"http://127.0.0.1:{port}/"})
    assert launcher.find_running(tmp_path) is None
    assert launcher.read_state(tmp_path) is None


def test_garbage_state_file_counts_as_stale(tmp_path):
    (tmp_path / launcher.STATE_FILE).write_text("{not json", encoding="utf-8")
    assert launcher.find_running(tmp_path) is None


def test_claim_is_exclusive(tmp_path):
    assert launcher.claim_state(tmp_path) is True
    assert launcher.claim_state(tmp_path) is False


def test_state_and_log_dirs_follow_env(tmp_path):
    assert launcher.state_dir({"VVLOG_STATE_DIR": str(tmp_path)}) == tmp_path
    assert launcher.log_dir({"VVLOG_STATE_DIR": str(tmp_path)}) == tmp_path / "logs"
    assert launcher.log_dir({"VVLOG_STATE_DIR": str(tmp_path), "VVLOG_LOG_DIR": str(tmp_path / "x")}) == tmp_path / "x"


# --- child environment -----------------------------------------------------------------------------------------------
def test_bin_dir_goes_first_on_the_childs_path(tmp_path):
    """The installer's ffmpeg / ffprobe live only in bin\\; the server calls them by name (a plain PC has none on PATH)."""
    env = launcher.server_env(8765, {"VVLOG_BIN_DIR": str(tmp_path), "Path": "C:\\Windows"})
    assert env["Path"] == str(tmp_path) + os.pathsep + "C:\\Windows" and "PATH" not in env
    assert launcher.server_env(8765, {"VVLOG_BIN_DIR": str(tmp_path)})["PATH"] == str(tmp_path)
    assert launcher.server_env(8765, {"PATH": "/usr/bin"})["PATH"] == "/usr/bin"
    assert launcher.server_env(8765, {})["VVLOG_LOCAL"] == "1"


def test_server_spawned_by_the_launcher_finds_the_bundled_ffprobe(tmp_path):
    name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    (tmp_path / name).write_bytes(b"")
    if os.name != "nt":
        (tmp_path / name).chmod(0o755)
    env = launcher.server_env(8765, {"VVLOG_BIN_DIR": str(tmp_path), "PATH": ""})
    out = subprocess.run([sys.executable, "-c", "import shutil; print(shutil.which('ffprobe'))"], env={**os.environ, **env},
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out and os.path.dirname(out) == str(tmp_path)


# --- port ------------------------------------------------------------------------------------------------------------
def test_preferred_port_is_used_when_free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]; s.close()
    assert launcher.choose_port(free) == free


def test_falls_back_to_a_free_port_when_8765_style_port_is_taken():
    busy = socket.socket(); busy.bind(("127.0.0.1", 0)); busy.listen(); taken = busy.getsockname()[1]
    try:
        got = launcher.choose_port(taken)
        assert got != taken and got > 0
    finally:
        busy.close()


# --- real launcher process -------------------------------------------------------------------------------------------
def launcher_env(tmp_path):
    env = dict(os.environ)
    busy = socket.socket(); busy.bind(("127.0.0.1", 0)); busy.listen()     # stands in for "8765 is taken"
    env.update(VVLOG_STATE_DIR=str(tmp_path / "state"), VVLOG_LAUNCHER_PORT=str(busy.getsockname()[1]),
               VVLOG_DATA_ROOT=str(tmp_path / "data"), VVLOG_LOCAL_PROJECTS_DIR=str(tmp_path / "p"),
               VVLOG_LOCAL_APPDATA_DIR=str(tmp_path / "a"), VVLOG_LOCAL_CONFIG_DIR=str(tmp_path / "c"))
    env.pop("VVLOG_API_TOKEN", None)
    return env, busy


def wait_for(pred, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out")


@posix_only
def test_second_launch_only_opens_the_url_and_sigint_stops_everything(tmp_path):
    env, busy = launcher_env(tmp_path)
    sdir = tmp_path / "state"
    first = subprocess.Popen([sys.executable, "-m", "vvlog_server.launcher", "--no-browser"], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        st = wait_for(lambda: (launcher.read_state(sdir) or {}).get("url") and launcher.read_state(sdir))
        assert st["pid"] == first.pid
        assert st["port"] != busy.getsockname()[1]                      # fell back from the taken port
        assert launcher.health_ok(st["url"])
        server_pid = st["serverPid"]

        opened = []
        os.environ.update(VVLOG_STATE_DIR=env["VVLOG_STATE_DIR"], VVLOG_LAUNCHER_PORT=env["VVLOG_LAUNCHER_PORT"])
        try:
            assert launcher.main([], opener=opened.append) == 0
        finally:
            for k in ("VVLOG_STATE_DIR", "VVLOG_LAUNCHER_PORT"):
                os.environ.pop(k)
        assert opened == [st["url"]]                                    # opened the existing one ...
        assert launcher.read_state(sdir)["serverPid"] == server_pid     # ... and started nothing new
        assert first.poll() is None

        first.send_signal(signal.SIGINT)                                 # Ctrl+C
        assert first.wait(30) == 0
        assert launcher.read_state(sdir) is None
        assert not launcher.health_ok(st["url"])
        assert not launcher.pid_alive(server_pid)
        assert "VVLOG_LOCAL_READY" in (tmp_path / "state" / "logs" / "server.log").read_text(encoding="utf-8")
    finally:
        busy.close()
        if first.poll() is None:
            first.kill()
            first.wait()
        st2 = launcher.read_state(sdir)
        if st2 and st2.get("serverPid") and launcher.pid_alive(st2["serverPid"]):
            os.kill(st2["serverPid"], signal.SIGKILL)


@posix_only
def test_shutdown_from_the_page_stops_the_launcher(tmp_path):
    env, busy = launcher_env(tmp_path)
    sdir = tmp_path / "state"
    first = subprocess.Popen([sys.executable, "-m", "vvlog_server.launcher", "--no-browser"], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        st = wait_for(lambda: (launcher.read_state(sdir) or {}).get("url") and launcher.read_state(sdir))
        import urllib.request
        base = st["url"].rstrip("/")
        info = json.load(urllib.request.urlopen(base + "/v1/local/info"))
        req = urllib.request.Request(base + "/v1/local/shutdown", method="POST",
                                     headers={"Authorization": f"Bearer {info['token']}", "Origin": base})
        assert urllib.request.urlopen(req).status == 202
        assert first.wait(30) == 0
        assert launcher.read_state(sdir) is None
    finally:
        busy.close()
        if first.poll() is None:
            first.kill()
            first.wait()


# --- POST /v1/local/shutdown -----------------------------------------------------------------------------------------
def make(tmp_path, local=True):
    env = {"VVLOG_API_TOKEN": TOKEN, "VVLOG_DATA_ROOT": str(tmp_path / "data"), "VVLOG_PORT": str(PORT),
           "VVLOG_LOCAL_PROJECTS_DIR": str(tmp_path / "p"), "VVLOG_LOCAL_APPDATA_DIR": str(tmp_path / "a"),
           "VVLOG_LOCAL_CONFIG_DIR": str(tmp_path / "c"), "VVLOG_ALLOWED_ORIGINS": "https://pages.example"}
    if local:
        env["VVLOG_LOCAL"] = "1"
    app = create_app(Settings.from_env(env))
    calls = []
    app.state.request_shutdown = lambda: calls.append(1)
    return TestClient(app, base_url=SELF), calls


def test_shutdown_requires_the_token(tmp_path):
    c, calls = make(tmp_path)
    assert c.post("/v1/local/shutdown", headers={"Origin": SELF}).status_code == 401
    assert calls == []


def test_shutdown_refuses_other_origins_even_with_the_token(tmp_path):
    c, calls = make(tmp_path)
    for h in ({"Origin": "https://pages.example"}, {"Sec-Fetch-Site": "cross-site"}):
        r = c.post("/v1/local/shutdown", headers={**AUTH, **h})
        assert r.status_code == 403 and r.json()["error"]["code"] == "SHUTDOWN_LOCAL_ONLY"
    assert c.post("/v1/local/shutdown", headers={**AUTH, "Origin": "https://evil.example"}).status_code == 403
    assert calls == []


def test_shutdown_from_the_local_app_triggers_the_hook(tmp_path):
    c, calls = make(tmp_path)
    r = c.post("/v1/local/shutdown", headers={**AUTH, "Origin": SELF})
    assert r.status_code == 202 and calls == [1]


def test_shutdown_does_not_exist_outside_local_mode(tmp_path):
    c, calls = make(tmp_path, local=False)
    assert c.post("/v1/local/shutdown", headers={**AUTH, "Origin": SELF}).status_code == 404
    assert calls == []


# --- tray / manual URL -----------------------------------------------------------------------------------------------
class FakeTray:
    def __init__(self, url, on_quit, opener):
        self.url, self.on_quit, self.opener, self.stopped = url, on_quit, opener, False

    def stop(self):
        self.stopped = True


def test_tray_gets_the_url_quit_stops_the_launcher_and_a_failed_open_prints_the_url(tmp_path, caplog):
    env, busy = launcher_env(tmp_path)
    made = []

    def factory(url, on_quit, opener):
        made.append(FakeTray(url, on_quit, opener))
        return made[0]

    result = []
    import threading
    t = threading.Thread(target=lambda: result.append(
        launcher.run(env=env, opener=lambda u: False, install_signals=False, tray_start=factory)))
    caplog.set_level("INFO", logger="vvlog.launcher")
    try:
        t.start()
        tray = wait_for(lambda: made and made[0])
        assert launcher.health_ok(tray.url)
        assert not tray.stopped
        tray.on_quit()                                   # "終了" in the tray menu
        t.join(30)
        assert result == [0] and tray.stopped
        assert not launcher.health_ok(tray.url)
        assert any(tray.url in r.getMessage() and "入力" in r.getMessage() for r in caplog.records)
    finally:
        busy.close()


def test_no_tray_flag_skips_the_tray(tmp_path):
    env, busy = launcher_env(tmp_path)
    called = []
    import threading
    t = threading.Thread(target=lambda: launcher.run(env=env, open_browser=False, install_signals=False, use_tray=False,
                                                     tray_start=lambda *a: called.append(a)))
    try:
        t.start()
        st = wait_for(lambda: (launcher.read_state(tmp_path / "state") or {}).get("url") and launcher.read_state(tmp_path / "state"))
        assert called == []
        os.kill(st["serverPid"], signal.SIGTERM if os.name != "nt" else signal.SIGBREAK)   # server gone -> launcher ends
        t.join(30)
    finally:
        busy.close()
