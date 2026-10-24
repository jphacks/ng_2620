"""Keep torch.compile / Triton build results in a persistent per-user folder.

Both caches are filled on the user's own machine by the first run and reused by every later run. Without them the
first avatar job spends ~54 s compiling (Penn 0400: 119.8 s -> 65.4 s with a filled cache, frames byte-identical).
torch's default inductor cache sits in %TEMP%, which Windows cleanup may empty. The cache keys include the GPU name
and the torch/Triton/CUDA versions, so a new GPU or upgrade only adds new entries.

Keep the root short. Triton writes <root>\\t\\<52-char hash>\\tmp.pid_<pid>_<uuid>\\<kernel name>.json and failed
with FileNotFoundError (Windows MAX_PATH) at a 70-character TRITON_CACHE_DIR.
"""
import os

MAX_ROOT = 50


def use_persistent_compile_cache():
    """Point the inductor and Triton caches at <SAMBODY4D_CACHE_DIR or %LOCALAPPDATA%\\SAMBody4D\\cc>.

    TORCHINDUCTOR_CACHE_DIR / TRITON_CACHE_DIR set by the caller win. Both are read when compiling, so call this
    any time before the first torch.compile / Triton launch. Returns the root, or None if the caller set both."""
    if "TORCHINDUCTOR_CACHE_DIR" in os.environ and "TRITON_CACHE_DIR" in os.environ:
        return None
    root = os.environ.get("SAMBODY4D_CACHE_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SAMBody4D", "cc")
    if len(root) > MAX_ROOT:
        print(f"[compile_cache] WARNING: {root} is {len(root)} characters; Triton may hit the Windows path limit. "
              f"Set SAMBODY4D_CACHE_DIR to a shorter folder.", flush=True)
    os.environ.setdefault("TORCHINDUCTOR_CACHE_DIR", os.path.join(root, "i"))
    os.environ.setdefault("TRITON_CACHE_DIR", os.path.join(root, "t"))
    return root
