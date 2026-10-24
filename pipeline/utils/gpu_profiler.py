# gpu_profiler.py
#
# Unified profiler for PyTorch:
# - Measures time (auto formats: seconds / minutes / hours)
# - Measures GPU memory usage (alloc / reserved / peak / delta)
#
# Usage:
#   from gpu_profiler import gpu_profile
#   on_mask_generation = gpu_profile(on_mask_generation)
#   on_4d_generation   = gpu_profile(on_4d_generation)

import torch
import time


def _fmt_mem(bytes_val: int) -> str:
    """Format bytes to human-readable GB."""
    return f"{bytes_val / (1024 ** 3):.2f} GB"


def _fmt_time(sec: float) -> str:
    """
    Smart human-readable time formatter:
    - < 60 sec → "xx.xx s"
    - < 1 hour → "Xm Ys"
    - ≥ 1 hour → "Xh Ym Zs"
    """
    if sec < 60:
        return f"{sec:.2f} s"
    elif sec < 3600:
        m = int(sec // 60)
        s = sec % 60
        return f"{m:d}m {s:.1f}s"
    else:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h:d}h {m:d}m {s:.1f}s"


def gpu_profile(fn):
    """
    Decorator: Profile both GPU memory usage and runtime.
    Works even if CUDA is not available (time only).
    """
    def wrapped(*args, **kwargs):
        # ------------------ CPU ONLY MODE ------------------
        if not torch.cuda.is_available():
            t0 = time.time()
            out = fn(*args, **kwargs)
            t1 = time.time()
            print(f"[TIME PROF][{fn.__name__}] runtime={_fmt_time(t1 - t0)}")
            return out

        # ------------------ GPU MEMORY BASELINE ------------------
        torch.cuda.synchronize()
        base_alloc = torch.cuda.memory_allocated()
        base_reserved = torch.cuda.memory_reserved()

        torch.cuda.reset_peak_memory_stats()

        # ------------------ RUN FUNCTION ------------------
        t0 = time.time()
        out = fn(*args, **kwargs)
        torch.cuda.synchronize()
        t1 = time.time()

        # ------------------ AFTER METRICS ------------------
        end_alloc = torch.cuda.memory_allocated()
        end_reserved = torch.cuda.memory_reserved()
        peak_alloc = torch.cuda.max_memory_allocated()
        delta_peak = peak_alloc - base_alloc

        # ------------------ REPORT ------------------
        print(
            f"[PROF][{fn.__name__}] "
            f"time={_fmt_time(t1 - t0)},  "
            f"alloc={_fmt_mem(end_alloc)}, "
            f"reserved={_fmt_mem(end_reserved)}, "
            f"peak={_fmt_mem(peak_alloc)}, "
            f"delta_peak={_fmt_mem(delta_peak)}"
        )

        return out

    wrapped.__name__ = f"{fn.__name__}_gpu_profiled"
    return wrapped
