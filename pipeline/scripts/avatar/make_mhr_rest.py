"""Write data/mhr_rest_skeleton.npz: the MHR skeleton at zero model parameters (its rest pose).

  parents (127,) int, rest_pos (127,3) metres, rest_rot (127,3,3) global rotations, all in MHR model space
  (Y up, the person faces +Z, left = +X); floor_y: lowest rest-mesh vertex (the soles), metres.
  Joint rotations do not depend on the identity (shape / scale change translations only), so one rest skeleton
  serves every person.

usage (repo root): .venv/Scripts/python.exe scripts/avatar/make_mhr_rest.py [<mhr_model.pt>]
"""
import os
import sys

import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(mhr_path):
    m = torch.jit.load(mhr_path, map_location="cpu")
    with torch.no_grad():
        verts, state = m(torch.zeros(1, 45), torch.zeros(1, 204), torch.zeros(1, 72))
    state = state[0].double()
    pos = state[:, :3] / 100.0
    q = state[:, 3:7]  # xyzw (roma.unitquat_to_rotmat convention, as in mhr_head.mhr_forward)
    import roma
    rot = roma.unitquat_to_rotmat(q / q.norm(dim=1, keepdim=True))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "mhr_rest_skeleton.npz")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez(out, parents=m.character_torch.skeleton.joint_parents.numpy().astype(np.int32),
             rest_pos=pos.numpy().astype(np.float32), rest_rot=rot.numpy().astype(np.float32),
             floor_y=np.float32(verts[0, :, 1].min().item() / 100.0))
    print("wrote", out)


if __name__ == "__main__":
    from omegaconf import OmegaConf
    default = OmegaConf.load(os.path.join(ROOT, "configs", "body4d.yaml")).sam_3d_body["mhr_path"]
    main(sys.argv[1] if len(sys.argv) > 1 else default)
