"""重みの取得(#33)。実 HF には接続しない: ローカルの偽 HTTP サーバーで Range・401/403・途中切断・リダイレクトを再現する。"""
import hashlib
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH, TOKEN
from vvlog_server.adapters.local_weights import MARKER, WeightsManager
from vvlog_server.config import Settings
from vvlog_server.main import create_app

SECRET = "hf_SECRET_TOKEN_0123456789"
A = bytes(range(256)) * 4096          # 1 MiB (gated, from "HF")
B = b"plain-file-" * 1000             # from a plain URL
sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731


class Fake:
    """HF っぽい偽サーバー。files: path -> bytes。gated な path は Bearer SECRET が必要。"""

    def __init__(self, files, gated=()):
        self.files, self.gated, self.log = dict(files), set(gated), []
        self.drop_after = None        # 最初のリクエストだけ N バイトで切る
        self.ignore_range = False
        self.redirect = {}            # path -> 絶対 URL
        self.deny_with = 403          # トークンはあるが権限が無いときの応答
        self.slow = 0.0
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.log.append((self.path, dict(self.headers)))
                if self.path in fake.redirect:
                    self.send_response(302)
                    self.send_header("Location", fake.redirect[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path in fake.gated:
                    auth = self.headers.get("Authorization")
                    if not auth:
                        return self.err(401)
                    if auth != f"Bearer {SECRET}":
                        return self.err(401 if auth.endswith("bad") else fake.deny_with)
                body = fake.files.get(self.path)
                if body is None:
                    return self.err(404)
                start, status = 0, 200
                rng = self.headers.get("Range")
                if rng and not fake.ignore_range:
                    start = int(rng.split("=")[1].split("-")[0])
                    if start >= len(body):
                        return self.err(416)
                    status = 206
                chunk = body[start:]
                self.send_response(status)
                self.send_header("Content-Length", str(len(chunk)))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                self.end_headers()
                cut = fake.drop_after
                if cut is not None:
                    fake.drop_after = None
                    self.wfile.write(chunk[:cut])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                for i in range(0, len(chunk), 65536):
                    self.wfile.write(chunk[i:i + 65536])
                    if fake.slow:
                        time.sleep(fake.slow)

            def err(self, code):
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake():
    f = Fake({"/org/gated/resolve/main/a.bin": A, "/plain/b.bin": B}, gated={"/org/gated/resolve/main/a.bin"})
    yield f
    f.close()


def manifest(tmp_path, fake, a_sha=None, b_sha=None):
    items = [
        {"dest": "gated/a.bin", "source": "hf", "repo": "org/gated", "filename": "a.bin", "gated": True, "sha256": a_sha or sha(A)},
        {"dest": "plain/b.bin", "source": "url", "url": f"{fake.base}/plain/b.bin", "sha256": b_sha or sha(B)},
    ]
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"files": items, "hf_cache": [{"repo": "x/y"}]}), encoding="utf-8")
    return p


def mgr(tmp_path, fake, **kw):
    kw = {"hf_base": fake.base, "nominal_total": 0, "reserve_bytes": 0, "backoff_s": 0.01, "retries": 2,
          "disk_free": lambda p: 10 ** 12, **kw}
    return WeightsManager(manifest(tmp_path, fake), tmp_path / "ckpt", **kw)


def run(m, token=SECRET):
    m.start(token)
    m.wait(20)
    return m.job()


def test_downloads_verifies_and_reports_ready(tmp_path, fake):
    m = mgr(tmp_path, fake)
    assert m.status()["allReady"] is False and m.status()["needsToken"] is True
    job = run(m)
    assert job["state"] == "succeeded" and job["error"] is None
    assert (tmp_path / "ckpt/gated/a.bin").read_bytes() == A
    assert (tmp_path / "ckpt/plain/b.bin").read_bytes() == B
    assert not list((tmp_path / "ckpt").rglob("*.part"))
    st = m.status()
    assert st["allReady"] is True and all(f["state"] == "ok" for f in st["files"]) and st["externalCache"] == ["x/y"]
    # 2 回目は取得しない(記録と一致)
    n = len(fake.log)
    assert run(m)["state"] == "succeeded" and len(fake.log) == n


