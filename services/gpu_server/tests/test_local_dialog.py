"""/v1/local/dialog (#46): the native dialog, the grants it creates, and who may open it."""
import base64
import json
import subprocess
import threading

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH, TOKEN
from vvlog_server.adapters import native_dialog
from vvlog_server.adapters.native_dialog import NativeDialog, WindowsDialogBackend, build_request
from vvlog_server.config import Settings
from vvlog_server.domain.errors import VvlogError
from vvlog_server.main import create_app

PORT = 54324
SELF = f"http://127.0.0.1:{PORT}"
PAGES = "https://pages.example"
D = "/v1/local/dialog"
FS = "/v1/local/fs"


class FakeBackend:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def run(self, request, timeout):
        self.calls.append(request)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def make(tmp_path, fake=None):
    for d in ("projects", "appdata", "config", "dist", "elsewhere"):
        (tmp_path / d).mkdir(exist_ok=True)
    (tmp_path / "dist" / "index.html").write_text("x")
    env = {"VVLOG_API_TOKEN": TOKEN, "VVLOG_DATA_ROOT": str(tmp_path / "data"), "VVLOG_LOCAL": "1", "VVLOG_PORT": str(PORT),
           "VVLOG_SPA_DIR": str(tmp_path / "dist"), "VVLOG_LOCAL_PROJECTS_DIR": str(tmp_path / "projects"),
           "VVLOG_LOCAL_APPDATA_DIR": str(tmp_path / "appdata"), "VVLOG_LOCAL_CONFIG_DIR": str(tmp_path / "config"),
           "VVLOG_ALLOWED_ORIGINS": PAGES}
    c = TestClient(create_app(Settings.from_env(env)), base_url=SELF)
    c.headers.update(AUTH)
    if fake:
        c.app.state.native_dialog.backend = fake
    return c


@pytest.fixture
def world(tmp_path):
    with make(tmp_path) as c:
        c.root = tmp_path
        yield c


# --- the three dialogs -----------------------------------------------------------------------------------------------------
def test_open_files_returns_paths_and_passes_the_options(world):
    a, b = str(world.root / "elsewhere" / "a.mp4"), str(world.root / "elsewhere" / "b.mov")
    world.app.state.native_dialog.backend = fake = FakeBackend([a, b])
    r = world.post(f"{D}/open-files", json={"filters": [{"name": "動画", "extensions": ["mp4", ".mov"]}], "multiple": True,
                                            "defaultPath": "D:\\Videos", "title": "動画を選ぶ"})
    assert r.status_code == 200 and r.json() == {"paths": [a, b]}
    assert fake.calls == [{"kind": "open-files", "filters": [{"name": "動画", "extensions": ["mp4", "mov"]}],
                           "multiple": True, "defaultPath": "D:\\Videos", "title": "動画を選ぶ"}]


def test_single_selection_never_returns_more_than_one(world):
    world.app.state.native_dialog.backend = FakeBackend(["/x/a.mp4", "/x/b.mp4"])
    assert len(world.post(f"{D}/open-files", json={"multiple": False}).json()["paths"]) == 1


def test_cancel_is_an_empty_result_not_an_error(world):
    world.app.state.native_dialog.backend = FakeBackend([], [], [])
    assert world.post(f"{D}/open-files", json={}).json() == {"paths": []}
    assert world.post(f"{D}/open-dir", json={}).json() == {"path": None}
    assert world.post(f"{D}/save", json={}).json() == {"path": None}


def test_save_and_open_dir_return_one_path(world):
    d, f = str(world.root / "elsewhere"), str(world.root / "elsewhere" / "out.mp4")
    world.app.state.native_dialog.backend = FakeBackend([d], [f])
    assert world.post(f"{D}/open-dir", json={"title": "保存先"}).json() == {"path": d}
    assert world.post(f"{D}/save", json={"defaultPath": f}).json() == {"path": f}


# --- what the user picks becomes accessible (and nothing else does) ------------------------------------------------------
def test_a_picked_file_becomes_readable_but_its_neighbours_do_not(world):
    picked, other = world.root / "elsewhere" / "clip.mp4", world.root / "elsewhere" / "other.txt"
    picked.write_bytes(b"0123456789")
    other.write_text("private")
    assert world.get(f"{FS}/exists", params={"path": str(picked)}).status_code == 403   # before: outside every root
    world.app.state.native_dialog.backend = FakeBackend([str(picked)])
    world.post(f"{D}/open-files", json={})
    assert world.get(f"{FS}/stat", params={"path": str(picked)}).json()["size"] == 10
    assert world.get(f"{FS}/raw", params={"path": str(picked)}, headers={"Range": "bytes=2-4"}).content == b"234"
    assert world.get(f"{FS}/text", params={"path": str(other)}).status_code == 403
    assert world.get(f"{FS}/list", params={"path": str(world.root / "elsewhere")}).status_code == 403


