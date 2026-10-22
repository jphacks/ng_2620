"""Retarget the SAM-3D-Body (MHR) skeleton onto a VRM Humanoid, before any mesh is involved.

Input per frame and person (skeleton_4d_individual/<id>/<stem>.npz, written by save_skeleton_results):
  joint_rots (127,3,3)  global joint rotations G_j, MHR model space (Y up, faces +Z, left = +X)
  joint_coords (127,3) + cam_t (3,)   joint positions in camera coords (x right, y down, z forward, metres)
  keypoints_3d (70,3) + cam_t         MHR70 keypoints (toe tips and heels give the soles)

Rotation transfer, the same idea as virtual-vlog's SMPL -> VRM (core/retarget.py: Pose_b = Delta_j R_b), with one
addition because MHR and VRM do not share a rest pose (MHR rests in an A pose with the arms ~48 deg down, VRM in a
T pose):
  Delta_j = G_j G0_j^T          rotation of MHR joint j away from its own rest (G0 = rest rotation, data/*.npz)
  C_b     = FromTo(d_V, d_M)    turns VRM bone b's rest direction onto MHR's rest direction of the same bone
  T_b     = Delta_j C_b R_b     target global rotation of VRM bone b (R_b = VRM rest rotation, MHR model frame)
so a VRM bone points where the MHR bone points in every frame. Unmapped VRM nodes (hair, skirt, eyes, accessories)
keep their rest rotation relative to their parent. Rotations, not positions, are copied: VRM bone lengths never
change, so differing proportions do not stretch the limbs.

MHR has full hands (5 fingers x 3 segments, thumb included), so VRM fingers are driven directly (virtual-vlog's
SMPL input had no fingers and used a fixed grip pose instead).

Frames: VRM glTF -> MHR model: RY (VRM 0.x faces -Z in glTF space; VRM 1.0 faces +Z -> identity).
        MHR model -> camera: S = diag(1,-1,-1) (the pipeline's "verts[..., [1, 2]] *= -1").
Placement: one uniform scale per person and a per-frame translation.
  scale = leg length ratio (hip -> knee -> ankle -> sole). An anime body has longer legs and a shorter trunk than a
          person; with this scale the hips and the soles both land on the person's (measured on Penn 0400: matching
          the whole skeleton length instead puts the soles 13-25 cm into the floor when the hips are matched, or the
          hands 17-30 cm off when the soles are matched, as virtual-vlog S06 does).
  translation = the hips joint (3D).
  arms  = 2-bone IK (virtual-vlog retarget.apply_arm_ik) puts the avatar's wrists on the person's wrists, which the
          shorter trunk otherwise leaves low; the hand keeps the person's hand orientation.
  legs  = 2-bone IK (virtual-vlog retarget.apply_leg_ik) puts the soles on the person's soles; the thigh / shin
          ratio differs, so with bent knees the feet otherwise miss (Penn 0400 lifter: ankles 10 cm off, soles 12.6 cm
          low). The target is the person's ankle raised along the foot's up axis by the difference in ankle height
          (the avatar's shoes are thicker than a bare MHR foot); the foot keeps the person's foot orientation.
"""

from __future__ import annotations

import os

import numpy as np

from .springbone import _from_to
from .vrm import VrmModel

S_CAM = np.diag([1.0, -1.0, -1.0]).astype(np.float32)
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "mhr_rest_skeleton.npz")

