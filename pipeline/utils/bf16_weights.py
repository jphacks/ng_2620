"""Store the Linear / Conv weights of a model that runs under bf16 autocast in bf16 instead of fp32.

Under `torch.autocast("cuda", dtype=torch.bfloat16)` every Linear / Conv call casts its fp32 weight and bias to bf16
(`.to(torch.bfloat16)`, round-to-nearest-even) and computes in bf16. Parameters that require grad are leaves, so
autocast also caches those bf16 copies until the outermost autocast region exits; SAM3's builders open one for the
whole process. The model therefore holds each weight twice (fp32 + bf16, 1.5x the fp32 size).

Casting the same tensors ahead of time gives the kernels the same bf16 values, so the outputs are unchanged, and only
the bf16 copy is kept. Norm layers, embeddings and loose parameters (positional / RoPE tables, rel-pos, tokens) stay
fp32: autocast runs those ops in fp32 and does not cast them.

Only valid for modules whose every call runs under bf16 autocast (a call outside it fails on the dtype mismatch, or
-- where code casts its input to `weight.dtype` -- computes in bf16). Each model it is applied to is checked for
bit-identical outputs (scripts/eval/check_bf16_weights.py).
"""
import torch

_TYPES = (torch.nn.Linear, torch.nn.Conv1d, torch.nn.Conv2d, torch.nn.Conv3d, torch.nn.ConvTranspose1d,
          torch.nn.ConvTranspose2d, torch.nn.ConvTranspose3d)


def bf16_weights(model, skip=()):
    """Cast weight / bias of every Linear / Conv module of `model` (except under the module names in `skip`) to
    bf16 in place. Returns the number of bytes freed."""
    freed = 0
    for name, m in model.named_modules():
        if not isinstance(m, _TYPES) or any(name == s or name.startswith(s + ".") for s in skip):
            continue
        for p in (m.weight, m.bias):
            if p is not None and p.dtype == torch.float32:
                freed += p.numel() * 2
                p.data = p.data.to(torch.bfloat16)
    return freed