def test_resumes_from_part_with_range(tmp_path, fake):
    part = tmp_path / "ckpt/gated/a.bin.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(A[:300_000])
    m = mgr(tmp_path, fake)
    assert m.status()["files"][0]["state"] == "partial" and m.status()["files"][0]["bytes"] == 300_000
    assert run(m)["state"] == "succeeded"
    assert (tmp_path / "ckpt/gated/a.bin").read_bytes() == A
    assert fake.log[0][1]["Range"] == "bytes=300000-"


def test_resumes_after_a_dropped_connection(tmp_path, fake):
    fake.drop_after = 100_000
    m = mgr(tmp_path, fake)
    assert run(m)["state"] == "succeeded"
    ranges = [h.get("Range") for p, h in fake.log if p.endswith("a.bin")]
    assert ranges[0] is None and ranges[1] == "bytes=100000-"
    assert (tmp_path / "ckpt/gated/a.bin").read_bytes() == A


def test_server_ignoring_range_restarts_from_zero(tmp_path, fake):
    part = tmp_path / "ckpt/gated/a.bin.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"garbage-prefix")
    fake.ignore_range = True
    assert run(mgr(tmp_path, fake))["state"] == "succeeded"
    assert (tmp_path / "ckpt/gated/a.bin").read_bytes() == A


def test_oversized_part_is_discarded_on_416(tmp_path, fake):
    part = tmp_path / "ckpt/gated/a.bin.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(A + b"extra")
    assert run(mgr(tmp_path, fake))["state"] == "succeeded"
    assert (tmp_path / "ckpt/gated/a.bin").read_bytes() == A


def test_checksum_mismatch_deletes_and_refetches_once(tmp_path, fake):
    p = manifest(tmp_path, fake, a_sha="0" * 64)
    m = WeightsManager(p, tmp_path / "ckpt", hf_base=fake.base, nominal_total=0, reserve_bytes=0, backoff_s=0.01,
                       disk_free=lambda _: 10 ** 12)
    job = run(m)
    assert job["state"] == "failed" and job["error"]["code"] == "WEIGHTS_CHECKSUM_MISMATCH"
    assert sum(1 for p_, _ in fake.log if p_.endswith("a.bin")) == 2           # 取り直しは 1 回
    assert not (tmp_path / "ckpt/gated/a.bin").exists() and not (tmp_path / "ckpt/gated/a.bin.part").exists()


def test_corrupt_existing_file_is_replaced(tmp_path, fake):
    f = tmp_path / "ckpt/gated/a.bin"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"truncated")
    m = mgr(tmp_path, fake)
    assert m.status()["files"][0]["state"] == "unverified"
    assert run(m)["state"] == "succeeded" and f.read_bytes() == A


def test_present_file_with_the_right_hash_is_adopted_without_download(tmp_path, fake):
    f = tmp_path / "ckpt/gated/a.bin"
    f.parent.mkdir(parents=True)
    f.write_bytes(A)
    assert run(mgr(tmp_path, fake))["state"] == "succeeded"
    assert not any(p.endswith("a.bin") for p, _ in fake.log)


def test_unpinned_file_reports_its_sha256(tmp_path, fake):
    items = [{"dest": "plain/b.bin", "source": "url", "url": f"{fake.base}/plain/b.bin"}]
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"files": items}))
    m = WeightsManager(p, tmp_path / "ckpt", nominal_total=0, reserve_bytes=0, disk_free=lambda _: 10 ** 12)
    job = run(m)
    assert job["state"] == "succeeded" and job["unpinned"] == [{"dest": "plain/b.bin", "sha256": sha(B)}]


@pytest.mark.parametrize("token,deny,code", [
    (None, 403, "HF_TOKEN_REQUIRED"),
    ("hf_bad", 403, "HF_TOKEN_INVALID"),
    ("hf_other_account", 403, "HF_ACCESS_DENIED"),
])
def test_hf_errors_have_user_facing_codes(tmp_path, fake, token, deny, code):
    fake.deny_with = deny
    job = run(mgr(tmp_path, fake), token)
    assert job["state"] == "failed" and job["error"]["code"] == code
    if code != "HF_TOKEN_INVALID":
        assert job["error"]["detail"]["url"] == f"{fake.base}/org/gated"   # ライセンス同意ページへの案内
    for secret in (token,):
        assert secret is None or secret not in json.dumps(job)


