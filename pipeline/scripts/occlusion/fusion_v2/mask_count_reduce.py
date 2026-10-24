"""Exact mask threshold counts, optionally fused with Triton.

Only comparisons and integer additions are fused. Model inference and the numpy
filter stay unchanged. The ordinary PyTorch implementation remains available on
CPU, unsupported layouts/dtypes, or installations without a working Triton.
"""
import warnings

import torch

try:
    import triton
    import triton.language as tl
except (ImportError, OSError):
    triton = None

_fused_failed = False
_BLOCK = 4096


if triton is not None:
    @triton.jit
    def _threshold_partials(masks, partials, pixels, chunks: tl.constexpr, BLOCK: tl.constexpr):
        row = tl.program_id(0)
        chunk = tl.program_id(1)
        x = chunk * BLOCK + tl.arange(0, BLOCK)
        valid = x < pixels
        value = tl.load(masks + row.to(tl.int64) * pixels + x, mask=valid, other=0.0)
        # Mask padding explicitly: its zero would otherwise count toward > -1.
        hi = tl.sum((valid & (value > 1.0)).to(tl.int32), axis=0)
        lo = tl.sum((valid & (value > -1.0)).to(tl.int32), axis=0)
        area = tl.sum((valid & (value > 0.0)).to(tl.int32), axis=0)
        tl.store(partials + (row * 3) * chunks + chunk, hi)
        tl.store(partials + (row * 3 + 1) * chunks + chunk, lo)
        tl.store(partials + (row * 3 + 2) * chunks + chunk, area)


def count_masks_native(m):
    """Original integer reduction, including its overflow guard and int64 output."""
    dtype = torch.int32 if m.shape[-2] * m.shape[-1] <= 2**31 - 1 else torch.int64
    return torch.stack([(m > value).sum((1, 2), dtype=dtype)
                        for value in (1.0, -1.0, 0.0)]).to(torch.int64).cpu().numpy()


def _count_fused(m):
    n, h, w = m.shape
    chunks = triton.cdiv(h * w, _BLOCK)
    with torch.cuda.device(m.device):
        partials = torch.empty((n, 3, chunks), device=m.device, dtype=torch.int32)
        _threshold_partials[(n, chunks)](m, partials, h * w, chunks, _BLOCK)
        # Each partial is <= BLOCK. Sum in int64 and preserve the original
        # contiguous (3, N) numpy interface for the downstream filter.
        return partials.sum(2, dtype=torch.int64).T.contiguous().cpu().numpy()


def count_masks(m):
    """Return counts for logits > 1, > -1 and > 0 as numpy int64 (3, N)."""
    global _fused_failed
    supported = (triton is not None and not _fused_failed and m.is_cuda
                 and m.dtype == torch.float32 and m.ndim == 3 and m.is_contiguous()
                 and m.numel() > 0 and m.shape[-2] * m.shape[-1] <= 2**31 - 1)
    if supported:
        try:
            return _count_fused(m)
        except torch.cuda.OutOfMemoryError:
            raise
        except Exception as exc:
            # A missing compiler/unsupported GPU must not make the optional
            # accelerator a new installation requirement. Report once per process.
            _fused_failed = True
            warnings.warn(f"Fused mask counts unavailable; using PyTorch: {exc}", RuntimeWarning)
    return count_masks_native(m)
