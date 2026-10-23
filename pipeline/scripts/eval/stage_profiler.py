"""Wall time of model calls inside the pipeline's stages, by wrapping functions / methods in place.

Every wrapped call is bracketed by torch.cuda.synchronize(), so its time is the GPU work it issued, not just the launch.
That removes the overlap the pipeline relies on in a few places (e.g. fusion v2's async 'person' grounding), so the
sum of the parts can exceed the stage's production time slightly; the stage totals are measured separately.

  patch(obj, "attr", "group/name")      obj.attr -> timed (a class: every instance; an instance: that one only)
  REC["group/name"] = [calls, seconds, [per-call seconds]]
"""
import functools
import time

REC = {}


def _sync():
    import torch
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def timed(name, fn, key=None):
    """key(args, kwargs) -> suffix: record the call under name/<suffix> (e.g. body / hand passes of one method)."""
    @functools.wraps(fn)
    def w(*a, **k):
        _sync()
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            _sync()
            n = name if key is None else f"{name}/{key(a, k)}"
            r = REC.setdefault(n, [0, 0.0, []])
            dt = time.perf_counter() - t0
            r[0] += 1
            r[1] += dt
            r[2].append(dt)
    w.__wrapped_timed__ = True
    return w


def patch(obj, attr, name, key=None):
    fn = getattr(obj, attr)
    if getattr(fn, "__wrapped_timed__", False):
        return
    setattr(obj, attr, timed(name, fn, key))


class Stage:
    """Wall time and peak GPU memory of one stage -> stages[name]."""

    def __init__(self, stages, name):
        self.stages, self.name = stages, name

    def __enter__(self):
        import torch
        _sync()
        torch.cuda.reset_peak_memory_stats()
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        import torch
        _sync()
        self.stages[self.name] = dict(time_s=time.perf_counter() - self.t0,
                                      peak_alloc_GB=torch.cuda.max_memory_allocated() / 2**30,
                                      peak_reserved_GB=torch.cuda.max_memory_reserved() / 2**30)
        print(f"[STAGE][{self.name}] {self.stages[self.name]}", flush=True)


def records():
    """median_ms / first_ms: per call; the first call carries one-time work (torch.compile, cuDNN autotuning)."""
    import statistics
    return {k: dict(calls=n, time_s=s, first_ms=1000 * d[0], median_ms=1000 * statistics.median(d),
                    steady_s=s - d[0] + statistics.median(d))
            for k, (n, s, d) in sorted(REC.items())}