def test_token_never_leaks(tmp_path, fake, caplog):
    cdn = Fake({"/blob": A})
    fake.redirect["/org/gated/resolve/main/a.bin"] = f"{cdn.base}/blob"      # HF -> CDN
    caplog.set_level(logging.DEBUG)
    m = mgr(tmp_path, fake)
    try:
        job = run(m)
        assert job["state"] == "succeeded"
        assert fake.log[0][1].get("Authorization") == f"Bearer {SECRET}"      # HF にだけ渡す
        assert all("Authorization" not in h for _, h in cdn.log)              # CDN には渡さない
        assert all("Authorization" not in h for p, h in fake.log if p.startswith("/plain"))   # HF 以外の URL にも
        blob = json.dumps(m.status()) + json.dumps(job) + caplog.text
        assert SECRET not in blob
        for f in (tmp_path / "ckpt").rglob("*"):                              # ディスク(記録ファイル含む)にも残らない
            if f.is_file() and f.stat().st_size < 10_000:
                assert SECRET.encode() not in f.read_bytes()
        assert SECRET not in repr(vars(m))
    finally:
        cdn.close()


def test_token_is_not_stored_after_a_failure(tmp_path, fake):
    fake.files.pop("/org/gated/resolve/main/a.bin")
    m = mgr(tmp_path, fake)
    job = run(m)
    assert job["error"]["code"] == "WEIGHTS_NOT_FOUND"
    assert SECRET not in json.dumps(job) and SECRET not in repr(vars(m))


def test_insufficient_disk_space_is_refused_before_downloading(tmp_path, fake):
    m = mgr(tmp_path, fake, nominal_total=8 * 1024 ** 3, disk_free=lambda _: 1024 ** 3)
    from vvlog_server.domain.errors import VvlogError
    with pytest.raises(VvlogError) as e:
        m.start(SECRET)
    assert e.value.code == "WEIGHTS_DISK_FULL" and m.job()["state"] == "idle" and not fake.log


def test_disk_space_is_rechecked_with_the_real_size(tmp_path, fake):
    m = mgr(tmp_path, fake, disk_free=lambda _: 1000)     # 事前チェックは通る(目安 0)が、実サイズ 1MiB には足りない
    job = run(m)
    assert job["state"] == "failed" and job["error"]["code"] == "WEIGHTS_DISK_FULL"
    assert not (tmp_path / "ckpt/gated/a.bin").exists()


def test_cancel_keeps_the_part_and_a_restart_resumes(tmp_path, fake):
    fake.slow = 0.02
    m = mgr(tmp_path, fake)
    m.chunk = 16384
    m.start(SECRET)
    for _ in range(200):
        if m.job().get("bytesDone", 0) > 100_000:
            break
        time.sleep(0.02)
    m.cancel()
    m.wait(10)
    assert m.job()["state"] == "canceled"
    part = tmp_path / "ckpt/gated/a.bin.part"
    got = part.stat().st_size
    assert 0 < got < len(A)
    fake.slow = 0
    assert run(m)["state"] == "succeeded"
    assert any(h.get("Range") == f"bytes={got}-" for _, h in fake.log)
    assert (tmp_path / "ckpt/gated/a.bin").read_bytes() == A


def test_only_one_job_at_a_time(tmp_path, fake):
    from vvlog_server.domain.errors import VvlogError
    fake.slow = 0.02
    m = mgr(tmp_path, fake)
    m.start(SECRET)
    with pytest.raises(VvlogError) as e:
        m.start(SECRET)
    assert e.value.code == "WEIGHTS_BUSY"
    m.cancel()
    m.wait(10)


def test_network_down_fails_with_resumable_part(tmp_path, fake):
    fake.drop_after = 50_000
    m = mgr(tmp_path, fake, retries=0)
    job = run(m)
    assert job["error"]["code"] == "WEIGHTS_NETWORK"
    assert (tmp_path / "ckpt/gated/a.bin.part").stat().st_size == 50_000
    assert run(m)["state"] == "succeeded"


