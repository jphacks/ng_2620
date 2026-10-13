"""Skip the random weight initialisation of models whose weights are then loaded from a checkpoint.

Constructing the pipeline's models spends ~14 s per run filling weights with random numbers (Linear/Conv
reset_parameters, truncated-normal init) that the checkpoint then overwrites. Inside `skip_random_init()` the random
fills are no-ops, so the tensors keep their uninitialised memory until the checkpoint is loaded.

Only use it around a construction whose checkpoint load is verified to overwrite every parameter and buffer the
model uses: scripts/eval/check_skip_init.py compares all parameters and buffers (persistent or not) of a model
built normally and built under this context, after loading. Inference itself must not draw random numbers either
(skipping changes how much of the RNG stream construction consumes); checked for every lite stage.
"""
import contextlib

import torch

_INIT_FNS = ("uniform_", "normal_", "trunc_normal_", "kaiming_uniform_", "kaiming_normal_", "xavier_uniform_",
             "xavier_normal_", "orthogonal_")
_TENSOR_FNS = ("uniform_", "normal_", "erfinv_")


@contextlib.contextmanager
def skip_random_init():
    saved = [(torch.nn.init, n, getattr(torch.nn.init, n)) for n in _INIT_FNS]
    saved += [(torch.Tensor, n, getattr(torch.Tensor, n)) for n in _TENSOR_FNS]
    try:
        for n in _INIT_FNS:
            setattr(torch.nn.init, n, lambda tensor, *a, **k: tensor)
        for n in _TENSOR_FNS:
            setattr(torch.Tensor, n, lambda self, *a, **k: self)
        yield
    finally:
        for obj, n, f in saved:
            setattr(obj, n, f)
