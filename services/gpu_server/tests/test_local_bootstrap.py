"""初回ブートストラップ(#32)。uv も GPU も実物は使わない: コマンド実行(run)と nvidia-smi(gpu)を差し替えて、
手順の順序・冪等/再開・GPU 判定・キャンセル・API を確かめる。"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH, TOKEN
from vvlog_server.adapters.local_bootstrap import MARKER, STEPS, BootstrapManager, judge_gpu
from vvlog_server.config import Settings
from vvlog_server.domain.errors import VvlogError
from vvlog_server.main import create_app

RULE = {"minComputeCap": "8.0", "minVramGiB": 12, "minDriver": "570.0"}
RTX5060TI = {"present": True, "name": "NVIDIA GeForce RTX 5060 Ti", "driver": "572.16", "vramGiB": 16.0, "computeCap": "12.0"}


def write_spec(tmp_path, **over):
    spec = {"provisional": True, "python": "3.12", "torch": {"index": "cu128", "packages": ["torch", "torchvision"]},
            "detectron2": {"wheelUrl": "https://example.invalid/detectron2.whl", "sha256": None},
            "packages": ["einops"], "git": ["git+https://example.invalid/MoGe.git"], "uninstall": ["utils3d_moge"],
            "gitNoDeps": ["git+https://example.invalid/utils3d.git"], "editable": ["models/sam3"], "gpu": RULE, **over}
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return p


class FakeRun:
    """uv venv が python を作ったことにする。fail_on に含まれる語を持つコマンドは失敗する。"""

    def __init__(self, env_dir, fail_on=(), block_on=None):
        self.cmds, self.env_dir, self.fail_on, self.block_on = [], env_dir, set(fail_on), block_on
        self.entered = threading.Event()

    def __call__(self, cmd, on_line, cancel, env=None, cwd=None):
        cmd = [str(c) for c in cmd]
        self.cmds.append(cmd)
        on_line("running " + cmd[1] if len(cmd) > 1 else "running")
        if cmd[1:2] == ["venv"]:
            from vvlog_server.config import env_b_python
            p = env_b_python(self.env_dir)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("")
        if self.block_on and any(self.block_on in c for c in cmd):
            self.entered.set()
            cancel.wait(5)
            return 1
        return 1 if any(f in " ".join(cmd) for f in self.fail_on) else 0


def mgr(tmp_path, run=None, gpu=RTX5060TI, spec=None, uv="uv", **kw):
    env = tmp_path / "envs" / "pipeline"
    run = run or FakeRun(env)
    m = BootstrapManager(spec or write_spec(tmp_path), env, tmp_path / "pipeline", uv=uv, run=run, gpu=lambda: gpu, **kw)
    return m, run


# --- GPU 判定 --------------------------------------------------------------------------------------------------------
def test_judge_supported_rtx_5060_ti():
    assert judge_gpu(RTX5060TI, RULE) == {"verdict": "supported", "reasons": [], "warnings": []}


@pytest.mark.parametrize("gpu,reason", [
    ({"present": False, "reason": "NO_NVIDIA_DRIVER"}, "NO_NVIDIA_DRIVER"),
    ({**RTX5060TI, "computeCap": "7.5", "name": "RTX 2080"}, "GPU_TOO_OLD"),
    ({**RTX5060TI, "driver": "551.23"}, "DRIVER_TOO_OLD"),
])
def test_judge_unsupported(gpu, reason):
    j = judge_gpu(gpu, RULE)
    assert j["verdict"] == "unsupported" and reason in j["reasons"]


def test_judge_low_vram_only_warns_and_unknown_cap_is_allowed():
    j = judge_gpu({**RTX5060TI, "vramGiB": 8.0}, RULE)
    assert j["verdict"] == "supported" and j["warnings"] == ["LOW_VRAM"]
    j = judge_gpu({k: v for k, v in RTX5060TI.items() if k != "computeCap"}, RULE)
    assert j["verdict"] == "unknown" and not j["reasons"]


# --- 手順 ------------------------------------------------------------------------------------------------------------
def test_runs_every_step_in_order_and_marks_ready(tmp_path):
    m, run = mgr(tmp_path)
    assert m.status()["ready"] is False
    m.start()
    m.wait()
    job = m.job()
    assert job["state"] == "succeeded", job
    assert [job["steps"][s] for s in STEPS] == ["done"] * len(STEPS)
    flat = [" ".join(c) for c in run.cmds]
    order = [next(i for i, c in enumerate(flat) if key in c) for key in ("venv", "torch", "detectron2.whl", "einops", "MoGe", "utils3d.git", "-c import torch")]
    assert order == sorted(order)
    assert any("download.pytorch.org/whl/cu128" in c for c in flat)
    assert any("uninstall utils3d_moge" in c for c in flat)
    assert m.status()["ready"] is True


def test_resumes_after_a_failure_without_redoing_finished_steps(tmp_path):
    m, run = mgr(tmp_path, run=FakeRun(tmp_path / "envs" / "pipeline", fail_on=["einops"]))
    m.start()
    m.wait()
    job = m.job()
    assert job["state"] == "failed" and job["error"]["code"] == "BOOTSTRAP_COMMAND_FAILED" and job["error"]["step"] == "deps"
    assert json.loads((m.env_dir / MARKER).read_text())["done"] == ["detect", "venv", "torch", "detectron2"]
    m2, run2 = mgr(tmp_path)  # 直して、もう一度(別プロセスでも同じ)
    m2.start()
    m2.wait()
    assert m2.job()["state"] == "succeeded"
    assert m2.job()["steps"]["venv"] == "skipped" and m2.job()["steps"]["torch"] == "skipped"
    assert not any("venv" in c[1:2] or "torch" in " ".join(c) and "index-url" in " ".join(c) for c in run2.cmds)


def test_done_marker_is_discarded_when_the_spec_changes(tmp_path):
    m, _ = mgr(tmp_path)
    m.start()
    m.wait()
    m2, run2 = mgr(tmp_path, spec=write_spec(tmp_path, python="3.13"))
    assert m2.status()["ready"] is False
    m2.start()
    m2.wait()
    assert any(c[1:2] == ["venv"] and "3.13" in c for c in run2.cmds)


def test_missing_wheel_is_a_clear_failure(tmp_path):
    m, _ = mgr(tmp_path, spec=write_spec(tmp_path, detectron2={"wheelUrl": None, "sha256": None}))
    m.start()
    m.wait()
    assert m.job()["state"] == "failed" and m.job()["error"]["code"] == "BOOTSTRAP_NO_WHEEL" and m.job()["error"]["step"] == "detectron2"


def test_unsupported_gpu_refuses_to_start_unless_forced(tmp_path):
    m, run = mgr(tmp_path, gpu={"present": False, "reason": "NO_NVIDIA_DRIVER"})
    with pytest.raises(VvlogError) as e:
        m.start()
    assert e.value.code == "BOOTSTRAP_UNSUPPORTED" and e.value.detail == {"reasons": ["NO_NVIDIA_DRIVER"]} and not run.cmds
    s = m.status()
    assert s["judgement"]["verdict"] == "unsupported" and s["ready"] is False


def test_start_needs_uv_and_refuses_a_second_job(tmp_path):
    with pytest.raises(VvlogError) as e:
        mgr(tmp_path, uv=None)[0].start()
    assert e.value.code == "BOOTSTRAP_UV_MISSING"
    run = FakeRun(tmp_path / "envs" / "pipeline", block_on="torch")
    m, _ = mgr(tmp_path, run=run)
    m.start()
    assert run.entered.wait(5)
    with pytest.raises(VvlogError) as e:
        m.start()
    assert e.value.code == "BOOTSTRAP_BUSY"
    m.cancel()
    m.wait()


def test_cancel_keeps_finished_steps(tmp_path):
    run = FakeRun(tmp_path / "envs" / "pipeline", block_on="torch")
    m, _ = mgr(tmp_path, run=run)
    m.start()
    assert run.entered.wait(5)
    m.cancel()
    m.wait()
    assert m.job()["state"] == "canceled"
    assert json.loads((m.env_dir / MARKER).read_text())["done"] == ["detect", "venv"]


def test_disk_space_is_checked_up_front(tmp_path):
    m, run = mgr(tmp_path, disk_free=lambda _: 1024 ** 3)
    m.start()
    m.wait()
    assert m.job()["error"]["code"] == "BOOTSTRAP_DISK_FULL" and not run.cmds


# --- HTTP API --------------------------------------------------------------------------------------------------------
PORT = 54398
SELF = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def api(tmp_path):
    (tmp_path / "dist").mkdir()
    env = {"VVLOG_API_TOKEN": TOKEN, "VVLOG_DATA_ROOT": str(tmp_path / "data"), "VVLOG_LOCAL": "1", "VVLOG_PORT": str(PORT),
           "VVLOG_SPA_DIR": str(tmp_path / "dist"), "VVLOG_LOCAL_PROJECTS_DIR": str(tmp_path / "p"),
           "VVLOG_LOCAL_APPDATA_DIR": str(tmp_path / "a"), "VVLOG_LOCAL_CONFIG_DIR": str(tmp_path / "c"),
           "VVLOG_BOOTSTRAP_SPEC": str(write_spec(tmp_path)), "VVLOG_UV": ""}
    app = create_app(Settings.from_env(env, uv="uv"))
    b = app.state.bootstrap
    b._gpu, b._run = (lambda: RTX5060TI), FakeRun(b.env_dir)
    with TestClient(app, base_url=SELF) as c:
        c.headers.update(AUTH)
        c.app_ = app
        yield c


def test_api_defaults_env_b_under_app_data_and_points_the_pipeline_at_it(api, tmp_path):
    st = api.get("/v1/local/bootstrap").json()
    assert st["envDir"] == str(tmp_path / "a" / "envs" / "pipeline") and st["ready"] is False
    assert [s["id"] for s in st["steps"]] == list(STEPS)
    assert api.app_.state.bootstrap.python().parent.parent == tmp_path / "a" / "envs" / "pipeline"


def test_api_requires_the_token_and_same_origin(api):
    assert api.get("/v1/local/bootstrap", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = api.post("/v1/local/bootstrap/start", headers={"Origin": "https://evil.example"})
    assert r.status_code in (403,)


def test_api_start_and_sse(api):
    r = api.post("/v1/local/bootstrap/start", json={})
    assert r.status_code == 202
    with api.stream("GET", "/v1/local/bootstrap/events") as s:
        text = "".join(s.iter_text())
    assert "event: progress" in text and "event: done" in text and '"state": "succeeded"' in text
    assert api.get("/v1/local/bootstrap").json()["ready"] is True


def test_api_unsupported_gpu_is_409(api):
    api.app_.state.bootstrap._gpu = lambda: {"present": False, "reason": "NO_NVIDIA_DRIVER"}
    r = api.post("/v1/local/bootstrap/start", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "BOOTSTRAP_UNSUPPORTED"
