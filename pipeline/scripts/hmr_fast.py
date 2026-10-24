"""Sync-free replacements for the SAM-3D-Body MHR head path (models/sam_3d_body/.../heads/mhr_head.py).

The MHR head runs once per decoder layer (intermediate predictions feed the keypoint token update), i.e. ~6x per
decoder call. One call is ~1000 small ops and 27 host<->device syncs, so it takes ~12 ms at batch 1 and ~17 ms at
batch 64: launch/sync-bound, not compute-bound. The syncs come from
  - roma.rotmat_to_euler: nonzero() for the quaternion branch, boolean-mask writes for gimbal lock (18 per call)
  - boolean-mask / Python-list indexing (each list or CPU index is copied to the GPU, each mask runs nonzero)
  - CPU tensors created then moved with .to(device)
  - self.faces.cpu() on every call.
Every replacement here performs the same elementwise arithmetic on the same values in the same order; only how
elements are selected changes (torch.where / gather / long indices on the device instead of masks). The outputs are
therefore bitwise identical to the original functions (scripts/eval/check_hmr_fast.py checks this per function
and for whole head calls).

install(model) patches the model's two MHRHead instances (body and hand) and nothing else.
"""
import math
import types

import numpy as np
import roma
import torch
import torch.nn.functional as F

_DEV_CACHE = {}


def _dev(key, make, device):
    """A constant index tensor, built once per device."""
    k = (key, device)
    if k not in _DEV_CACHE:
        _DEV_CACHE[k] = make().to(device)
    return _DEV_CACHE[k]


# --------------------------------------------------------------------------------------------- roma replacements
def rotmat_to_unitquat(R):
    """roma.rotmat_to_unitquat without nonzero(): all four branches are computed, the argmax branch is gathered."""
    matrix, batch_shape = roma.internal.flatten_batch_dims(R, end_dim=-3)
    num_rotations, D1, D2 = matrix.shape
    assert (D1, D2) == (3, 3), "Input should be a Bx3x3 tensor."

    decision_matrix = torch.empty((num_rotations, 4), dtype=matrix.dtype, device=matrix.device)
    decision_matrix[:, :3] = matrix.diagonal(dim1=1, dim2=2)
    decision_matrix[:, -1] = decision_matrix[:, :3].sum(axis=1)
    choices = decision_matrix.argmax(axis=1)

    cands = []
    for i in range(3):  # choices 0..2
        j = (i + 1) % 3
        k = (j + 1) % 3
        q = [None] * 4
        q[i] = 1 - decision_matrix[:, -1] + 2 * matrix[:, i, i]
        q[j] = matrix[:, j, i] + matrix[:, i, j]
        q[k] = matrix[:, k, i] + matrix[:, i, k]
        q[3] = matrix[:, k, j] - matrix[:, j, k]
        cands.append(torch.stack(q, dim=-1))
    cands.append(torch.stack([matrix[:, 2, 1] - matrix[:, 1, 2],
                              matrix[:, 0, 2] - matrix[:, 2, 0],
                              matrix[:, 1, 0] - matrix[:, 0, 1],
                              1 + decision_matrix[:, -1]], dim=-1))
    quat = torch.stack(cands, dim=1).gather(1, choices[:, None, None].expand(-1, 1, 4))[:, 0]

    quat = quat / torch.norm(quat, dim=1)[:, None]
    return roma.internal.unflatten_batch_dims(quat, batch_shape)


def _axis(a):
    return {"x": 0, "y": 1, "z": 2}[a]


