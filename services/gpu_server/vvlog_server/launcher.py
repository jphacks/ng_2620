"""Windows launcher (#31, ADR-0015): start the local app server without a UI, open the browser, keep one instance.

  python -m vvlog_server.launcher          (start.bat / start.ps1 are thin wrappers around this)

  * picks port 8765 (what the GitHub Pages hint probes) and falls back to a free port when it is taken
  * runs `python -m vvlog_server` (VVLOG_LOCAL=1) as a child, mirrors its output into the log file, reads the
    `VVLOG_LOCAL_READY <url>` line and opens it with the default browser
  * one instance per user: <state dir>/launcher.json holds pid / port / url. A second launch whose recorded instance
    is alive (pid exists AND /v1/health answers) only opens that URL. A stale file is removed.
  * Windows: a tray icon shows it is running (tooltip = URL; open / copy URL / quit); --no-tray turns it off
  * if the browser cannot be opened automatically, the URL is printed so it can be typed in by hand
  * stops on Ctrl+C / SIGTERM / console close, or when the page calls POST /v1/local/shutdown (the child exits);
    the child's whole process tree is stopped. A running job is left to become INTERRUPTED on the next start.

Environment: VVLOG_STATE_DIR (default %LOCALAPPDATA%\\vvlog), VVLOG_LOG_DIR (default <state dir>\\logs),
VVLOG_LAUNCHER_PORT (preferred port, default 8765). Anything else is passed through to the server.
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable

from . import tray as tray_mod

DEFAULT_PORT = 8765
READY_PREFIX = "VVLOG_LOCAL_READY "
STATE_FILE = "launcher.json"
log = logging.getLogger("vvlog.launcher")


# --- locations -------------------------------------------------------------------------------------------------------
def state_dir(env=None) -> Path:
    e = os.environ if env is None else env
    if e.get("VVLOG_STATE_DIR"):
        return Path(e["VVLOG_STATE_DIR"])
    if os.name == "nt":
        return Path(e.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "vvlog"
    return Path.home() / ".local" / "share" / "vvlog"


def log_dir(env=None) -> Path:
    e = os.environ if env is None else env
    return Path(e["VVLOG_LOG_DIR"]) if e.get("VVLOG_LOG_DIR") else state_dir(e) / "logs"


# --- state file ------------------------------------------------------------------------------------------------------
def read_state(sdir: Path) -> dict | None:
    try:
        data = json.loads((sdir / STATE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_state(sdir: Path, state: dict) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    tmp = sdir / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, sdir / STATE_FILE)


def remove_state(sdir: Path, only_pid: int | None = None) -> None:
    st = read_state(sdir)
    if only_pid is not None and st and st.get("pid") != only_pid:
        return  # somebody else's file now
    try:
        (sdir / STATE_FILE).unlink()
    except OSError:
        pass


def claim_state(sdir: Path) -> bool:
    """Atomically create launcher.json as 'starting' (url null). False if it already exists."""
    sdir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(sdir / STATE_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "port": None, "url": None}, f)
    return True


# --- liveness --------------------------------------------------------------------------------------------------------
def pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":  # os.kill(pid, 0) would terminate the process on Windows
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def health_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/v1/health", timeout=timeout) as r:
            return r.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def find_running(sdir: Path, wait_starting: float = 30.0) -> str | None:
    """URL of a live instance, else None (a stale state file is cleaned up on the way)."""
    deadline = time.monotonic() + wait_starting
    while True:
        st = read_state(sdir)
        if st is None:
            return None
        pid = st.get("pid")
        if pid == os.getpid() or not pid_alive(pid):
            remove_state(sdir, pid)
            return None
        url = st.get("url")
        if url:
            if health_ok(url):
                return url
            remove_state(sdir, pid)  # pid reused by something else / server gone
            return None
        if time.monotonic() >= deadline:  # 'starting' for too long: its launcher is stuck
            return None
        time.sleep(0.2)  # another launcher is still starting its server


# --- port ------------------------------------------------------------------------------------------------------------
def choose_port(preferred: int = DEFAULT_PORT) -> int:
    """preferred if it can be bound on 127.0.0.1, else a free port picked by the OS."""
    for p in (preferred, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise OSError("空きポートが見つかりません")


# --- logging ---------------------------------------------------------------------------------------------------------
def setup_logging(ldir: Path) -> logging.Logger:
    """Server output -> logs/server.log (rotating, 2MB x 5). The launcher's own messages go there and to stderr."""
    ldir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(ldir / "server.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("[vvlog] %(message)s"))
    server_log = logging.getLogger("vvlog.server")
    server_log.propagate = False
    for lg, handlers in ((server_log, [fh]), (log, [fh, sh])):
        for h in list(lg.handlers):
            lg.removeHandler(h)
            h.close()
        lg.setLevel(logging.INFO)
        for h in handlers:
            lg.addHandler(h)
    return server_log


# --- child process ---------------------------------------------------------------------------------------------------
def server_env(port: int, env: dict) -> dict:
    """The child's environment. VVLOG_BIN_DIR goes first on PATH: the server and the pipeline call `ffmpeg` / `ffprobe`
    by name, and a plain Windows machine has them only in the installer's bin dir."""
    child_env = dict(env)
    child_env.update(VVLOG_LOCAL="1", VVLOG_PORT=str(port), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    bin_dir = env.get("VVLOG_BIN_DIR")
    if bin_dir:
        key = next((k for k in child_env if k.upper() == "PATH"), "PATH")  # a plain dict keeps Windows' "Path" spelling
        rest = child_env.get(key, "")
        child_env[key] = bin_dir + (os.pathsep + rest if rest else "")
    return child_env


def spawn_server(port: int, env: dict, cwd: Path | None = None) -> subprocess.Popen:
    child_env = server_env(port, env)
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True  # own process group: Ctrl+C in the terminal must not hit it twice
    return subprocess.Popen([sys.executable, "-m", "vvlog_server"], env=child_env, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", **kw)


def stop_server(proc: subprocess.Popen, grace: float = 15.0) -> None:
    """Graceful first (uvicorn finishes in-flight requests), then the whole tree."""
    if proc.poll() is None:
        try:
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        try:
            proc.wait(grace)
        except subprocess.TimeoutExpired:
            log.warning("サーバーが %.0f 秒で止まらないため強制終了します", grace)
    kill_tree(proc)


def kill_tree(proc: subprocess.Popen) -> None:
    """Also reaps grandchildren (the pipeline subprocesses) that outlived the server."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ValueError):
        pass
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass


# --- main ------------------------------------------------------------------------------------------------------------
def run(*, env: dict | None = None, opener: Callable[[str], object] = webbrowser.open, open_browser: bool = True,
        ready_timeout: float = 60.0, install_signals: bool = True, use_tray: bool = True,
        tray_start: Callable = tray_mod.start) -> int:
    e = dict(os.environ if env is None else env)
    sdir, ldir = state_dir(e), log_dir(e)
    server_log = setup_logging(ldir)

    while not claim_state(sdir):
        url = find_running(sdir)
        if url:
            log.info("すでに起動しています。%s を開きます", url)
            if open_browser:
                opener(url)
            return 0
        remove_state(sdir)  # stale -> retry the claim

    proc: subprocess.Popen | None = None
    tray = None
    stop = threading.Event()
    try:
        if install_signals:
            for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
                if hasattr(signal, name):
                    signal.signal(getattr(signal, name), lambda *_: stop.set())
        preferred = int(e.get("VVLOG_LAUNCHER_PORT") or DEFAULT_PORT)
        port = choose_port(preferred)
        if preferred and port != preferred:
            log.info("ポート %s は使用中のため %s を使います", preferred, port)
        e.setdefault("VVLOG_DATA_ROOT", str(sdir / "data"))
        proc = spawn_server(port, e)
        ready = threading.Event()
        found: list[str] = []

        def pump() -> None:
            assert proc and proc.stdout
            for line in proc.stdout:
                line = line.rstrip()
                server_log.info(line)
                if line.startswith(READY_PREFIX) and not found:
                    found.append(line[len(READY_PREFIX):].strip())
                    ready.set()
            ready.set()  # EOF: the server is gone

        threading.Thread(target=pump, name="server-output", daemon=True).start()
        deadline = time.monotonic() + ready_timeout
        while not ready.wait(0.2):
            if stop.is_set() or time.monotonic() > deadline:
                break
        if not found:
            log.error("サーバーが起動しませんでした。ログ: %s", ldir / "server.log")
            return 1
        url = found[0]
        # READY is printed once the socket is bound; wait until the server actually answers before telling anyone
        while not health_ok(url, 1.0):
            if stop.is_set() or proc.poll() is not None or time.monotonic() > deadline:
                log.error("サーバーが応答しませんでした。ログ: %s", ldir / "server.log")
                return 1
            time.sleep(0.2)
        write_state(sdir, {"pid": os.getpid(), "port": port, "url": url, "serverPid": proc.pid})
        log.info("起動しました: %s（終了は Ctrl+C か画面の『アプリを終了』、トレイの『終了』）", url)
        if use_tray:
            tray = tray_start(url, stop.set, opener)
        if open_browser:
            try:
                opened = opener(url)
            except Exception as ex:  # noqa: BLE001
                log.warning("ブラウザを開けませんでした: %s", ex)
                opened = False
            if opened is False:
                log.warning("ブラウザを自動で開けませんでした。ブラウザのアドレス欄に %s を入力してください", url)
        while not stop.is_set() and proc.poll() is None:
            stop.wait(0.3)
        log.info("終了します")
        return 0
    finally:
        if tray is not None:
            tray.stop()
        if proc is not None:
            stop_server(proc)
        remove_state(sdir, os.getpid())


def main(argv: list[str] | None = None, *, opener: Callable[[str], object] = webbrowser.open) -> int:
    ap = argparse.ArgumentParser(prog="vvlog_server.launcher", description="vvlog ローカルアプリのランチャー")
    ap.add_argument("--no-browser", action="store_true", help="ブラウザを開かない")
    ap.add_argument("--no-tray", action="store_true", help="トレイアイコンを出さない")
    args = ap.parse_args(argv)
    return run(opener=opener, open_browser=not args.no_browser, use_tray=not args.no_tray)


if __name__ == "__main__":
    sys.exit(main())
