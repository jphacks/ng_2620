"""Local app server mode (#22): SPA serving, Host/Origin guard, /v1/local/info, free-port binding."""
import socket

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH, TOKEN
from vvlog_server.config import Settings
from vvlog_server.interface.local import bind_local_socket
from vvlog_server.main import create_app

PORT = 54321
SELF = f"http://127.0.0.1:{PORT}"
PAGES = "https://pages.example"


@pytest.fixture
def spa(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>vvlog</title>", encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    return d


@pytest.fixture
def local(tmp_path, spa):
    from fake_pipeline import FakePipelineRunner
    env = {"VVLOG_API_TOKEN": TOKEN, "VVLOG_DATA_ROOT": str(tmp_path / "data"), "VVLOG_LOCAL": "1",
           "VVLOG_PORT": str(PORT), "VVLOG_SPA_DIR": str(spa), "VVLOG_ALLOWED_ORIGINS": PAGES}
    with TestClient(create_app(Settings.from_env(env), runner_factory=FakePipelineRunner), base_url=SELF) as c:
        yield c


# --- the SPA is served -----------------------------------------------------------------------------------------------
def test_root_serves_index(local):
    r = local.get("/")
    assert r.status_code == 200 and "<title>vvlog</title>" in r.text


def test_static_asset_and_spa_fallback(local):
    assert local.get("/assets/app.js").text == "console.log(1)"
    r = local.get("/project/abc/editor")  # client-side route
    assert r.status_code == 200 and "<title>vvlog</title>" in r.text


def test_missing_asset_is_404_not_app_shell(local):
    assert local.get("/assets/missing.js").status_code == 404


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt",
                                  "/%2e%2e%2fsecret.txt", "/..%5csecret.txt"])
def test_path_traversal_is_refused(local, path):
    r = local.get(path)
    assert "top secret" not in r.text


def test_api_routes_are_not_shadowed_by_spa(local):
    assert local.get("/v1/health").json()["status"] == "ok"
    assert local.get("/v1/queue").status_code == 401           # still behind the token
    assert local.get("/v1/queue", headers=AUTH).status_code == 200
    r = local.get("/v1/nope", headers=AUTH)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_spa_not_built_gives_a_hint(tmp_path):
    env = {"VVLOG_API_TOKEN": TOKEN, "VVLOG_DATA_ROOT": str(tmp_path / "d"), "VVLOG_LOCAL": "1",
           "VVLOG_PORT": str(PORT), "VVLOG_SPA_DIR": str(tmp_path / "nothing")}
    with TestClient(create_app(Settings.from_env(env)), base_url=SELF) as c:
        r = c.get("/")
        assert r.status_code == 404 and r.json()["error"]["code"] == "SPA_NOT_BUILT"
        assert c.get("/v1/local/info").json()["spa"] is False


# --- /v1/local/info ----------------------------------------------------------------------------------------------------
def test_info_tells_the_spa_it_is_in_full_mode(local):
    r = local.get("/v1/local/info")
    body = r.json()
    assert r.status_code == 200 and body["mode"] == "local" and body["port"] == PORT
    assert body["spa"] is True and body["apiVersion"] == 1
    assert r.headers["cache-control"] == "no-store"


def test_info_hands_the_token_to_same_origin_only(local):
    assert local.get("/v1/local/info").json()["token"] == TOKEN
    assert local.get("/v1/local/info", headers={"Origin": SELF}).json()["token"] == TOKEN
    assert local.get("/v1/local/info", headers={"Sec-Fetch-Site": "same-origin"}).json()["token"] == TOKEN
    # the allowed Pages origin may detect the server, but must not receive the token
    r = local.get("/v1/local/info", headers={"Origin": PAGES})
    assert r.status_code == 200 and "token" not in r.json()
    # a cross-site no-cors request (no Origin on GET) is recognised by Sec-Fetch-Site
    assert "token" not in local.get("/v1/local/info", headers={"Sec-Fetch-Site": "cross-site"}).json()


# --- Host / Origin guard -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("host", ["evil.example", "evil.example:54321", "127.0.0.1.evil.example:54321",
                                  "127.0.0.1:9999", "127.0.0.1", "192.168.0.5:54321", ""])
def test_foreign_host_is_rejected(local, host):
    r = local.get("/v1/local/info", headers={"Host": host})
    assert r.status_code == 403 and r.json()["error"]["code"] == "HOST_NOT_ALLOWED"


@pytest.mark.parametrize("host", [f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"LOCALHOST:{PORT}", f"[::1]:{PORT}"])
def test_loopback_hosts_are_accepted(local, host):
    assert local.get("/v1/local/info", headers={"Host": host}).status_code == 200


def test_foreign_origin_is_rejected_on_reads_and_writes(local):
    evil = {"Origin": "https://evil.example"}
    r = local.get("/v1/local/info", headers=evil)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    r = local.post("/v1/projects", headers={**AUTH, **evil}, json={"name": "x"})
    assert r.status_code == 403
    assert local.get("/", headers=evil).status_code == 403
    # even with the right token, a page on another origin cannot talk to the server
    assert local.get("/v1/queue", headers={**AUTH, **evil}).status_code == 403


def test_no_origin_header_is_allowed(local):  # curl, the launcher, same-origin navigations
    assert local.post("/v1/projects", headers=AUTH, json={"name": "x"}).status_code == 201


def test_pages_origin_passes_with_cors_headers(local):
    r = local.get("/v1/queue", headers={**AUTH, "Origin": PAGES})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == PAGES
    pre = local.options("/v1/projects", headers={"Origin": PAGES, "Access-Control-Request-Method": "POST",
                                                 "Access-Control-Request-Headers": "authorization,content-type"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == PAGES


def test_preflight_from_foreign_origin_is_rejected(local):
    r = local.options("/v1/projects", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.status_code == 403


def test_tauri_origins_are_no_longer_allowed(local):
    assert local.get("/v1/local/info", headers={"Origin": "tauri://localhost"}).status_code == 403


# --- remote (non-local) mode is unchanged --------------------------------------------------------------------------------
def test_remote_mode_has_no_guard_and_no_spa(client):
    assert client.get("/v1/local/info").status_code == 404
    assert client.get("/", headers={"Host": "gpu.tailnet.ts.net"}).status_code == 404


# --- binding -------------------------------------------------------------------------------------------------------------
def test_bind_picks_a_free_loopback_port():
    a, b = bind_local_socket(0), bind_local_socket(0)
    try:
        (ha, pa), (hb, pb) = a.getsockname(), b.getsockname()
        assert ha == hb == "127.0.0.1" and pa != pb and pa > 0
    finally:
        a.close()
        b.close()


def test_bind_refuses_a_port_in_use():
    a = bind_local_socket(0)
    a.listen()
    try:
        with pytest.raises(OSError):
            bind_local_socket(a.getsockname()[1])
    finally:
        a.close()


def test_server_really_listens_on_loopback_only():
    s = bind_local_socket(0)
    try:
        assert s.getsockname()[0] == "127.0.0.1" and s.family == socket.AF_INET
    finally:
        s.close()