def unitquat_to_euler(convention, quat, epsilon=1e-7):
    """roma.unitquat_to_euler (radians, stacked) with torch.where instead of boolean-mask writes."""
    assert len(convention) == 3
    pi = math.pi
    lamb = math.pi / 2

    extrinsic = convention.islower()
    if not extrinsic:
        convention = convention.lower()[::-1]

    quat, batch_shape = roma.internal.flatten_batch_dims(quat, end_dim=-2)
    i, j, k = (_axis(c) for c in convention)
    assert i != j and j != k, "Consecutive axes should not be identical."
    symmetric = i == k
    if symmetric:
        k = 3 - i - j
    sign = (i - j) * (j - k) * (k - i) // 2

    if symmetric:
        a = quat[:, 3]
        b = quat[:, i]
        c = quat[:, j]
        d = quat[:, k] * sign
    else:
        a = quat[:, 3] - quat[:, j]
        b = quat[:, i] + quat[:, k] * sign
        c = quat[:, j] + quat[:, 3]
        d = quat[:, k] * sign - quat[:, i]

    angle_first, angle_third = (0, 2) if extrinsic else (2, 0)

    angles = [None] * 3
    angles[1] = 2 * torch.atan2(roma.internal.hypot(c, d), roma.internal.hypot(a, b))

    case1 = torch.abs(angles[1]) <= epsilon
    case2 = torch.abs(angles[1] - pi) <= epsilon
    case1or2 = torch.logical_or(case1, case2)
    half_sum = torch.atan2(b, a)
    half_diff = torch.atan2(d, c)

    angles[angle_first] = half_sum - half_diff
    angles[angle_third] = half_sum + half_diff

    angles[2] = torch.where(case1or2, torch.zeros_like(angles[2]), angles[2])
    angles[0] = torch.where(case1, 2 * half_sum, angles[0])
    angles[0] = torch.where(case2, 2 * (-1 if extrinsic else 1) * half_diff, angles[0])

    if not symmetric:
        angles[angle_third] = angles[angle_third] * sign
        angles[1] = angles[1] - lamb

    for idx in range(3):
        foo = angles[idx]
        foo = torch.where(foo < -pi, foo + 2 * pi, foo)
        foo = torch.where(foo > pi, foo - 2 * pi, foo)
        angles[idx] = roma.internal.unflatten_batch_dims(foo, batch_shape)
    return torch.stack(angles, dim=-1)


def rotmat_to_euler(convention, rotmat):
    return unitquat_to_euler(convention, rotmat_to_unitquat(rotmat))


# ------------------------------------------------------------------------------------ mhr_utils replacements
_HAND_DOFS = [3, 1, 1, 3, 1, 1, 3, 1, 1, 3, 1, 1, 2, 3, 1, 1]


def _mask_idx(mask):
    return lambda: torch.as_tensor(mask).nonzero().flatten()


def compact_cont_to_model_params_hand(hand_cont, batchXYZfrom6D):
    assert hand_cont.shape[-1] == 54
    dev = hand_cont.device
    cont3 = _dev("hand_cont3", _mask_idx(np.concatenate([[k == 3] * (2 * k) for k in _HAND_DOFS])), dev)
    cont1 = _dev("hand_cont1", _mask_idx(np.concatenate([[k in (1, 2)] * (2 * k) for k in _HAND_DOFS])), dev)
    par3 = _dev("hand_par3", _mask_idx(np.concatenate([[k == 3] * k for k in _HAND_DOFS])), dev)
    par1 = _dev("hand_par1", _mask_idx(np.concatenate([[k in (1, 2)] * k for k in _HAND_DOFS])), dev)

    hand_cont_threedofs = hand_cont[..., cont3].unflatten(-1, (-1, 6))
    hand_model_params_threedofs = batchXYZfrom6D(hand_cont_threedofs).flatten(-2, -1)
    hand_cont_onedofs = hand_cont[..., cont1].unflatten(-1, (-1, 2))
    hand_model_params_onedofs = torch.atan2(hand_cont_onedofs[..., -2], hand_cont_onedofs[..., -1])

    hand_model_params = torch.zeros(*hand_cont.shape[:-1], 27, dtype=hand_cont.dtype, device=dev)
    hand_model_params[..., par3] = hand_model_params_threedofs
    hand_model_params[..., par1] = hand_model_params_onedofs
    return hand_model_params


