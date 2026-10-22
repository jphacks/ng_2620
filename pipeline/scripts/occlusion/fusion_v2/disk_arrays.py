"""Whole-video arrays on disk instead of in RAM, with unchanged values (for long clips).

A 5-minute 270x480 clip has 9000 frames: mesh depth 4.4 GB, MoGe depth 4.7 GB, packed candidate masks 4.6 GB,
frames and clean plates 3.5 GB each, all held at once by the frozen stages (estimated 40-45 GB with a 36 GB
commit limit). Every stage reads them one frame at a time, so a memory-mapped file serves them as well:

  * npz_member(path, key): the array np.load(path)[key] returns, memory-mapped. The .npy member is copied out of the
    npz once (streamed, never whole in RAM) to <path>.<key>.npy beside it, so the bytes, and therefore every value,
    are the npz's own. Small members (< MIN_BYTES) are just loaded. The copy is redone when the npz changes (its
    size and mtime are recorded in <path>.<key>.npy.src). Opened read-only: no caller writes into these arrays (a
    write would raise), and Windows charges a copy-on-write view's full size to the commit limit up front (a 4.35 GB
    Z: private bytes +4.35 GB with one frame read), which is what the 5-minute run ran into at 42 / 42.5 GB.
  * scratch(path, shape, dtype): a zero-filled array backed by a new .npy file, for arrays a stage builds frame by
    frame (np.zeros equivalent). release(path) deletes the file after the caller dropped the array.
"""
import gc
import os
import shutil
import zipfile

import numpy as np

MIN_BYTES = int(os.environ.get("DISK_ARRAYS_MIN_BYTES", 32 * 2**20))


def _member_header(zf, name):
    with zf.open(name) as f:
        version = np.lib.format.read_magic(f)
        read = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
        shape, fortran, dtype = read(f)
    return shape, fortran, dtype


def _src_key(path):
    st = os.stat(path)
    return f"{st.st_size} {st.st_mtime_ns}"


def npz_member(path, key):
    """np.load(path)[key], memory-mapped (copy-on-write) when the array is MIN_BYTES or larger."""
    name = key + ".npy"
    with zipfile.ZipFile(path) as zf:
        shape, fortran, dtype = _member_header(zf, name)
        if int(np.prod(shape)) * dtype.itemsize < MIN_BYTES or dtype.hasobject:
            with np.load(path) as z:
                return z[key]
        side, meta = f"{path}.{key}.npy", f"{path}.{key}.npy.src"
        key_now = _src_key(path)
        if not (os.path.exists(side) and os.path.exists(meta) and open(meta).read() == key_now):
            tmp = side + ".part"
            with zf.open(name) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst, 16 * 2**20)
            os.replace(tmp, side)
            with open(meta, "w") as f:
                f.write(key_now)
    return np.load(side, mmap_mode="r")


def scratch(path, shape, dtype):
    """A zero-filled array of shape/dtype backed by the new file `path` (.npy), like np.zeros."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return np.lib.format.open_memmap(path, mode="w+", dtype=dtype, shape=tuple(shape))


def release(path):
    """Delete a scratch file once the caller has dropped every reference to its array (Windows refuses while a view
    is still mapped: the file is then left for the next run to overwrite). -> True if deleted."""
    gc.collect()
    try:
        os.remove(path)
        return True
    except OSError:
        return False
