"""Run ViTDet's global-attention softmax one head at a time, in place.

detectron2's `Attention.forward` builds the full (B*heads, HW, HW) score matrix, adds the decomposed rel-pos terms
(a second copy) and runs softmax, which autocast computes in fp32 (a third copy, 2x the size). For the 1024 px input
the 4 global blocks have HW = 4096 and 16 heads: 0.5 + 0.5 + 1 GB, the peak of the whole detection.

Here both matmuls still run over all heads at once (same shapes, so cuBLAS picks the same kernels; splitting them by
head changes the kernel and the boxes). Only the row-wise part -- rel-pos add and softmax -- runs per head, and its
fp32 result is written back into the bf16 score matrix: the same round-to-nearest cast autocast applies before
`attn @ v`. Boxes, scores and classes are bit-identical (scripts/eval/check_bf16_weights.py, mode vitdet_attn);
peak allocated 3.91 -> 2.07 GB on its 12 images.

Windowed blocks (HW = 196) and anything not under bf16 autocast take the original path.
"""
import torch
from detectron2.modeling.backbone import vit
from detectron2.modeling.backbone.utils import add_decomposed_rel_pos

_orig = vit.Attention.forward


def _forward(self, x):
    B, H, W, _ = x.shape
    if H * W <= 1024 or not torch.is_autocast_enabled() or torch.get_autocast_gpu_dtype() != torch.bfloat16:
        return _orig(self, x)
    qkv = self.qkv(x).reshape(B, H * W, 3, self.num_heads, -1).permute(2, 0, 3, 1, 4)
    q, k, v = qkv.reshape(3, B * self.num_heads, H * W, -1).unbind(0)
    attn = (q * self.scale) @ k.transpose(-2, -1)
    for s in range(attn.shape[0]):
        a = attn[s:s + 1]
        if self.use_rel_pos:
            a = add_decomposed_rel_pos(a, q[s:s + 1], self.rel_pos_h, self.rel_pos_w, (H, W), (H, W))
        attn[s:s + 1] = a.softmax(dim=-1)
    x = (attn @ v).view(B, self.num_heads, H, W, -1).permute(0, 2, 3, 1, 4).reshape(B, H, W, -1)
    return self.proj(x)


def install():
    vit.Attention.forward = _forward