def test_marker_detects_a_modified_file(tmp_path, fake):
    m = mgr(tmp_path, fake)
    run(m)
    (tmp_path / "ckpt/plain/b.bin").write_bytes(B + b"x")
    assert m.status()["files"][1]["state"] == "unverified" and m.status()["allReady"] is False
    assert (tmp_path / "ckpt" / MARKER).is_file()


def test_manifest_dest_cannot_escape_the_root(tmp_path, fake):
    from vvlog_server.domain.errors import VvlogError
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"files": [{"dest": "../evil", "source": "url", "url": f"{fake.base}/plain/b.bin"}]}))
    with pytest.raises(VvlogError):
        WeightsManager(p, tmp_path / "ckpt", nominal_total=0, disk_free=lambda _: 10 ** 12).start(None)


# --- HTTP API --------------------------------------------------------------------------------------------------------
PORT = 54399
SELF = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def api(tmp_path, fake):
    (tmp_path / "dist").mkdir()
    env = {"VVLOG_API_TOKEN": TOKEN, "VVLOG_DATA_ROOT": str(tmp_path / "data"), "VVLOG_LOCAL": "1", "VVLOG_PORT": str(PORT),
           "VVLOG_SPA_DIR": str(tmp_path / "dist"), "VVLOG_LOCAL_PROJECTS_DIR": str(tmp_path / "p"),
           "VVLOG_LOCAL_APPDATA_DIR": str(tmp_path / "a"), "VVLOG_LOCAL_CONFIG_DIR": str(tmp_path / "c"),
           "VVLOG_WEIGHTS_MANIFEST": str(manifest(tmp_path, fake)), "VVLOG_WEIGHTS_HF_BASE": fake.base}
    app = create_app(Settings.from_env(env))
    app.state.weights.nominal, app.state.weights.reserve = 0, 0
    app.state.weights.backoff = 0.01
    with TestClient(app, base_url=SELF) as c:
        c.headers.update(AUTH)
        c.app_ = app
        yield c


def test_api_defaults_the_root_to_app_data(api, tmp_path):
    assert api.get("/v1/local/weights").json()["root"] == str(tmp_path / "a" / "weights")


def test_api_requires_the_api_token(api):
    assert api.get("/v1/local/weights", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_api_start_status_and_sse(api, tmp_path):
    r = api.post("/v1/local/weights/start", json={"hfToken": SECRET})
    assert r.status_code == 202 and SECRET not in r.text
    with api.stream("GET", "/v1/local/weights/events") as s:
        text = "".join(s.iter_text())
    assert "event: progress" in text and "event: done" in text and '"state": "succeeded"' in text
    assert SECRET not in text
    st = api.get("/v1/local/weights").json()
    assert st["allReady"] is True and SECRET not in json.dumps(st)


def test_api_busy_is_409_and_cancel_works(api, fake):
    fake.slow = 0.02
    assert api.post("/v1/local/weights/start", json={"hfToken": SECRET}).status_code == 202
    r = api.post("/v1/local/weights/start", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "WEIGHTS_BUSY"
    assert api.post("/v1/local/weights/cancel").status_code == 200
    api.app_.state.weights.wait(10)
    assert api.get("/v1/local/weights").json()["job"]["state"] == "canceled"


def test_api_missing_token_surfaces_the_code(api):
    api.post("/v1/local/weights/start", json={})
    api.app_.state.weights.wait(10)
    err = api.get("/v1/local/weights").json()["job"]["error"]
    assert err["code"] == "HF_TOKEN_REQUIRED"


def test_api_start_refuses_a_foreign_origin(api):
    r = api.post("/v1/local/weights/start", json={"hfToken": SECRET}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_api_rejects_unknown_fields(api):
    assert api.post("/v1/local/weights/start", json={"token": "x"}).status_code == 422


def test_api_accepts_a_long_oauth_token(api):
    """`hf auth login` in the browser leaves an OAuth token of ~800 chars in ~/.cache/huggingface/token."""
    r = api.post("/v1/local/weights/start", json={"hfToken": "hf_oauth_" + "x" * 816})
    assert r.status_code == 202