def compact_cont_to_model_params_body(body_pose_cont, mu):
    """mu: the original mhr_utils module (index constants and batchXYZfrom6D come from there)."""
    import inspect
    dev = body_pose_cont.device
    if ("body_idx", dev) not in _DEV_CACHE:
        # The index lists are literals inside the original function; take them from its source once, so they
        # cannot drift from the original.
        src = inspect.getsource(mu.compact_cont_to_model_params_body)
        ns = {"torch": torch}
        lines = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("all_param_")]
        for ln in lines:
            exec(ln, ns)
        _DEV_CACHE[("body_idx", dev)] = (ns["all_param_3dof_rot_idxs"].flatten().to(dev),
                                         ns["all_param_1dof_rot_idxs"].to(dev),
                                         ns["all_param_1dof_trans_idxs"].to(dev))
    idx3, idx1, idxt = _DEV_CACHE[("body_idx", dev)]
    n3 = idx3.numel()
    n1 = idx1.numel()
    assert body_pose_cont.shape[-1] == 2 * n3 + 2 * n1 + idxt.numel()
    body_cont_3dofs = body_pose_cont[..., : 2 * n3]
    body_cont_1dofs = body_pose_cont[..., 2 * n3: 2 * n3 + 2 * n1]
    body_cont_trans = body_pose_cont[..., 2 * n3 + 2 * n1:]
    body_cont_3dofs = body_cont_3dofs.unflatten(-1, (-1, 6))
    body_params_3dofs = mu.batchXYZfrom6D(body_cont_3dofs).flatten(-2, -1)
    body_cont_1dofs = body_cont_1dofs.unflatten(-1, (-1, 2))
    body_params_1dofs = torch.atan2(body_cont_1dofs[..., -2], body_cont_1dofs[..., -1])
    body_pose_params = torch.zeros(*body_pose_cont.shape[:-1], 133, dtype=body_pose_cont.dtype, device=dev)
    body_pose_params[..., idx3] = body_params_3dofs
    body_pose_params[..., idx1] = body_params_1dofs
    body_pose_params[..., idxt] = body_cont_trans
    return body_pose_params


# ------------------------------------------------------------------------------------------ MHRHead methods
def _replace_hands_in_pose(self, full_pose_params, hand_pose_params):
    assert full_pose_params.shape[1] == 136
    left_hand_params, right_hand_params = torch.split(
        hand_pose_params, [self.num_hand_pose_comps, self.num_hand_pose_comps], dim=1)
    left = compact_cont_to_model_params_hand(
        self.hand_pose_mean + torch.einsum("da,ab->db", left_hand_params, self.hand_pose_comps),
        self._hf_mu.batchXYZfrom6D)
    right = compact_cont_to_model_params_hand(
        self.hand_pose_mean + torch.einsum("da,ab->db", right_hand_params, self.hand_pose_comps),
        self._hf_mu.batchXYZfrom6D)
    full_pose_params[:, self.hand_joint_idxs_left] = left
    full_pose_params[:, self.hand_joint_idxs_right] = right
    return full_pose_params


def _mhr_forward(self, global_trans, global_rot, body_pose_params, hand_pose_params, scale_params, shape_params,
                 expr_params=None, return_keypoints=False, do_pcblend=True, return_joint_coords=False,
                 return_model_params=False, return_joint_rotations=False, scale_offsets=None, vertex_offsets=None):
    """MHRHead.mhr_forward; only rotmat_to_euler differs (sync-free, identical values)."""
    if self.enable_hand_model:
        global_rot_ori = global_rot.clone()
        global_trans_ori = global_trans.clone()
        global_rot = rotmat_to_euler("xyz", roma.euler_to_rotmat("xyz", global_rot_ori) @ self.local_to_world_wrist)
        global_trans = (-(roma.euler_to_rotmat("xyz", global_rot) @ (self.right_wrist_coords - self.root_coords)
                          + self.root_coords) + global_trans_ori)

    body_pose_params = body_pose_params[..., :130]
    if len(scale_params.shape) == 1:
        scale_params = scale_params[None]
    if len(shape_params.shape) == 1:
        shape_params = shape_params[None]
    scales = self.scale_mean[None, :] + scale_params @ self.scale_comps
    if scale_offsets is not None:
        scales = scales + scale_offsets

    full_pose_params = torch.cat([global_trans * 10, global_rot, body_pose_params], dim=1)
    if hand_pose_params is not None:
        full_pose_params = self.replace_hands_in_pose(full_pose_params, hand_pose_params)
    model_params = torch.cat([full_pose_params, scales], dim=1)

    if self.enable_hand_model:
        model_params.index_fill_(1, self.nonhand_param_idxs, 0)

    curr_skinned_verts, curr_skel_state = self.mhr(shape_params, model_params, expr_params)
    curr_joint_coords, curr_joint_quats, _ = torch.split(curr_skel_state, [3, 4, 1], dim=2)
    curr_skinned_verts = curr_skinned_verts / 100
    curr_joint_coords = curr_joint_coords / 100
    curr_joint_rots = roma.unitquat_to_rotmat(curr_joint_quats)

    to_return = [curr_skinned_verts]
    if return_keypoints:
        model_vert_joints = torch.cat([curr_skinned_verts, curr_joint_coords], dim=1)
        model_keypoints_pred = ((self.keypoint_mapping @ model_vert_joints.permute(1, 0, 2).flatten(1, 2))
                                .reshape(-1, model_vert_joints.shape[0], 3).permute(1, 0, 2))
        if self.enable_hand_model:
            model_keypoints_pred[:, :21] = 0
            model_keypoints_pred[:, 42:] = 0
        to_return = to_return + [model_keypoints_pred]
    if return_joint_coords:
        to_return = to_return + [curr_joint_coords]
    if return_model_params:
        to_return = to_return + [model_params]
    if return_joint_rotations:
        to_return = to_return + [curr_joint_rots]
    if isinstance(to_return, list) and len(to_return) == 1:
        return to_return[0]
    return tuple(to_return)


