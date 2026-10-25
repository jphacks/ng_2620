"""/v1/local/fs (#23): behaviour ported from fs.rs, plus the root allow-list that HTTP access makes mandatory."""
import io
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH, TOKEN
from vvlog_server.config import Settings
from vvlog_server.main import create_app

PORT = 54322
SELF = f"http://127.0.0.1:{PORT}"
FS = "/v1/local/fs"


@pytest.fixture
def world(tmp_path):
    """allowed: <tmp>/projects, <tmp>/extra. forbidden: <tmp>/outside."""
    for d in ("projects", "extra", "outside", "appdata", "config", "dist"):
        (tmp_path / d).mkdir()
    (tmp_path / "dist" / "index.html").write_text("x")
    (tmp_path / "outside" / "secret.txt").write_text("top secret")
    env = {"VVLOG_API_TOKEN": TOKEN, "VVLOG_DATA_ROOT": str(tmp_path / "data"), "VVLOG_LOCAL": "1",
           "VVLOG_PORT": str(PORT), "VVLOG_SPA_DIR": str(tmp_path / "dist"),
           "VVLOG_LOCAL_PROJECTS_DIR": str(tmp_path / "projects"), "VVLOG_LOCAL_APPDATA_DIR": str(tmp_path / "appdata"),
           "VVLOG_LOCAL_CONFIG_DIR": str(tmp_path / "config"), "VVLOG_LOCAL_ROOTS": str(tmp_path / "extra")}
    with TestClient(create_app(Settings.from_env(env)), base_url=SELF) as c:
        c.headers.update(AUTH)
        c.root = tmp_path
        yield c


def p(world, *parts):
    return str(world.root.joinpath(*parts))


def put_text(world, path, text, **q):
    return world.put(f"{FS}/text", params={"path": path, **q}, content=text.encode())


# --- auth ----------------------------------------------------------------------------------------------------------------
def test_fs_requires_the_token(world):
    assert world.get(f"{FS}/exists", params={"path": p(world, "projects")}, headers={"Authorization": ""}).status_code == 401


def test_info_lists_the_dirs_for_same_origin_only(world):
    d = world.get("/v1/local/info").json()["dirs"]
    assert d["projects"] == p(world, "projects") and set(d) == {"projects", "appData", "config"}
    assert "dirs" not in world.get("/v1/local/info", headers={"Sec-Fetch-Site": "cross-site"}).json()


# --- ported behaviour ----------------------------------------------------------------------------------------------------
def test_text_roundtrip_creates_parents_and_replaces(world):
    f = p(world, "projects", "a", "b", "project.json")
    assert put_text(world, f, '{"v":1}').status_code == 204
    assert world.get(f"{FS}/text", params={"path": f}).text == '{"v":1}'
    assert put_text(world, f, '{"v":2}').status_code == 204
    assert world.get(f"{FS}/text", params={"path": f}).text == '{"v":2}'
    assert sorted(os.listdir(os.path.dirname(f))) == ["project.json"]  # no temp file left behind


def test_text_is_utf8(world):
    f = p(world, "projects", "動画.json")
    put_text(world, f, "こんにちは")
    assert world.get(f"{FS}/text", params={"path": f}).content.decode() == "こんにちは"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_private_write_is_mode_600(world):
    f = p(world, "config", "secrets.json")
    put_text(world, f, "{}", private="true")
    assert os.stat(f).st_mode & 0o777 == 0o600


def test_failed_atomic_write_keeps_the_old_file(world, monkeypatch):
    f = p(world, "projects", "project.json")
    put_text(world, f, "old")

    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    r = put_text(world, f, "new")
    assert r.status_code == 500
    monkeypatch.undo()
    assert world.get(f"{FS}/text", params={"path": f}).text == "old"
    assert [n for n in os.listdir(os.path.dirname(f)) if n != "project.json"] == []  # temp file cleaned up


