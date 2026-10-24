"""Keep a stack of identical transformer blocks in pinned CPU memory and stream each block's weights to the GPU just
before it runs, prefetching the next block on a side stream.

The kernels see the same tensors (same dtype, same values, a plain copy), so outputs are unchanged; only where the
weights live between calls changes. The GPU holds two fixed weight slots (double buffer) instead of every block, and
the slots are allocated once, so streaming does not churn or fragment the caching allocator.
"""
import torch


def stream_blocks(blocks, device):
    """`blocks`: an nn.ModuleList of same-shaped blocks, on the CPU, run in order. Pins their weights and installs
    the hooks. Returns the number of bytes kept off the GPU."""
    blocks = list(blocks)
    cpu = []
    for blk in blocks:
        ts = list(blk.parameters()) + list(blk.buffers())
        for t in ts:
            t.data = t.data.pin_memory()
        cpu.append(ts)
    shapes = [(t.shape, t.dtype) for t in cpu[0]]
    assert all([(t.shape, t.dtype) for t in c] == shapes for c in cpu), "blocks differ in shape"
    host = [[t.data for t in c] for c in cpu]
    slots = [[torch.empty(s, dtype=d, device=device) for s, d in shapes] for _ in range(2)]
    copy_stream = torch.cuda.Stream(device)
    loaded = [None, None]  # block index held by each slot
    copied = [torch.cuda.Event(), torch.cuda.Event()]
    released = [torch.cuda.Event(), torch.cuda.Event()]  # main stream finished with the slot
    for e in released:
        e.record(torch.cuda.current_stream(device))

    def load(i):
        k = i % 2
        if i >= len(blocks) or loaded[k] == i:
            return
        with torch.cuda.stream(copy_stream):
            copy_stream.wait_event(released[k])
            for g, h in zip(slots[k], host[i]):
                g.copy_(h, non_blocking=True)
            copied[k].record(copy_stream)
        loaded[k] = i

    def pre(i):
        def hook(mod, args):
            k = i % 2
            load(i)
            torch.cuda.current_stream(device).wait_event(copied[k])
            for t, g in zip(cpu[i], slots[k]):
                t.data = g
            load(i + 1)
        return hook

    def post(i):
        def hook(mod, args, out):
            k = i % 2
            for t, h in zip(cpu[i], host[i]):
                t.data = h
            released[k].record(torch.cuda.current_stream(device))
            loaded[k] = None
        return hook

    for i, blk in enumerate(blocks):
        blk.register_forward_pre_hook(pre(i))
        blk.register_forward_hook(post(i))
    return sum(h.numel() * h.element_size() for c in host for h in c)
