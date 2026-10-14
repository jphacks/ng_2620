"""Check that scripts/hmr_fast.py is bitwise identical to the original MHR head path.

1. roma rotmat_to_euler replacement on random rotations plus every degenerate branch (all 4 quaternion branches,
   gimbal lock at 0 and pi, angle wrap-around), for each convention the head uses.
2. Whole head calls (body and hand head, several batch sizes). The TorchScript MHR is not bitwise repeatable by
   itself (atomics), so it is replaced by a replay: the original run records its outputs, the fast run receives the
   same outputs, and its inputs are compared too. Everything around the MHR must then match bit for bit.

usage (repo root): .venv/Scripts/python.exe scripts/eval/check_hmr_fast.py
"""
import math
import os
import sys

import roma
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import offline_app as oa  # noqa: E402
import hmr_fast  # noqa: E402
from omegaconf import OmegaConf  # noqa: E402
from utils.skip_init import skip_random_init  # noqa: E402

ok = True


def report(name, a, b):
    global ok
    same = torch.equal(a, b) if torch.is_tensor(a) else a == b
    ok &= bool(same)
    if not same:
        print(f"  MISMATCH {name}: max diff {(a - b).abs().max().item():.3g}")
    return same


# 1. rotations
g = torch.Generator().manual_seed(0)
R = [roma.random_rotmat(4096, generator=g)]
special = [torch.eye(3), torch.diag(torch.tensor([1., -1., -1.])), torch.diag(torch.tensor([-1., 1., -1.])),
           torch.diag(torch.tensor([-1., -1., 1.]))]
for conv in ("xyz", "ZYX", "XZY"):  # gimbal lock for this convention: middle angle 0 / pi
    for mid in (0.0, math.pi / 2, -math.pi / 2, math.pi):
        e = torch.zeros(64, 3).uniform_(-math.pi, math.pi, generator=g)
        e[:, 1] = mid
        special.append(roma.euler_to_rotmat(conv, e))
R.append(torch.cat([s.reshape(-1, 3, 3) for s in special]))
R = torch.cat(R).cuda()
for conv in ("xyz", "ZYX", "XZY", "zyx"):
    for dt in (torch.float32,):
        report(f"rotmat_to_euler {conv}", roma.rotmat_to_euler(conv, R.to(dt)), hmr_fast.rotmat_to_euler(conv, R.to(dt)))
print("rotmat_to_euler:", "identical" if ok else "DIFFERENT")

# 2. whole head calls
ROOT = os.path.join(HERE, "..", "..")
cfg = OmegaConf.load(os.path.join(ROOT, "configs", "body4d.yaml"))
with skip_random_init():
    model, _ = oa.load_sam_3d_body(cfg.sam_3d_body["ckpt_path"], device=torch.device("cuda"),
                                   mhr_path=cfg.sam_3d_body["mhr_path"])


class Replay(torch.nn.Module):
    def __init__(self, real):
        super().__init__()
        self.real, self.mode, self.log = real, "record", []

    def forward(self, *args):
        if self.mode == "record":
            out = self.real(*args)
            self.log.append(([a.clone() if torch.is_tensor(a) else a for a in args], [o.clone() for o in out]))
            return out
        rec_args, rec_out = self.log.pop(0)
        for i, (a, b) in enumerate(zip(rec_args, args)):
            if torch.is_tensor(a):
                report(f"mhr input {i}", a, b)
        return tuple(o.clone() for o in rec_out)


heads = {"body": model.head_pose, "hand": model.head_pose_hand}
for h in heads.values():
    h.mhr = Replay(h.mhr)
cases = []
for name, h in heads.items():
    for B in (1, 8, 16):
        x = torch.randn(B, 1024, device="cuda", generator=torch.Generator("cuda").manual_seed(B)) * 0.5
        init = torch.randn(B, h.npose,
                           device="cuda", generator=torch.Generator("cuda").manual_seed(100 + B)) * 0.5
        cases.append((name, B, x, init))
with torch.no_grad():
    ref = [heads[n](x, init) for n, B, x, init in cases]
    extra = [heads["body"].mhr_forward(r["global_rot"], r["global_rot"], r["body_pose"], r["hand"], r["scale"],
                                       r["shape"], r["face"], return_joint_rotations=True) for r in ref[:3]]
    for h in heads.values():
        h.mhr.mode = "replay"
    hmr_fast.install(model)
    out = [heads[n](x, init) for n, B, x, init in cases]
    extra2 = [heads["body"].mhr_forward(r["global_rot"], r["global_rot"], r["body_pose"], r["hand"], r["scale"],
                                        r["shape"], r["face"], return_joint_rotations=True) for r in ref[:3]]
for (n, B, _, _), a, b in zip(cases, ref, out):
    for k in a:
        if torch.is_tensor(a[k]):
            report(f"{n} B={B} {k}", a[k], b[k])
        elif k == "faces":
            ok &= bool((a[k] == b[k]).all())
for a, b in zip(extra, extra2):
    for x, y in zip(a, b):
        report("direct mhr_forward", x, y)
print("head outputs:", "bitwise identical" if ok else "DIFFERENT")
sys.exit(0 if ok else 1)