def test_stat_exists_and_missing(world):
    f = p(world, "projects", "v.bin")
    assert world.get(f"{FS}/exists", params={"path": f}).json() == {"exists": False}
    world.put(f"{FS}/raw", params={"path": f}, content=b"0123456789")
    assert world.get(f"{FS}/exists", params={"path": f}).json() == {"exists": True}
    st = world.get(f"{FS}/stat", params={"path": f}).json()
    assert st["size"] == 10 and st["isDir"] is False and abs(st["mtimeMs"] - os.stat(f).st_mtime * 1000) < 1
    r = world.get(f"{FS}/stat", params={"path": p(world, "projects", "nope")})
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert world.get(f"{FS}/text", params={"path": p(world, "projects", "nope")}).status_code == 404


def test_raw_read_with_ranges(world):
    f = p(world, "projects", "v.bin")
    world.put(f"{FS}/raw", params={"path": f}, content=b"0123456789")
    assert world.get(f"{FS}/raw", params={"path": f}).content == b"0123456789"
    r = world.get(f"{FS}/raw", params={"path": f}, headers={"Range": "bytes=2-5"})
    assert r.status_code == 206 and r.content == b"2345" and r.headers["content-range"] == "bytes 2-5/10"
    assert world.get(f"{FS}/raw", params={"path": f}, headers={"Range": "bytes=8-"}).content == b"89"
    assert world.get(f"{FS}/raw", params={"path": f}, headers={"Range": "bytes=-3"}).content == b"789"
    assert world.get(f"{FS}/raw", params={"path": f}, headers={"Range": "bytes=20-30"}).status_code == 416


def test_raw_accepts_the_token_in_the_query_for_img_and_video(world):
    f = p(world, "projects", "v.bin")
    world.put(f"{FS}/raw", params={"path": f}, content=b"abc")
    r = world.get(f"{FS}/raw", params={"path": f, "t": TOKEN}, headers={"Authorization": ""})
    assert r.status_code == 200 and r.content == b"abc"
    assert world.get(f"{FS}/raw", params={"path": f, "t": "wrong"}, headers={"Authorization": ""}).status_code == 401
    # only raw GET takes ?t=; everything else still needs the header
    assert world.get(f"{FS}/stat", params={"path": f, "t": TOKEN}, headers={"Authorization": ""}).status_code == 401


def test_large_raw_write_is_streamed_and_atomic(world):
    f = p(world, "projects", "big.bin")
    blob = os.urandom(5 << 20)
    assert world.put(f"{FS}/raw", params={"path": f}, content=iter([blob[:1 << 20], blob[1 << 20:]])).status_code == 204
    assert open(f, "rb").read() == blob


def test_append_with_expected_size_makes_resume_safe(world):
    f = p(world, "projects", "dl.part")
    assert world.post(f"{FS}/append", params={"path": f, "expectedSize": 0}, content=b"abc").json() == {"size": 3}
    assert world.post(f"{FS}/append", params={"path": f, "expectedSize": 3}, content=b"def").json() == {"size": 6}
    r = world.post(f"{FS}/append", params={"path": f, "expectedSize": 3}, content=b"def")  # a repeated chunk
    assert r.status_code == 409 and r.json()["error"]["code"] == "FS_CONFLICT"
    assert open(f, "rb").read() == b"abcdef"


def test_rename_finishes_a_part_file_and_replaces_the_target(world):
    part, dest = p(world, "projects", "x", "a.zip.part"), p(world, "projects", "x", "a.zip")
    world.post(f"{FS}/append", params={"path": part}, content=b"new")
    put_text(world, dest, "old")
    assert world.post(f"{FS}/rename", json={"src": part, "dest": dest}).status_code == 204
    assert open(dest, "rb").read() == b"new" and not os.path.exists(part)
    assert world.post(f"{FS}/rename", json={"src": part, "dest": dest}).status_code == 404
    # creates the destination folder, like copy
    assert world.post(f"{FS}/rename", json={"src": dest, "dest": p(world, "projects", "y", "z", "b.zip")}).status_code == 204


