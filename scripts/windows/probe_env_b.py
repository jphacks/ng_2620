#!/usr/bin/env python3
"""環境 B の中で動かす確認（#30）。verify-env-b.ps1 から呼ばれる。結果を JSON で標準出力に 1 つ出す。

    <環境 B の python> scripts/windows/probe_env_b.py [--pipeline-dir pipeline]
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time

MODULES = [
    "torch", "torchvision", "sam3", "detectron2", "moge", "utils3d", "pyrender", "moderngl",
    "decord", "ai_edge_litert", "transformers", "diffusers", "pytorch_lightning",
    "cv2", "huggingface_hub",
]


def import_all() -> dict:
    out = {}
    for name in MODULES:
        t = time.time()
        try:
            m = importlib.import_module(name)
            out[name] = {"ok": True, "version": getattr(m, "__version__", None), "sec": round(time.time() - t, 2)}
        except BaseException as e:  # noqa: BLE001 - import 失敗の理由を全部拾う
            out[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    return out


def cuda_info() -> dict:
    try:
        import torch
    except BaseException as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:300]}
    info = {
        "torch": torch.__version__,
        "cuda_build": torch.version.cuda,
        "available": torch.cuda.is_available(),
        "arch_list": torch.cuda.get_arch_list() if hasattr(torch.cuda, "get_arch_list") else None,
    }
    if not info["available"]:
        return info
    p = torch.cuda.get_device_properties(0)
    cap = f"sm_{p.major}{p.minor}"
    info.update(
        device=p.name,
        capability=cap,
        vram_total_gb=round(p.total_memory / 2**30, 2),
        arch_supported=cap in (info["arch_list"] or []),
    )
    try:  # 実際に GPU で計算できるか（arch 非対応だとここで落ちる）
        a = torch.randn(2048, 2048, device="cuda", dtype=torch.float16)
        torch.cuda.synchronize()
        t = time.time()
        for _ in range(10):
            a = (a @ a).clamp(-1, 1)
        torch.cuda.synchronize()
        info["matmul_ok"] = bool(torch.isfinite(a).all().item())
        info["matmul_sec"] = round(time.time() - t, 3)
        info["vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
    except BaseException as e:  # noqa: BLE001
        info["matmul_ok"] = False
        info["matmul_error"] = f"{type(e).__name__}: {e}"[:300]
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline-dir", default="pipeline")
    args = ap.parse_args()
    # pipeline の中の相対 import（models/sam3 など）が通る位置から確かめる
    if os.path.isdir(args.pipeline_dir):
        os.chdir(args.pipeline_dir)
        sys.path.insert(0, os.getcwd())
    report = {"python": sys.version.split()[0], "platform": sys.platform}
    report["imports"] = import_all()
    report["cuda"] = cuda_info()
    report["ok"] = all(v["ok"] for v in report["imports"].values()) and bool(report["cuda"].get("matmul_ok"))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