def test_a_picked_folder_becomes_a_root(world):
    folder = world.root / "elsewhere"
    world.app.state.native_dialog.backend = FakeBackend([str(folder)])
    world.post(f"{D}/open-dir", json={})
    assert world.put(f"{FS}/text", params={"path": str(folder / "p" / "project.json")}, content=b"{}").status_code == 204
    assert (folder / "p" / "project.json").read_text() == "{}"
    # ...but the folder itself still cannot be deleted through the API
    assert world.post(f"{FS}/remove", json={"path": str(folder)}).status_code == 403


def test_a_picked_save_path_can_be_written_exactly_there(world):
    dest = world.root / "elsewhere" / "export.mp4"
    world.app.state.native_dialog.backend = FakeBackend([str(dest)])
    world.post(f"{D}/save", json={})
    assert world.put(f"{FS}/raw", params={"path": str(dest)}, content=b"video").status_code == 204
    assert dest.read_bytes() == b"video"
    assert world.put(f"{FS}/raw", params={"path": str(dest) + ".bak"}, content=b"x").status_code == 403


def test_grants_survive_a_restart_and_a_garbled_store_is_ignored(tmp_path):
    f, d = tmp_path / "elsewhere" / "clip.mp4", tmp_path / "elsewhere" / "sub"
    (tmp_path / "elsewhere").mkdir()
    d.mkdir()
    f.write_bytes(b"abc")
    with make(tmp_path, FakeBackend([str(f)], [str(d)])) as c:
        c.post(f"{D}/open-files", json={})
        c.post(f"{D}/open-dir", json={})
    with make(tmp_path) as c2:  # a fresh server on the same data root
        assert c2.get(f"{FS}/stat", params={"path": str(f)}).json()["size"] == 3
        assert c2.get(f"{FS}/list", params={"path": str(d)}).status_code == 200
        assert c2.get(f"{FS}/stat", params={"path": str(tmp_path / "elsewhere")}).status_code == 403
    (tmp_path / "data" / "local" / "grants.json").write_text("{not json")
    with make(tmp_path) as c3:
        assert c3.get(f"{FS}/stat", params={"path": str(f)}).status_code == 403   # nothing remembered, no crash


def test_the_grant_store_is_not_reachable_through_the_file_api(world):
    store = world.root / "data" / "local" / "grants.json"
    world.app.state.native_dialog.backend = FakeBackend([str(world.root / "elsewhere")])
    world.post(f"{D}/open-dir", json={})
    assert store.is_file()
    assert world.get(f"{FS}/text", params={"path": str(store)}).status_code == 403
    assert world.put(f"{FS}/text", params={"path": str(store)}, content=b"{}").status_code == 403


# --- who may open a dialog ---------------------------------------------------------------------------------------------------
def test_requires_the_token(world):
    assert world.post(f"{D}/open-files", json={}, headers={"Authorization": ""}).status_code == 401