def test_rename_cannot_cross_the_roots_or_move_a_root(world):
    a = p(world, "projects", "a.txt")
    put_text(world, a, "hi")
    assert world.post(f"{FS}/rename", json={"src": a, "dest": p(world, "outside", "a.txt")}).status_code == 403
    assert world.post(f"{FS}/rename", json={"src": p(world, *OUTSIDE), "dest": p(world, "projects", "s.txt")}).status_code == 403
    assert world.post(f"{FS}/rename", json={"src": p(world, "projects"), "dest": p(world, "extra", "p")}).status_code == 403
    assert open(a).read() == "hi" and open(p(world, *OUTSIDE)).read() == "top secret"


def test_copy_mkdir_list_remove(world):
    a = p(world, "projects", "a.txt")
    put_text(world, a, "hi")
    assert world.post(f"{FS}/copy", json={"src": a, "dest": p(world, "projects", "sub", "deep", "b.txt")}).status_code == 204
    assert world.post(f"{FS}/mkdir", json={"path": p(world, "projects", "empty", "x")}).status_code == 204
    assert sorted(world.get(f"{FS}/list", params={"path": p(world, "projects")}).json()) == ["a.txt", "empty", "sub"]
    assert world.get(f"{FS}/list", params={"path": p(world, "projects", "missing")}).json() == []
    assert world.post(f"{FS}/remove", json={"path": p(world, "projects", "sub")}).status_code == 204  # recursive
    assert not os.path.exists(p(world, "projects", "sub"))
    assert world.post(f"{FS}/remove", json={"path": p(world, "projects", "never-existed")}).status_code == 204
    assert world.post(f"{FS}/copy", json={"src": p(world, "projects", "none"), "dest": a + "2"}).status_code == 404


def test_move_to_trash(world, monkeypatch):
    import send2trash
    seen = []
    monkeypatch.setattr(send2trash, "send2trash", lambda path: (seen.append(path), os.remove(path)))
    a = p(world, "projects", "a.txt")
    put_text(world, a, "hi")
    assert world.post(f"{FS}/trash", json={"path": a}).status_code == 204
    assert seen == [a] and not os.path.exists(a)
    assert world.post(f"{FS}/trash", json={"path": a}).status_code == 404


def test_unzip_store_and_deflate(world):
    for comp in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        z = p(world, "projects", f"layers{comp}.zip")
        with zipfile.ZipFile(z, "w", comp) as zf:
            zf.writestr("frames/000001.webp", b"AAA")
            zf.writestr("person_thumbs/p1.png", b"BBB")
        dest = p(world, "projects", f"cache{comp}")
        assert world.post(f"{FS}/unzip", json={"zipPath": z, "destDir": dest}).status_code == 204
        assert open(os.path.join(dest, "frames", "000001.webp"), "rb").read() == b"AAA"
        assert open(os.path.join(dest, "person_thumbs", "p1.png"), "rb").read() == b"BBB"


@pytest.mark.parametrize("entry", ["../escape.txt", "a/../../escape.txt", "/abs.txt", "C:/win.txt", "..\\back.txt"])
def test_unzip_refuses_zip_slip_before_writing_anything(world, entry):
    z = p(world, "projects", "evil.zip")
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("ok.txt", b"fine")
        zf.writestr(entry, b"pwned")
    dest = p(world, "projects", "out")
    r = world.post(f"{FS}/unzip", json={"zipPath": z, "destDir": dest})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert not os.path.exists(os.path.join(dest, "ok.txt"))  # nothing was extracted
    assert not os.path.exists(p(world, "projects", "escape.txt")) and not os.path.exists(p(world, "escape.txt"))


@pytest.mark.skipif(os.name == "nt", reason="symlink entries")
def test_unzip_refuses_symlink_entries(world):
    z = p(world, "projects", "link.zip")
    info = zipfile.ZipInfo("link")
    info.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(info, "/etc/passwd")
    r = world.post(f"{FS}/unzip", json={"zipPath": z, "destDir": p(world, "projects", "out")})
    assert r.status_code == 422


def test_unzip_of_a_corrupt_zip(world):
    z = p(world, "projects", "bad.zip")
    open(z, "wb").write(b"not a zip")
    assert world.post(f"{FS}/unzip", json={"zipPath": z, "destDir": p(world, "projects", "o")}).status_code == 422


