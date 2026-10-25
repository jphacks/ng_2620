"""初回ブートストラップ(#32、ADR-0015): uv で環境 B(pipeline の torch/CUDA 環境)を作る。

  * 手順(detect → venv → torch → detectron2 → deps → verify)ごとに完了を `<env>/.vvlog-bootstrap.json` へ記録する。
    途中で止まっても、次の start は終わった手順を飛ばして続きから進む(uv pip install 自体も冪等)
  * GPU 判定(`nvidia-smi`)で非対応と分かったら始めない(409 BOOTSTRAP_UNSUPPORTED)。画面は制限モードか外部 GPU(BYO)へ案内する
  * 版の決まりは bootstrap-spec.json。`provisional` の間は #30(実機検証)で確定するまでの暫定値
  * 同時に走るのは 1 ジョブだけ。キャンセルは実行中のコマンドを止める(完了済みの手順は残る)
  * 実行するコマンドは run(cmd, on_line, cancel) 経由。テストはここを差し替える
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from ..config import env_b_python
from ..domain.errors import VvlogError

MARKER = ".vvlog-bootstrap.json"
STEPS = ("detect", "venv", "torch", "detectron2", "deps", "verify")
STEP_LABEL = {"detect": "GPU の確認", "venv": "Python 環境の作成", "torch": "torch(CUDA)の導入", "detectron2": "detectron2 の導入",
              "deps": "ライブラリの導入", "verify": "動作確認"}
LOG_LINES = 200


class Canceled(Exception):
    pass


class StepFailed(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


def _ver(s: str) -> tuple[int, ...]:
    out = []
    for p in s.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def query_gpu() -> dict:
    """nvidia-smi から先頭の GPU を読む。無ければ {"present": False, "reason": ...}。"""
    try:
        cp = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,compute_cap",
                             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return {"present": False, "reason": "NO_NVIDIA_DRIVER"}
    except (subprocess.TimeoutExpired, OSError):
        return {"present": False, "reason": "NVIDIA_SMI_FAILED"}
    if cp.returncode != 0 or not cp.stdout.strip():
        if "compute_cap" in (cp.stderr + cp.stdout):  # 古いドライバは compute_cap を知らない: 付けずにもう一度
            try:
                cp = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                                     "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                return {"present": False, "reason": "NVIDIA_SMI_FAILED"}
        if cp.returncode != 0 or not cp.stdout.strip():
            return {"present": False, "reason": "NVIDIA_SMI_FAILED"}
    f = [x.strip() for x in cp.stdout.strip().splitlines()[0].split(",")]
    gpu = {"present": True, "name": f[0], "driver": f[1], "vramGiB": round(float(f[2]) / 1024, 1) if f[2].replace(".", "").isdigit() else None}
    if len(f) > 3 and f[3].replace(".", "").isdigit():
        gpu["computeCap"] = f[3]
    return gpu


def judge_gpu(gpu: dict, rule: dict) -> dict:
    """{"verdict": "supported" | "unsupported" | "unknown", "reasons": [...], "warnings": [...]}。reasons があれば始めない。"""
    if not gpu.get("present"):
        return {"verdict": "unsupported", "reasons": [gpu.get("reason", "NO_NVIDIA_DRIVER")], "warnings": []}
    reasons, warnings = [], []
    cap = gpu.get("computeCap")
    if cap is None:
        warnings.append("COMPUTE_CAP_UNKNOWN")
    elif _ver(cap) < _ver(rule["minComputeCap"]):
        reasons.append("GPU_TOO_OLD")
    if _ver(gpu["driver"]) < _ver(rule["minDriver"]):
        reasons.append("DRIVER_TOO_OLD")
    if gpu.get("vramGiB") is not None and gpu["vramGiB"] < rule["minVramGiB"] - 0.5:  # 12GB 目安: 足りなくても止めず注意に留める
        warnings.append("LOW_VRAM")
    verdict = "unsupported" if reasons else ("unknown" if "COMPUTE_CAP_UNKNOWN" in warnings else "supported")
    return {"verdict": verdict, "reasons": reasons, "warnings": warnings}


def default_run(cmd: list[str], on_line: Callable[[str], None], cancel: threading.Event, env: dict | None = None,
                cwd: Path | None = None) -> int:
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW
    p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                         env={**os.environ, **(env or {})}, cwd=str(cwd) if cwd else None, **kw)
    done = threading.Event()

    def watch():  # キャンセルされたら子を止める
        while not done.is_set():
            if cancel.wait(0.2):
                p.terminate()
                return
    threading.Thread(target=watch, daemon=True).start()
    try:
        for line in p.stdout:
            on_line(line.rstrip())
        return p.wait()
    finally:
        done.set()


class BootstrapManager:
    def __init__(self, spec_path: Path, env_dir: Path, pipeline_root: Path, *, uv: str | None,
                 run: Callable[..., int] = default_run, gpu: Callable[[], dict] = query_gpu,
                 disk_free: Callable[[Path], int] | None = None):
        self.spec_path, self.env_dir, self.pipeline_root, self.uv = Path(spec_path), Path(env_dir), Path(pipeline_root), uv
        self._run, self._gpu = run, gpu
        self._disk_free = disk_free
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._job: dict = {"state": "idle"}
        self._log: deque[str] = deque(maxlen=LOG_LINES)

    # --- spec / marker --------------------------------------------------------------------------------------------
    def spec(self) -> dict:
        return json.loads(self.spec_path.read_text(encoding="utf-8"))

    def _spec_hash(self, spec: dict) -> str:
        return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]

    def _done(self, spec: dict) -> list[str]:
        try:
            m = json.loads((self.env_dir / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return m.get("done", []) if m.get("spec") == self._spec_hash(spec) else []  # 決まりが変わったら最初から確かめ直す

    def _mark(self, spec: dict, done: list[str]) -> None:
        self.env_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.env_dir / (MARKER + ".tmp")
        tmp.write_text(json.dumps({"spec": self._spec_hash(spec), "done": done}), encoding="utf-8")
        os.replace(tmp, self.env_dir / MARKER)

    def python(self) -> Path:
        return env_b_python(self.env_dir)

    # --- status ---------------------------------------------------------------------------------------------------
    def status(self) -> dict:
        spec = self.spec()
        gpu = self._gpu()
        done = self._done(spec)
        ready = "verify" in done and self.python().is_file()
        return {"ready": ready, "provisional": bool(spec.get("provisional")), "envDir": str(self.env_dir),
                "uv": bool(self.uv), "gpu": gpu, "judgement": judge_gpu(gpu, spec["gpu"]),
                "steps": [{"id": s, "label": STEP_LABEL[s], "done": s in done} for s in STEPS],
                "job": self.job()}

    def job(self) -> dict:
        with self._lock:
            return {**self._job, "log": list(self._log)[-40:]} if self._job.get("state") != "idle" else {"state": "idle"}

    # --- control --------------------------------------------------------------------------------------------------
    def start(self, force: bool = False) -> dict:
        spec = self.spec()
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise VvlogError("BOOTSTRAP_BUSY", "セットアップは実行中です")
        if not self.uv:
            raise VvlogError("BOOTSTRAP_UV_MISSING", "uv が見つかりません。インストーラーに含まれる uv か、PATH 上の uv が必要です")
        j = judge_gpu(self._gpu(), spec["gpu"])
        if j["reasons"] and not force:
            raise VvlogError("BOOTSTRAP_UNSUPPORTED", "この PC の GPU では解析環境を作れません。制限モードか、自前の外部 GPU(BYO)を使ってください",
                             {"reasons": j["reasons"]})
        self._cancel.clear()
        with self._lock:
            self._log.clear()
            self._job = {"state": "running", "step": None, "startedAt": time.time(), "steps": {}}
            self._thread = threading.Thread(target=self._work, args=(spec,), daemon=True)
            self._thread.start()
        return self.job()

    def cancel(self) -> dict:
        self._cancel.set()
        return {"state": "canceling"}

    def wait(self, timeout: float = 10.0) -> None:  # tests
        t = self._thread
        if t:
            t.join(timeout)

    # --- the work -------------------------------------------------------------------------------------------------
    def _set(self, **kw) -> None:
        with self._lock:
            self._job.update(kw)

    def _line(self, line: str) -> None:
        with self._lock:
            self._log.append(line)

    def _cmd(self, cmd: list, env: dict | None = None, cwd: Path | None = None) -> None:
        self._line("$ " + " ".join(map(str, cmd)))
        if self._cancel.is_set():
            raise Canceled()
        rc = self._run(cmd, self._line, self._cancel, env=env, cwd=cwd)
        if self._cancel.is_set():
            raise Canceled()
        if rc != 0:
            raise StepFailed("BOOTSTRAP_COMMAND_FAILED", f"コマンドが失敗しました(終了コード {rc}): {Path(str(cmd[0])).name} {str(cmd[1]) if len(cmd) > 1 else ''}".strip(),
                             "ネットワークとディスクの空きを確かめて、もう一度始めると続きから進みます")

    def _pip(self, *args: str) -> list:
        return [self.uv, "pip", *args, "-p", str(self.python())]

    def _work(self, spec: dict) -> None:
        done = self._done(spec)
        py = self.python()
        try:
            for step in STEPS:
                if step in done and (step == "detect" or py.is_file()):
                    self._set(step=step, steps={**self._job["steps"], step: "skipped"})
                    continue
                self._set(step=step, steps={**self._job["steps"], step: "running"})
                self._line(f"==> {STEP_LABEL[step]}")
                getattr(self, "_s_" + step)(spec)
                done = [*done, step]
                self._mark(spec, done)
                self._set(steps={**self._job["steps"], step: "done"})
            self._set(state="succeeded", step=None, finishedAt=time.time())
        except Canceled:
            self._set(state="canceled", finishedAt=time.time())
        except StepFailed as e:
            self._set(state="failed", finishedAt=time.time(), error={"code": e.code, "message": e.message, "hint": e.hint, "step": self._job.get("step")})
        except BaseException as e:  # noqa: BLE001 - スレッドが黙って死なないように
            self._set(state="failed", finishedAt=time.time(), error={"code": "BOOTSTRAP_INTERNAL", "message": f"{type(e).__name__}: {e}"[:300], "hint": None, "step": self._job.get("step")})

    def _s_detect(self, spec: dict) -> None:
        j = judge_gpu(self._gpu(), spec["gpu"])
        if j["reasons"]:
            raise StepFailed("BOOTSTRAP_UNSUPPORTED", "GPU が対応していません: " + ", ".join(j["reasons"]))
        if self._disk_free:
            free = self._disk_free(self.env_dir)
            if free < 15 * 1024 ** 3:  # torch + 依存で 10GB 前後(#30 の実測で確定)
                raise StepFailed("BOOTSTRAP_DISK_FULL", f"空き容量が足りません(あと {(15 * 1024 ** 3 - free) // 1024 ** 3 + 1}GB 以上必要)", "不要なファイルを消してから、もう一度始めてください")

    def _s_venv(self, spec: dict) -> None:
        self.env_dir.parent.mkdir(parents=True, exist_ok=True)
        self._cmd([self.uv, "venv", str(self.env_dir), "-p", spec["python"], "--allow-existing"])

    def _s_torch(self, spec: dict) -> None:
        t = spec["torch"]
        self._cmd(self._pip("install", *t["packages"], "--index-url", f"https://download.pytorch.org/whl/{t['index']}"))

    def _s_detectron2(self, spec: dict) -> None:
        d = spec["detectron2"]
        if not d.get("wheelUrl"):
            raise StepFailed("BOOTSTRAP_NO_WHEEL", "detectron2 の配布 wheel がまだ設定されていません(#51)",
                             "bootstrap-spec.json の detectron2.wheelUrl が決まるまで、この手順は完了できません")
        url = d["wheelUrl"] + (f"#sha256={d['sha256']}" if d.get("sha256") else "")  # pip が取得物のハッシュを検証する
        self._cmd(self._pip("install", "--no-deps", url))

    def _s_deps(self, spec: dict) -> None:
        self._cmd(self._pip("install", *spec["packages"]))
        self._cmd(self._pip("install", *spec["git"]))
        for u in spec.get("uninstall", []):
            self._cmd([self.uv, "pip", "uninstall", u, "-p", str(self.python())])
        self._cmd(self._pip("install", "--no-deps", *spec["gitNoDeps"]))
        for e in spec.get("editable", []):
            self._cmd(self._pip("install", "--no-deps", "-e", str(self.pipeline_root / e)))

    def _s_verify(self, spec: dict) -> None:
        code = ("import torch,sam3,detectron2,moge,utils3d,pyrender,moderngl,decord,ai_edge_litert,transformers,diffusers,pytorch_lightning;"
                "assert torch.cuda.is_available(), 'torch.cuda.is_available() is False';"
                "x=torch.ones(8,device='cuda');assert float(x.sum())==8.0")
        self._cmd([self.python(), "-c", code], cwd=self.pipeline_root)