def test_the_pages_origin_cannot_open_a_dialog_even_with_the_token(world):
    world.app.state.native_dialog.backend = fake = FakeBackend(["/x/a.mp4"])
    r = world.post(f"{D}/open-files", json={}, headers={"Origin": PAGES})
    assert r.status_code == 403 and r.json()["error"]["code"] == "DIALOG_LOCAL_ONLY"
    assert world.post(f"{D}/open-files", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert fake.calls == []                                      # nothing was shown
    assert world.post(f"{D}/open-files", json={}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_not_available_outside_local_mode(client):
    assert client.post(f"{D}/open-files", headers=AUTH, json={}).status_code == 404


def test_one_dialog_at_a_time(world):
    entered, release = threading.Event(), threading.Event()

    class Slow:
        def run(self, request, timeout):
            entered.set()
            release.wait(5)
            return []

    nd = NativeDialog(Slow())
    t = threading.Thread(target=nd.ask, args=(build_request("open-files"),))
    t.start()
    assert entered.wait(5)
    with pytest.raises(VvlogError) as e:
        nd.ask(build_request("save"))
    assert e.value.code == "DIALOG_BUSY"
    release.set()
    t.join()
    assert nd._lock.acquire(blocking=False)                                    # released afterwards


def test_busy_and_failures_map_to_http(world):
    world.app.state.native_dialog.backend = FakeBackend(VvlogError("DIALOG_BUSY", "別のダイアログが開いています"),
                                                        VvlogError("DIALOG_FAILED", "x"))
    assert world.post(f"{D}/open-files", json={}).status_code == 409
    assert world.post(f"{D}/open-files", json={}).status_code == 500


def test_unavailable_off_windows(world, monkeypatch):
    monkeypatch.setattr(native_dialog.os, "name", "posix")
    world.app.state.native_dialog.backend = native_dialog.default_backend()
    r = world.post(f"{D}/open-files", json={})
    assert r.status_code == 501 and r.json()["error"]["code"] == "DIALOG_UNAVAILABLE"


# --- request validation --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ext", ["mp4;*.exe", "*", "a b", "x" * 11, ""])
def test_unsafe_extensions_are_refused(world, ext):
    world.app.state.native_dialog.backend = fake = FakeBackend([])
    r = world.post(f"{D}/open-files", json={"filters": [{"name": "n", "extensions": [ext]}]})
    assert r.status_code == 422 and fake.calls == []


def test_filter_names_lose_the_characters_of_the_windows_filter_syntax():
    req = build_request("open-files", filters=[{"name": "動画 (a|b;*)", "extensions": ["mp4"]}], title="a\nb")
    assert req["filters"][0]["name"] == "動画  a b"
    assert req["title"] == "a b"
    assert build_request("open-files", filters=[{"name": "|", "extensions": ["mp4"]}])["filters"][0]["name"] == "Files"


def test_unknown_fields_are_rejected(world):
    assert world.post(f"{D}/open-files", json={"kind": "save"}).status_code == 422


# --- the Windows backend (PowerShell is not available here: its invocation is checked, the dialog itself is not) -----------
def test_windows_backend_passes_arguments_through_the_environment_only(monkeypatch):
    seen = {}

    def run(cmd, env, capture_output, timeout, creationflags=0):
        seen.update(cmd=cmd, env=env, timeout=timeout)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"paths":["C:\\\\v\\\\a.mp4","C:\\\\v\\\\動画.mp4"]}\r\n'.encode(), stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    req = build_request("open-files", filters=[{"name": "x", "extensions": ["mp4"]}], default_path="C:\\evil'; calc #")
    paths = WindowsDialogBackend().run(req, 30)
    assert paths == ["C:\\v\\a.mp4", "C:\\v\\動画.mp4"]
    cmd = seen["cmd"]
    assert cmd[0] == "powershell.exe" and "-STA" in cmd and "-NoProfile" in cmd and "-NonInteractive" in cmd
    script = base64.b64decode(cmd[cmd.index("-EncodedCommand") + 1]).decode("utf-16-le")
    assert "calc" not in script and "evil" not in script                      # arguments never enter the script text
    assert json.loads(seen["env"]["VVLOG_DIALOG_ARGS"])["defaultPath"] == "C:\\evil'; calc #"
    assert "TopMost" in script and "ShowDialog($owner)" in script             # parented to a top-most window
    assert seen["timeout"] == 30


def test_windows_backend_results(monkeypatch):
    def fake(stdout=b"", code=0, stderr=b"", exc=None):
        def run(cmd, **kw):
            if exc:
                raise exc
            return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr=stderr)
        return run

    b = WindowsDialogBackend()
    monkeypatch.setattr(subprocess, "run", fake(b'{"paths":[]}'))
    assert b.run({}, 1) == []                                                  # cancelled
    monkeypatch.setattr(subprocess, "run", fake(b'{"paths":"C:\\\\one.mp4"}'))
    assert b.run({}, 1) == ["C:\\one.mp4"]                                    # PowerShell 5.1 may collapse a 1-item array
    monkeypatch.setattr(subprocess, "run", fake(b""))
    assert b.run({}, 1) == []
    monkeypatch.setattr(subprocess, "run", fake(exc=subprocess.TimeoutExpired("ps", 1)))
    assert b.run({}, 1) == []                                                  # timeout = cancel
    for bad in (fake(b"not json"), fake(b"", 1, b"boom"), fake(exc=FileNotFoundError("powershell.exe"))):
        monkeypatch.setattr(subprocess, "run", bad)
        with pytest.raises(VvlogError) as e:
            b.run({}, 1)
        assert e.value.code == "DIALOG_FAILED"