# --- the allow-list ------------------------------------------------------------------------------------------------------
OUTSIDE = ("outside", "secret.txt")


def test_paths_outside_the_roots_are_refused_everywhere(world):
    out = p(world, *OUTSIDE)
    cases = [
        world.get(f"{FS}/exists", params={"path": out}), world.get(f"{FS}/stat", params={"path": out}),
        world.get(f"{FS}/text", params={"path": out}), world.get(f"{FS}/raw", params={"path": out}),
        world.get(f"{FS}/list", params={"path": p(world, "outside")}), put_text(world, out, "x"),
        world.put(f"{FS}/raw", params={"path": out}, content=b"x"),
        world.post(f"{FS}/append", params={"path": out}, content=b"x"),
        world.post(f"{FS}/copy", json={"src": out, "dest": p(world, "projects", "stolen.txt")}),
        world.post(f"{FS}/copy", json={"src": p(world, "dist", "index.html"), "dest": out}),
        world.post(f"{FS}/mkdir", json={"path": p(world, "outside", "new")}),
        world.post(f"{FS}/remove", json={"path": out}), world.post(f"{FS}/trash", json={"path": out}),
        world.post(f"{FS}/unzip", json={"zipPath": out, "destDir": p(world, "projects", "o")}),
        world.post(f"{FS}/unzip", json={"zipPath": p(world, "projects", "z.zip"), "destDir": p(world, "outside", "o")}),
    ]
    for r in cases:
        assert r.status_code == 403 and r.json()["error"]["code"] == "PATH_NOT_ALLOWED", r.request.url
    assert open(out).read() == "top secret" and not os.path.exists(p(world, "projects", "stolen.txt"))


@pytest.mark.parametrize("make", [
    lambda w: p(w, "projects", "..", "outside", "secret.txt"),
    lambda w: p(w, "projects", "a", "..", "..", "outside", "secret.txt"),
    lambda w: p(w, "extra") + "/../outside/secret.txt",
    lambda w: p(w, "projectsX", "..", "outside", "secret.txt"),
])
def test_dotdot_cannot_climb_out(world, make):
    r = world.get(f"{FS}/text", params={"path": make(world)})
    assert r.status_code == 403


def test_sibling_with_the_same_prefix_is_not_inside(world):
    os.mkdir(p(world, "projects-evil"))
    open(p(world, "projects-evil", "x.txt"), "w").write("x")
    assert world.get(f"{FS}/text", params={"path": p(world, "projects-evil", "x.txt")}).status_code == 403


@pytest.mark.skipif(os.name == "nt", reason="symlinks")
def test_symlink_inside_a_root_pointing_out_is_refused(world):
    os.symlink(p(world, "outside"), p(world, "projects", "link"))
    r = world.get(f"{FS}/text", params={"path": p(world, "projects", "link", "secret.txt")})
    assert r.status_code == 403
    assert put_text(world, p(world, "projects", "link", "new.txt"), "x").status_code == 403
    assert not os.path.exists(p(world, "outside", "new.txt"))


@pytest.mark.parametrize("path", ["", "relative/path.txt", "./a.txt", "~/a.txt", "a\x00b", "C:relative.txt"])
def test_non_absolute_or_malformed_paths_are_refused(world, path):
    assert world.get(f"{FS}/exists", params={"path": path}).status_code == 403


def test_the_roots_themselves_cannot_be_removed_or_overwritten(world):
    root = p(world, "projects")
    for body in ({"path": root}, {"path": p(world, "")}, {"path": str(world.root.parent)}):
        r = world.post(f"{FS}/remove", json=body)
        assert r.status_code == 403, body
    assert os.path.isdir(root)
    assert world.post(f"{FS}/trash", json={"path": root}).status_code == 403
    assert world.put(f"{FS}/raw", params={"path": root}, content=b"x").status_code == 403
    assert world.post(f"{FS}/mkdir", json={"path": root}).status_code == 204  # creating an existing root is harmless


def test_json_bodies_reject_unknown_fields(world):
    assert world.post(f"{FS}/remove", json={"path": p(world, "projects", "x"), "recursive": False}).status_code == 422
