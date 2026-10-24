"""Reuse of fusion v2 intermediates that the frozen stages recompute, with unchanged values.

evidence, fusion.render and the avatar's AvatarHider each load the same cache files (prep.load: mesh.npz with every
frame's mesh depth and image; gpu_stage.load_cands: every candidate mask unpacked), and fusion.render and
AvatarHider.raw both call evidence.ground_mask on the same frame (~15 ms per call). install() replaces these module
attributes with memoized versions; the frozen files are not edited (MD5.txt).

  * load / load_cands: memoized per file (path, size, mtime); the latest file only, so at most one copy is alive, as
    before when the stages ran one after another. The callers only read the returned arrays. load reads each
    mesh.npz array when it is first used (the fast stages never read the images "I"), memory-mapping the large ones
    (disk_arrays.npz_member: the npz's own bytes, so the same values).
  * ground_mask: memoized on the exact inputs (the bytes of Z and lab, and f), stored as bits; every call returns a
    new array, so a caller that modifies its mask cannot change another caller's.
  * load_cands returns the masks as PackedMasks: the packed rows of cands.npz, unpacked one mask per access. The
    frozen loader unpacks all of them at once (bool, 1 byte per pixel; a 60 s 270x480 clip: 60,905 masks = 7.35 GB,
    with a uint8 copy alongside while converting) where the packed rows take 0.92 GB. Every caller only indexes one
    mask (masks[ci]) except evidence_fast.link, which reads rows() and areas() of the same values. The packed rows
    are memory-mapped too.
  * moge.npz's Z (T,H,W float32), which evidence_fast, fusion.render and AvatarHider each load whole, is
    memory-mapped: load_moge_z, and for the frozen fusion.py a numpy stand-in whose load() does the same for moge.npz.
Verified: avatar frames byte-identical with and without install() (scripts/eval/check_shared_cache.py).
"""
import hashlib
import os

import numpy as np

import disk_arrays

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


class PackedMasks:
    """Candidate masks kept as np.packbits rows; masks[i] equals the frozen load_cands' masks[i] (a new bool array)."""

    def __init__(self, packed, H, W):
        self.packed, self.H, self.W = packed, int(H), int(W)
        self.shape = (len(packed), self.H, self.W)

    def __len__(self):
        return len(self.packed)

    def __getitem__(self, i):
        if not isinstance(i, (int, np.integer)):
            raise TypeError("PackedMasks supports one integer index; use rows() for several masks")
        return np.unpackbits(self.packed[i])[:self.H * self.W].reshape(self.H, self.W).astype(bool)

    def rows(self, idx):
        """masks[idx] flattened to (len(idx), H*W) bool."""
        return np.unpackbits(self.packed[idx], axis=1)[:, :self.H * self.W].astype(bool)

    def areas(self, chunk=4096):
        """Pixel count of every mask (int64, as bool .sum(1))."""
        return np.concatenate([self.rows(np.arange(s, min(s + chunk, len(self)))).sum(1)
                               for s in range(0, len(self), chunk)]) if len(self) else np.zeros(0, np.int64)


class _LazyNpz(dict):
    """prep.load's dict with each array read on first access (then kept), the large ones memory-mapped
    (disk_arrays.npz_member): the fast stages never read mesh.npz's "I" (every frame's image: 0.65 GB per 1800
    frames at 270x480), and D / L stay on disk."""

    def __init__(self, path):
        super().__init__()
        self._path = path
        with np.load(path) as z:
            self._names = tuple(z.files)

    def __missing__(self, k):
        if k not in self._names:
            raise KeyError(k)
        v = self[k] = disk_arrays.npz_member(self._path, k)
        return v

    def keys(self):
        return self._names

    def __contains__(self, k):
        return k in self._names

    def __iter__(self):
        return iter(self._names)

    def __len__(self):
        return len(self._names)


def load_mesh_lazy(cache):
    """prep.load (every array of mesh.npz), decompressing an array when it is first read."""
    return _LazyNpz(f"{cache}/mesh.npz")


def load_cands_packed(cache):
    """gpu_stage.load_cands with the masks as PackedMasks over the memory-mapped packed rows."""
    p = f"{cache}/cands.npz"
    with np.load(p) as c:
        H, W = c["shape"]
        rest = c["frame"], c["score"], c["inV"]
    return (PackedMasks(disk_arrays.npz_member(p, "masks"), H, W),) + rest


def load_moge_z(cache):
    """np.load(f"{cache}/moge.npz")["Z"], memory-mapped."""
    return disk_arrays.npz_member(f"{cache}/moge.npz", "Z")


_installed = False


def moge_z(cache):
    """moge.npz's Z for the editable stages: memory-mapped once install() ran, else loaded as before."""
    if _installed:
        return load_moge_z(cache)
    return np.load(f"{cache}/moge.npz")["Z"]


class _MogeZ:
    def __init__(self, path):
        self._path = path

    def __getitem__(self, k):
        if k != "Z":
            with np.load(self._path) as z:
                return z[k]
        return disk_arrays.npz_member(self._path, "Z")


class _NumpyMemmapMoge:
    """numpy for the frozen fusion.py, except np.load(<cache>/moge.npz)["Z"] memory-maps Z (load_moge_z)."""

    def __getattr__(self, name):
        return getattr(np, name)

    @staticmethod
    def load(file, *a, **k):
        if not a and not k and isinstance(file, str) and file.replace("\\", "/").endswith("/moge.npz"):
            return _MogeZ(file)
        return np.load(file, *a, **k)


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
    import fusion
    import gpu_stage
    import prep
    global _installed
    _installed = True
    fusion.np = _NumpyMemmapMoge()   # fusion.render's moge.npz Z load (its other numpy calls pass through)
    if getattr(prep.load, "__wrapped__", None) is None:
        prep.load = _memo_file("mesh", load_mesh_lazy, lambda c: f"{c}/mesh.npz")
    if getattr(gpu_stage.load_cands, "__wrapped__", None) is None:
        gpu_stage.load_cands = _memo_file("cands", load_cands_packed, lambda c: f"{c}/cands.npz")
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