# VRM bone -> MHR joint whose global rotation drives it. Read off the MHR rest skeleton (parents + rest positions):
# 1 pelvis; 34-37 spine (37 carries the clavicles); 110 neck; 113 head; legs 2-8 (left) / 18-24 (right): hip, knee,
# ankle (4, 5 at the same point), 6 below the ankle, 7 mid-foot, 8 ball; arms 74-78 (left) / 38-42 (right):
# clavicle, shoulder, elbow, wrist (77, 78 at the same point); fingers per hand: little has a metacarpal joint
# (79 / 43), the thumb a CMC joint (96 / 60). 9-17, 25-33, 65-73, 101-109 are twist helpers (no children).
_SIDE = {
    "left": dict(shoulder=74, upper_arm=75, lower_arm=76, hand=78, upper_leg=2, lower_leg=3, ankle=4,
                 foot=6, toes=8, thumb=(97, 98, 99, 100), index=(92, 93, 94, 95), middle=(88, 89, 90, 91),
                 ring=(84, 85, 86, 87), little=(80, 81, 82, 83)),
    "right": dict(shoulder=38, upper_arm=39, lower_arm=40, hand=42, upper_leg=18, lower_leg=19, ankle=20,
                  foot=22, toes=24, thumb=(61, 62, 63, 64), index=(56, 57, 58, 59), middle=(52, 53, 54, 55),
                  ring=(48, 49, 50, 51), little=(44, 45, 46, 47)),
}
FINGERS = ("thumb", "index", "middle", "ring", "little")
SEGS = ("proximal", "intermediate", "distal")


def bone_map():
    """-> {vrm bone: (driving MHR joint, (MHR rest-direction start, end) or None)}"""
    m = {"hips": (1, None), "spine": (35, (35, 36)), "chest": (36, (36, 37)), "upper_chest": (37, (37, 110)),
         "neck": (110, (110, 113)), "head": (113, None)}
    for s, j in _SIDE.items():
        m[f"{s}_shoulder"] = (j["shoulder"], (j["shoulder"], j["upper_arm"]))
        m[f"{s}_upper_arm"] = (j["upper_arm"], (j["upper_arm"], j["lower_arm"]))
        m[f"{s}_lower_arm"] = (j["lower_arm"], (j["lower_arm"], j["hand"]))
        m[f"{s}_hand"] = (j["hand"], (j["hand"], j["middle"][0]))
        m[f"{s}_upper_leg"] = (j["upper_leg"], (j["upper_leg"], j["lower_leg"]))
        m[f"{s}_lower_leg"] = (j["lower_leg"], (j["lower_leg"], j["ankle"]))
        m[f"{s}_foot"] = (j["foot"], (j["ankle"], j["toes"]))
        m[f"{s}_toes"] = (j["toes"], None)
        for f in FINGERS:
            ch = j[f]
            for k, seg in enumerate(SEGS):
                m[f"{s}_{f}_{seg}"] = (ch[k], (ch[k], ch[k + 1]))
    return m


# VRM child bone used for the rest direction of each VRM bone (None: no direction correction)
def _vrm_child(b):
    side, _, rest = b.partition("_")
    nxt = {"spine": "chest", "chest": "upper_chest", "upper_chest": "neck", "neck": "head",
           "shoulder": "upper_arm", "upper_arm": "lower_arm", "lower_arm": "hand", "hand": "middle_proximal",
           "upper_leg": "lower_leg", "lower_leg": "foot", "foot": "toes",
           "proximal": "intermediate", "intermediate": "distal"}
    if b in nxt:
        return nxt[b]
    if side in ("left", "right"):
        if rest in nxt:
            return f"{side}_{nxt[rest]}"
        f, _, seg = rest.partition("_")
        if seg in nxt:
            return f"{side}_{f}_{nxt[seg]}"
    return None


