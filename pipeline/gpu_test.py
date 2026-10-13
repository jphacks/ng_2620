"""Minimal Modal GPU check for SAM-Body4D.

Runs on a cloud GPU with the same PyTorch build the local env uses
(torch 2.7.1 + cu118, per README) and reports the GPU, its memory, and a
CUDA matmul. Usage:  modal run gpu_test.py
"""
import modal

GPU = "L40S"  # 48 GB, Ada (sm_89) like the local RTX 4070 SUPER

image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "torch==2.7.1", index_url="https://download.pytorch.org/whl/cu118"
)
app = modal.App("sam-body4d-gpu-test", image=image)


@app.function(gpu=GPU, timeout=600)
def check():
    import subprocess
    import time

    import torch

    print(subprocess.run(["nvidia-smi"], capture_output=True, text=True).stdout)
    assert torch.cuda.is_available(), "CUDA not available"
    p = torch.cuda.get_device_properties(0)
    x = torch.randn(8192, 8192, device="cuda")
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(10):
        y = x @ x
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    return {
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": p.name,
        "capability": f"{p.major}.{p.minor}",
        "total_memory_GB": round(p.total_memory / 1024 ** 3, 1),
        "matmul_8192_x10_s": round(dt, 3),
        "tflops_fp32": round(10 * 2 * 8192 ** 3 / dt / 1e12, 1),
        "checksum": float(y[0, 0]),
    }


@app.local_entrypoint()
def main():
    print(check.remote())
