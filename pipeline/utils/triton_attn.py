"""FlashAttention-style forward kernel in Triton for the SAM3 tracker's memory attention.

The tracker's memory attention (sam3/sam/transformer.py RoPEAttention) runs F.scaled_dot_product_attention with one head
of dim 256: 5184 queries of the current frame against ~36k keys of 7 memory frames, 4 layers, per object. PyTorch's
Windows wheels ship without the flash kernel, and cuDNN has none for this shape, so SDPA falls back to the
memory-efficient kernel (21.7 ms per call on an RTX 5060 Ti, ~18 TFLOPS). This kernel is the standard online-softmax
forward (Dao et al.; the Triton tutorial's structure): q tile resident, k/v streamed, fp32 accumulators, bf16 inputs.
Results are not bit-identical to SDPA (different summation order); scripts/eval/check_track_fast.py --attn measures it.

install(): routes the tracker's SDPA calls with no mask / dropout, bf16 / fp16 inputs and head dim 64..256 here, the
rest to PyTorch.
"""
import torch
import triton
import triton.language as tl

# One fixed tile config: autotuning keyed on the key length re-tuned on almost every frame (the memory grows by a frame
# and by object-pointer tokens), which cost more than it saved. Autotuned over (64,32,4,1) (64,64,4,1) (64,32,8,2)
# (128,32,8,1) (64,64,8,1) (32,64,4,2) (128,64,8,1) on an RTX 5060 Ti, this one won at every tracker shape.
BM, BN, WARPS, STAGES = 32, 64, 4, 2


@triton.jit
def _fwd(Q, K, V, O, sm_scale,
         sqb, sqn, skb, skn, svb, svn, sob, son,
         NQ, NK, D: tl.constexpr, BM: tl.constexpr, BN: tl.constexpr):
    pid_m = tl.program_id(0)
    b = tl.program_id(1)
    offs_m = pid_m * BM + tl.arange(0, BM)
    offs_d = tl.arange(0, D)
    q = tl.load(Q + b * sqb + offs_m[:, None] * sqn + offs_d[None, :], mask=offs_m[:, None] < NQ, other=0.0)
    m_i = tl.full([BM], float("-inf"), tl.float32)
    l_i = tl.zeros([BM], tl.float32)
    acc = tl.zeros([BM, D], tl.float32)
    qk_scale = sm_scale * 1.4426950408889634   # exp(x) = exp2(x * log2(e))
    for start in range(0, NK, BN):
        offs_n = start + tl.arange(0, BN)
        kmask = offs_n < NK
        k = tl.load(K + b * skb + offs_n[None, :] * skn + offs_d[:, None], mask=kmask[None, :], other=0.0)
        s = tl.dot(q, k) * qk_scale
        s = tl.where(kmask[None, :], s, float("-inf"))
        m_new = tl.maximum(m_i, tl.max(s, 1))
        p = tl.math.exp2(s - m_new[:, None])
        alpha = tl.math.exp2(m_i - m_new)
        l_i = l_i * alpha + tl.sum(p, 1)
        v = tl.load(V + b * svb + offs_n[:, None] * svn + offs_d[None, :], mask=kmask[:, None], other=0.0)
        acc = acc * alpha[:, None] + tl.dot(p.to(v.dtype), v)
        m_i = m_new
    acc = acc / l_i[:, None]
    tl.store(O + b * sob + offs_m[:, None] * son + offs_d[None, :], acc.to(O.dtype.element_ty),
             mask=offs_m[:, None] < NQ)


def attention(q, k, v, scale=None):
    """softmax(q k^T * scale) v for q (B, H, NQ, D), k / v (B, H, NK, D) with the last dim contiguous."""
    B, H, NQ, D = q.shape
    NK = k.shape[2]
    q2, k2, v2 = (x.reshape(B * H, x.shape[2], D) for x in (q, k, v))
    q2, k2, v2 = (x if x.stride(-1) == 1 else x.contiguous() for x in (q2, k2, v2))
    o = torch.empty((B * H, NQ, D), device=q.device, dtype=q.dtype)
    scale = D ** -0.5 if scale is None else scale

    _fwd[(triton.cdiv(NQ, BM), B * H)](q2, k2, v2, o, scale, q2.stride(0), q2.stride(1), k2.stride(0), k2.stride(1),
                                       v2.stride(0), v2.stride(1), o.stride(0), o.stride(1), NQ, NK, D=D, BM=BM,
                                       BN=BN, num_warps=WARPS, num_stages=STAGES)
    return o.view(B, H, NQ, D)


def _usable(q, k, v, attn_mask, dropout_p, is_causal, scale, enable_gqa=False):
    return (attn_mask is None and not dropout_p and not is_causal and q.is_cuda and q.dtype in (torch.bfloat16,
            torch.float16) and k.dtype == q.dtype and v.dtype == q.dtype and q.dim() == 4
            and q.shape[-1] in (64, 128, 256) and v.shape[-1] == q.shape[-1] and k.shape[2] >= 1024)


def install():
    """Route the SDPA calls of sam3/sam/transformer.py (the tracker's memory attention and its SAM decoder) that
    _usable accepts to attention(); everything else stays with PyTorch."""
    import types
    import torch.nn.functional as F
    from sam3.sam import transformer as tmod
    sdpa = F.scaled_dot_product_attention

    def routed(q, k, v, attn_mask=None, dropout_p=0.0, is_causal=False, scale=None, **kw):
        if _usable(q, k, v, attn_mask, dropout_p, is_causal, scale):
            return attention(q, k, v, scale)
        return sdpa(q, k, v, attn_mask=attn_mask, dropout_p=dropout_p, is_causal=is_causal, scale=scale, **kw)
    ns = types.SimpleNamespace(**{n: getattr(F, n) for n in dir(F) if not n.startswith("__")})
    ns.scaled_dot_product_attention = routed
    tmod.F = ns