class MhrToVrm:
    """Per-frame VRM node matrices from MHR joint rotations, plus placement in camera space."""

    def __init__(self, model: VrmModel):
        self.m = model
        z = np.load(DATA)
        self.G0 = z["rest_rot"].astype(np.float32)        # (127,3,3)
        self.P0 = z["rest_pos"].astype(np.float32)        # (127,3)
        floor_y = float(z["floor_y"])                      # rest soles
        self.parent = {c: i for i, n in enumerate(model.nodes) for c in n.get("children", [])}
        self.order = _topo(model.nodes)
        self._ik_update_nodes = {}
        self.rest = model.rest_globals()                    # (N,4,4) glTF
        hb = model.humanoid
        # which way does the VRM face? (toes in front of the feet) -> glTF->MHR rotation
        fwd = np.mean([self.rest[hb[f"{s}_toes"]][2, 3] - self.rest[hb[f"{s}_foot"]][2, 3]
                       for s in ("left", "right")])
        self.RY = (np.diag([-1.0, 1.0, -1.0]) if fwd < 0 else np.eye(3)).astype(np.float32)
        self.A = (S_CAM @ self.RY).astype(np.float32)         # glTF -> camera axes
        self.Rrest = np.einsum("ij,njk,lk->nil", self.RY, self.rest[:, :3, :3], self.RY)  # rest rot, MHR frame
        self.prest = self.rest[:, :3, 3] @ self.RY.T                                   # rest pos, MHR frame

        self.drive: dict[int, int] = {}     # VRM node -> MHR joint
        self.corr: dict[int, np.ndarray] = {}
        for b, (j, dirm) in bone_map().items():
            n = hb.get(b)
            if n is None:
                continue
            self.drive[n] = j
            C = np.eye(3, dtype=np.float32)
            cb = _vrm_child(b)
            if dirm is not None:
                if cb is not None and cb in hb:
                    dv = self.prest[hb[cb]] - self.prest[n]
                elif self.m.nodes[n].get("children"):
                    dv = self.prest[self.m.nodes[n]["children"][0]] - self.prest[n]
                else:
                    dv = self.prest[n] - self.prest[self.parent[n]]
                dm = self.P0[dirm[1]] - self.P0[dirm[0]]
                if np.linalg.norm(dv) > 1e-5 and np.linalg.norm(dm) > 1e-5:
                    C = _from_to((dv / np.linalg.norm(dv)).astype(np.float32),
                                 (dm / np.linalg.norm(dm)).astype(np.float32))
            self.corr[n] = C
        self.hips = hb["hips"]
        self.foot_nodes = [hb[k] for k in ("left_foot", "right_foot", "left_toes", "right_toes") if k in hb]
        self._build_sole()
        # leg: hip -> knee -> ankle + ankle height above the sole (rest pose of each model)
        P0, Lr = self.P0, _SIDE["left"]
        leg0 = (np.linalg.norm(P0[Lr["upper_leg"]] - P0[Lr["lower_leg"]])
                + np.linalg.norm(P0[Lr["lower_leg"]] - P0[Lr["ankle"]]))
        self.mhr_ankle_ratio = float((leg0 + P0[Lr["ankle"], 1] - floor_y) / leg0)
        vleg, vh = [], []
        for sd in ("left", "right"):
            u, k, f = hb[f"{sd}_upper_leg"], hb[f"{sd}_lower_leg"], hb[f"{sd}_foot"]
            vleg.append(np.linalg.norm(self.prest[u] - self.prest[k]) + np.linalg.norm(self.prest[k] - self.prest[f]))
            q = self.sole_local.get(f)
            sole_y = (self.rest[f] @ q)[1] if q is not None else 0.0
            vh.append(self.prest[f][1] - sole_y)
        self.vrm_ankle_h = float(np.mean(vh))
        ys = np.concatenate([self.m.skin_primitive(p, self.rest, compute_normals=False)[0][:, 1]
                             for p in self.m.prims])
        self.height_m = float(ys.max() - ys.min())       # rest mesh height at scale 1
        self.vrm_leg = float(np.mean(vleg) + self.vrm_ankle_h)

    # --- scale -----------------------------------------------------------
    def mhr_leg(self, coords):
        """Hip -> knee -> ankle length of one MHR frame plus the ankle height (rest proportion), pose independent."""
        L = []
        for s in ("left", "right"):
            j = _SIDE[s]
            L.append(np.linalg.norm(coords[j["upper_leg"]] - coords[j["lower_leg"]])
                     + np.linalg.norm(coords[j["lower_leg"]] - coords[j["ankle"]]))
        return float(np.mean(L)) * self.mhr_ankle_ratio

    def leg_scale(self, coords_frames):
        """avatar scale = person's leg (hip..sole) / avatar's leg (hip..sole), median over frames."""
        return float(np.median([self.mhr_leg(c) for c in coords_frames])) / self.vrm_leg

    # --- pose ------------------------------------------------------------
    def targets(self, G: np.ndarray) -> dict[int, np.ndarray]:
        """MHR global joint rotations (127,3,3) -> target global rotation of every VRM node (MHR model frame)."""
        T: dict[int, np.ndarray] = {}
        for i in self.order:
            j = self.drive.get(i)
            if j is not None:
                T[i] = (G[j] @ self.G0[j].T) @ self.corr[i] @ self.Rrest[i]
            else:
                p = self.parent.get(i)
                T[i] = self.Rrest[i] if p is None else T[p] @ self.Rrest[p].T @ self.Rrest[i]
        return T

    def globals_(self, G: np.ndarray) -> np.ndarray:
        """-> node global matrices (N,4,4) in glTF space (unscaled, untranslated), rotation transfer only."""
        return self.m.global_matrices(self.locals_(self.targets(np.asarray(G, np.float32))))

    def placement(self, g: np.ndarray, scale: float, coords_cam: np.ndarray) -> np.ndarray:
        """Camera-space translation t (v_cam = scale * A v_gltf + t): the avatar's hips on the person's hips."""
        return (coords_cam[1] - scale * (self.A @ g[self.hips][:3, 3])).astype(np.float32)

    def sole_y(self, g: np.ndarray, scale: float, t: np.ndarray) -> float:
        """Camera y of the avatar's lower sole (y points down)."""
        return max(float((scale * (self.A @ (g[n] @ q)[:3]) + t)[1]) for n, q in self.sole_local.items())

    def solve(self, G, coords_cam, scale, arm_ik=True, leg_ik=True, reach=0.995):
        """One frame: -> (node globals (N,4,4) glTF, translation t, {limb: end-effector error before IK [m]})."""
        T = self.targets(np.asarray(G, np.float32))
        loc = self.locals_(T)
        g = self.m.global_matrices(loc)
        t = self.placement(g, scale, coords_cam)
        err = {}
        limbs = ([("arm", s, "upper_arm", "lower_arm", "hand", "hand") for s in ("left", "right")] * bool(arm_ik)
                 + [("leg", s, "upper_leg", "lower_leg", "foot", "ankle") for s in ("left", "right")] * bool(leg_ik))
        # ankle height difference avatar - person [m]; the person's ankle height from the rest proportion
        lift = scale * self.vrm_ankle_h - self.mhr_leg(coords_cam) * (1 - 1 / self.mhr_ankle_ratio)
        for kind, side, u, l, e, joint in limbs:
            w = coords_cam[_SIDE[side][joint]]
            if not np.all(np.isfinite(w)):
                continue
            if kind == "leg":
                j = _SIDE[side]["foot"]
                up = G[j] @ self.G0[j].T @ np.array([0, 1, 0], np.float32)    # the foot's up axis, MHR frame
                w = w + S_CAM @ up * lift
            # target in the avatar's own frame: MHR model axes, unscaled, untranslated
            r = self.two_bone_ik(T, g, side, (u, l, e), (S_CAM @ (w - t)) / scale, reach)
            if r is not None:
                err[f"{side}_{kind}"] = r * scale
                changed = tuple(self.m.humanoid[f"{side}_{b}"] for b in (u, l))
                self._update_ik_globals(T, loc, g, changed)
        return g, t, err

    def _update_ik_globals(self, T, loc, g, changed):
        """IK changes two target rotations. Recompute their dependent nodes only.

        Local rotations depend on T[node] and T[parent], so only the changed
        nodes and their immediate children need new locals. Global matrices must
        still be recomputed for ALL descendants, preserving rounding and the
        original parent-to-child matrix multiplication order. Nothing is reused
        across frames; only the static dependency lists are cached.
        """
        if changed not in self._ik_update_nodes:
            roots = set(changed)
            affected = set(roots)
            local_nodes, global_nodes = [], []
            for i in self.order:
                p = self.parent.get(i)
                if i in roots or p in roots:
                    local_nodes.append(i)
                if i in affected or p in affected:
                    affected.add(i)
                    global_nodes.append(i)
            self._ik_update_nodes[changed] = local_nodes, global_nodes
        local_nodes, global_nodes = self._ik_update_nodes[changed]
        for i in local_nodes:
            p = self.parent.get(i)
            Lm = T[i] if p is None else T[p].T @ T[i]
            loc[i] = self.RY.T @ Lm @ self.RY
        for i in global_nodes:
            local = self.m.local_matrix(i, loc)
            p = self.parent.get(i)
            g[i] = local if p is None else g[p] @ local

    def two_bone_ik(self, T, g, side, bones, target, reach=0.995):
        """2-bone IK, virtual-vlog retarget.VrmRetargeter.apply_arm_ik / apply_leg_ik: rotate the middle joint in
        its current bending plane to the distance the target needs (law of cosines), then turn the root joint so the
        end points at the target. The end bone (hand / foot) keeps its global rotation: MHR measures the wrist,
        the fingers and the foot. T is modified in place (MHR model frame). Returns the end error before IK
        [unscaled m] or None."""
        hb = self.m.humanoid
        U, L, Hn = (hb.get(f"{side}_{b}") for b in bones)
        if U is None or L is None or Hn is None:
            return None
        pU, pL, pH = (self.RY @ g[n][:3, 3] for n in (U, L, Hn))
        Tg = np.asarray(target, np.float32)
        a, b = float(np.linalg.norm(pL - pU)), float(np.linalg.norm(pH - pL))
        err0 = float(np.linalg.norm(pH - Tg))
        d = float(np.clip(np.linalg.norm(Tg - pU), abs(a - b) + 1e-4, (a + b) * reach))
        u, v = pU - pL, pH - pL
        cos0 = float(np.clip(np.dot(u, v) / max(a * b, 1e-8), -1, 1))
        cos1 = float(np.clip((a * a + b * b - d * d) / max(2 * a * b, 1e-8), -1, 1))
        n1 = np.cross(u, v)
        if np.linalg.norm(n1) < 1e-6:          # straight limb: no bending plane, keep it straight
            return err0
        n1 = n1 / max(float(np.linalg.norm(n1)), 1e-8)
        R1 = _axis_angle(n1, np.arccos(cos1) - np.arccos(cos0))
        pH1 = pL + R1 @ (pH - pL)
        e0, e1 = pH1 - pU, Tg - pU
        R2 = _from_to((e0 / max(float(np.linalg.norm(e0)), 1e-8)).astype(np.float32),
                      (e1 / max(float(np.linalg.norm(e1)), 1e-8)).astype(np.float32))
        T[U] = R2 @ T[U]
        T[L] = R2 @ R1 @ T[L]
        return err0

    def locals_(self, T):
        loc = {}
        for i in self.order:
            p = self.parent.get(i)
            Lm = T[i] if p is None else T[p].T @ T[i]
            loc[i] = self.RY.T @ Lm @ self.RY
        return loc

    def _build_sole(self) -> None:
        """Sole point of each foot bone in that bone's local frame (virtual-vlog VrmRetargeter.build_sole):
        the lowest rest vertex weighted > 0.5 to the bone, carried rigidly with the bone. Taking the lowest vertex of
        the whole mesh every frame would let hair or a skirt hem steal the ground contact."""
        self.sole_local: dict[int, np.ndarray] = {}
        for n in self.foot_nodes:
            best = None
            for p in self.m.prims:
                if p.skin is None or p.joints is None:
                    continue
                col = np.nonzero(np.asarray(self.m.skins[p.skin]["joints"]) == n)[0]
                if not len(col):
                    continue
                v = ((p.joints == col[0]) * p.weights).max(1) > 0.5
                if v.sum() < 5:
                    continue
                P, _ = self.m.skin_primitive(p, self.rest, compute_normals=False)
                y = P[v][:, 1]
                k = int(np.argmin(y))
                if best is None or y[k] < best[0]:
                    best = (float(y[k]), P[v][k])
            if best is not None:
                self.sole_local[n] = np.linalg.inv(self.rest[n]) @ np.append(best[1], 1.0).astype(np.float32)


def _axis_angle(axis, angle):
    k = np.asarray(axis, np.float64)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return (np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * (K @ K)).astype(np.float32)


def _topo(nodes):
    child = {c for n in nodes for c in n.get("children", [])}
    order, stack = [], [i for i in range(len(nodes)) if i not in child]
    while stack:
        i = stack.pop()
        order.append(i)
        stack.extend(nodes[i].get("children", []))
    return order