def _head_forward(self, x, init_estimate=None, do_pcblend=True, slim_keypoints=False):
    """MHRHead.forward with the syncs removed (identical values; see the module docstring)."""
    batch_size = x.shape[0]
    pred = self.proj(x)
    if init_estimate is not None:
        pred = pred + init_estimate

    count = 6
    global_rot_6d = pred[:, :count]
    global_rot_rotmat = self._hf_rot6d_to_rotmat(global_rot_6d)
    global_rot_euler = rotmat_to_euler("ZYX", global_rot_rotmat)
    global_trans = torch.zeros_like(global_rot_euler)

    pred_pose_cont = pred[:, count: count + self.body_cont_dim]
    count += self.body_cont_dim
    pred_pose_euler = compact_cont_to_model_params_body(pred_pose_cont, self._hf_mu)
    # index_fill_ instead of `[:, mask] = 0`: a Python-scalar index_put uploads the scalar (a sync)
    pred_pose_euler.index_fill_(1, _dev("hand_mask", _mask_idx(self._hf_mu.mhr_param_hand_mask), pred.device), 0)
    pred_pose_euler[:, -3:] = 0

    pred_shape = pred[:, count: count + self.num_shape_comps]
    count += self.num_shape_comps
    pred_scale = pred[:, count: count + self.num_scale_comps]
    count += self.num_scale_comps
    pred_hand = pred[:, count: count + self.num_hand_comps * 2]
    count += self.num_hand_comps * 2
    pred_face = pred[:, count: count + self.num_face_comps] * 0
    count += self.num_face_comps

    output = self.mhr_forward(
        global_trans=global_trans, global_rot=global_rot_euler, body_pose_params=pred_pose_euler,
        hand_pose_params=pred_hand, scale_params=pred_scale, shape_params=pred_shape, expr_params=pred_face,
        do_pcblend=do_pcblend, return_keypoints=True, return_joint_coords=True, return_model_params=True,
        return_joint_rotations=True)

    verts, j3d, jcoords, mhr_model_params, joint_global_rots = output
    j3d = j3d[:, :70]
    if verts is not None:
        verts[..., 1:3] *= -1  # was [..., [1, 2]]: same elements, no index upload
    j3d[..., 1:3] *= -1
    if jcoords is not None:
        jcoords[..., 1:3] *= -1

    return {
        "pred_pose_raw": torch.cat([global_rot_6d, pred_pose_cont], dim=1),
        "pred_pose_rotmat": None,
        "global_rot": global_rot_euler,
        "body_pose": pred_pose_euler,
        "shape": pred_shape,
        "scale": pred_scale,
        "hand": pred_hand,
        "face": pred_face,
        "pred_keypoints_3d": j3d.reshape(batch_size, -1, 3),
        "pred_vertices": verts.reshape(batch_size, -1, 3) if verts is not None else None,
        "pred_joint_coords": jcoords.reshape(batch_size, -1, 3) if jcoords is not None else None,
        "faces": self._hf_faces_np,
        "joint_global_rots": joint_global_rots,
        "mhr_model_params": mhr_model_params,
    }


def install(model):
    """Patch model.head_pose and model.head_pose_hand (instance attributes only; the classes stay untouched)."""
    import sys
    for head in (model.head_pose, model.head_pose_hand):
        mod = sys.modules[type(head).__module__]
        head._hf_mu = sys.modules[mod.compact_cont_to_model_params_body.__module__]
        head._hf_rot6d_to_rotmat = mod.rot6d_to_rotmat
        head._hf_faces_np = head.faces.cpu().numpy()  # was copied to the host on every call; read-only downstream
        head.forward = types.MethodType(_head_forward, head)
        head.mhr_forward = types.MethodType(_mhr_forward, head)
        head.replace_hands_in_pose = types.MethodType(_replace_hands_in_pose, head)
