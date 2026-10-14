"""Reuse of fusion v2 intermediates that the frozen stages recompute, with unchanged values.

evidence, fusion.render and the avatar's AvatarHider each load the same cache files (prep.load: mesh.npz with every
frame's mesh depth and image; gpu_stage.load_cands: every candidate mask unpacked), and fusion.render and
AvatarHider.raw both call evidence.ground_mask on the same frame (~15 ms per call). install() replaces these module
attributes with memoized versions; the frozen files are not edited (MD5.txt).

  * load / load_cands: memoized per file (path, size, mtime); the latest file only, so at most one copy is alive, as
    before when the stages ran one after another. The callers only read the returned arrays.
  * ground_mask: memoized on the exact inputs (the bytes of Z and lab, and f), stored as bits; every call returns a
    new array, so a caller that modifies its mask cannot change another caller's.
Verified: avatar frames byte-identical with and without install() (scripts/eval/check_shared_cache.py).
"""
import hashlib
import os

import numpy as np

_files = {}
_ground = {}


def _file_key(path):
    st = os.stat(path)
    return os.path.realpath(path), st.st_size, st.st_mtime_ns


def _memo_file(name, fn, path_of):
    def wrapped(cache):
        key = (name, _file_key(path_of(cache)))
        hit = _files.get(name)
        if hit is None or hit[0] != key:
            _files[name] = None                     # drop the old copy before loading the new one
            _files[name] = (key, fn(cache))
        return _files[name][1]
    wrapped.__wrapped__ = fn
    return wrapped


def _digest(a):
    a = np.ascontiguousarray(a)
    return hashlib.blake2b(a.data, digest_size=16).hexdigest(), a.dtype.str, a.shape


def _memo_ground(fn, max_items=512):
    def wrapped(Z, f, lab, *args, **kw):
        key = (_digest(Z), float(f), _digest(lab), args, tuple(sorted(kw.items())))
        r = _ground.get(key)
        if r is None:
            if len(_ground) >= max_items:
                _ground.pop(next(iter(_ground)))
            g = fn(Z, f, lab, *args, **kw)
            _ground[key] = (np.packbits(g), g.shape)            # bits: a clip of masks stays small
            return g
        return np.unpackbits(r[0], count=int(np.prod(r[1]))).reshape(r[1]).astype(bool)
    wrapped.__wrapped__ = fn
    return wrapped


def install():
    """Patch prep.load, gpu_stage.load_cands, evidence.ground_mask (and the names evidence / evidence_fast imported
    from them). Idempotent."""
    import evidence
    import evidence_fast
    import gpu_stage
    import prep
    if getattr(prep.load, "__wrapped__", None) is None:
        prep.load = _memo_file("mesh", prep.load, lambda c: f"{c}/mesh.npz")
    if getattr(gpu_stage.load_cands, "__wrapped__", None) is None:
        gpu_stage.load_cands = _memo_file("cands", gpu_stage.load_cands, lambda c: f"{c}/cands.npz")
    if getattr(evidence.ground_mask, "__wrapped__", None) is None:
        evidence.ground_mask = _memo_ground(evidence.ground_mask)
    for m in (evidence, evidence_fast):
        if hasattr(m, "load_cands"):
            m.load_cands = gpu_stage.load_cands
        if hasattr(m, "load"):
            m.load = prep.load


def clear():
    _files.clear()
    _ground.clear()
