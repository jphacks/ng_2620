"""One VRM avatar driven by one tracked person: MHR skeleton -> VRM pose -> skinned camera-space mesh -> RGBA + depth.

Skinning runs on the CPU and the vertex buffers are rewritten every frame (as virtual-vlog scene3d.AvatarScene: with
hundreds of nodes the bone palette does not fit the uniform limits of every GL implementation).
"""

from __future__ import annotations

import numpy as np

from .raster import Rasterizer
from .retarget_mhr import MhrToVrm
from .vrm import VrmModel


class AvatarActor:
    def __init__(self, model: VrmModel, rz: Rasterizer, scale: float, fps: float = 30.0,
                 spring: bool = True, spring_warmup: int = 30):
        self.m, self.rz = model, rz
        self.rt = MhrToVrm(model)
        self.scale = float(scale)
        self.dt = 1.0 / float(fps)
        self.spring = None
        self.spring_warmup = int(spring_warmup)
        if spring and model.spring:
            from .springbone import SpringBoneSim
            self.spring = SpringBoneSim(model, center="none")
        ctx = rz.ctx
        self.vbo, self.meshes = [], []
        for p in model.prims:
            vbo = ctx.buffer(reserve=len(p.positions) * 5 * 4, dynamic=True)   # pos3 + uv2
            ibo = ctx.buffer(p.indices.astype("i4").tobytes())
            mat = model.materials[p.material] if p.material is not None else None
            tex = None
            if mat is not None and mat.base_color_texture is not None and 0 <= mat.base_color_texture < len(model.images):
                tex = rz.texture(mat.base_color_texture, model.images[mat.base_color_texture])
            vao = ctx.vertex_array(rz.prog, [(vbo, "3f 2f", "in_pos", "in_uv")], ibo)
            self.vbo.append(vbo)
            self.meshes.append({
                "vao": vao, "texture": tex,
                "cull": bool(mat is not None and not mat.double_sided),
                "uniforms": {
                    "base_color": tuple(float(x) for x in (mat.base_color_factor if mat is not None else np.ones(4))),
                    "has_tex": 1 if tex is not None else 0,
                    "alpha_cutoff": float(mat.alpha_cutoff) if mat is not None else 0.5,
                    "alpha_mask": {"OPAQUE": 0, "MASK": 1, "BLEND": 2}.get(mat.alpha_mode if mat else "OPAQUE", 1),
                },
            })
        self.visible = False

    def reset(self):
        if self.spring is not None:
            self.spring.reset()

    def pose(self, joint_rots, coords_cam, arm_ik=True, leg_ik=True, gpu=None, expr=None):
        """Pose the avatar for one frame (frames must be fed in order: spring bones keep state).
        expr: {VRM expression name: weight 0-1} (avatar/face.py), added to the rest mesh before skinning."""
        rt = self.rt
        g, t, self.ik_err = rt.solve(joint_rots, coords_cam, self.scale, arm_ik=arm_ik, leg_ik=leg_ik)
        if self.spring is not None:
            # inertia source: the avatar's translation back in glTF units (A is a signed permutation, A^-1 = A^T)
            t_gltf = (rt.A.T @ t) / self.scale
            g = self.spring.step(g, t_gltf.astype(np.float32), self.dt, warmup=self.spring_warmup)
        if gpu is not None:
            self.apply_gpu(g, t, gpu, expr)
        else:
            self.apply(g, t, expr)
        return g, t

    def _morphs(self, expr):
        """{expression: weight} -> {prim index: {target index: weight}} through the expressions' binds."""
        out = {}
        for name, w in (expr or {}).items():
            for pi, ti, bw in self.m.blendshapes.get(name, ()):
                d = out.setdefault(pi, {})
                d[ti] = d.get(ti, 0.0) + float(w) * bw
        return out

    def _gpu_init(self, dev):
        """Static GPU tensors of every primitive, merged: homogeneous rest positions, uvs, the 4 influences as
        indices into one palette of (node, inverse bind matrix) pairs, weights."""
        import torch
        m = self.m
        pal, pal_of = [], {}
        ph, uv, pi, wt, sizes = [], [], [], [], []
        for p in m.prims:
            V = len(p.positions)
            sizes.append(V)
            ph.append(np.concatenate([p.positions, np.ones((V, 1), np.float32)], 1))
            uv.append(p.uvs)
            if p.skin is None or p.joints is None:   # = skin_primitive: the rest positions, identity palette entry
                key = ("I",)
                if key not in pal_of:
                    pal_of[key] = len(pal)
                    pal.append((-1, np.eye(4, dtype=np.float32)))
                pi.append(np.full((V, 4), pal_of[key], np.int64))
                wt.append(np.tile(np.array([1, 0, 0, 0], np.float32), (V, 1)))
                continue
            skin = m.skins[p.skin]
            jl = np.asarray(skin["joints"], np.int64)
            col = np.empty(len(jl), np.int64)
            for k, nd in enumerate(jl):
                key = (p.skin, k)
                if key not in pal_of:
                    pal_of[key] = len(pal)
                    pal.append((int(nd), skin["_ibm"][k].astype(np.float32)))
                col[k] = pal_of[key]
            pi.append(col[p.joints])
            wt.append(p.weights.astype(np.float32))
        T = lambda x, dt=torch.float32: torch.from_numpy(np.ascontiguousarray(np.concatenate(x) if isinstance(x, list) else x)).to(dev, dt)
        self._g = dict(ph=T(ph), uv=T(uv), pi=T(pi, torch.int64), w=T(wt),
                       node=torch.tensor([n for n, _ in pal], device=dev),
                       ibm=T(np.stack([b for _, b in pal])), sizes=sizes, dev=dev)
        # morph targets that some expression binds: merged vertex rows and position deltas, one entry per (prim, target)
        offs = np.cumsum([0] + sizes)
        self._g["morph"] = {}
        for pi_, ti in sorted({(pi_, ti) for b in m.blendshapes.values() for pi_, ti, _ in b}):
            d = m.prims[pi_].targets[ti]
            rows = np.nonzero(np.abs(d).max(1) > 0)[0]
            if len(rows):
                self._g["morph"][(pi_, ti)] = (T(rows + offs[pi_], torch.int64), T(d[rows]))

    def apply_gpu(self, g, t, dev, expr=None):
        """apply() with the skinning on the GPU (one pass over all primitives, then one copy back for the vertex
        buffers), and the screen box of the projected vertices (screen_box) computed there as well."""
        import torch
        if getattr(self, "_g", None) is None:
            self._gpu_init(dev)
        G = self._g
        ph = G["ph"]
        mw = self._morphs(expr)
        if mw:
            ph = ph.clone()
            for pi_, tw in mw.items():
                for ti, w in tw.items():
                    if w > 1e-4 and (pi_, ti) in G["morph"]:
                        rows, d = G["morph"][(pi_, ti)]
                        ph[rows, :3] += d * w
        gt = torch.from_numpy(np.ascontiguousarray(g, np.float32)).to(dev)
        node = G["node"]
        Mg = torch.where((node >= 0)[:, None, None], gt[node.clamp(min=0)], torch.eye(4, device=dev))
        Mp = (Mg[:, :3, :, None] * G["ibm"][:, None, :, :]).sum(2)          # (Pn,3,4) = (globals @ ibm)[:3]
        P = torch.zeros((len(G["ph"]), 3), device=dev)
        for k in range(4):
            Mk = Mp[G["pi"][:, k]]                                         # (V,3,4)
            P = P + G["w"][:, k:k + 1] * (Mk * ph[:, None, :]).sum(-1)
        M = torch.from_numpy((self.scale * self.rt.A).astype(np.float32)).to(dev)
        tt = torch.from_numpy(np.asarray(t, np.float32)).to(dev)
        C = (P[:, None, :] * M[None]).sum(-1) + tt                          # P @ M.T + t
        data = torch.cat([C, G["uv"]], 1).cpu().numpy()
        o = 0
        for V, vbo in zip(G["sizes"], self.vbo):
            vbo.write(np.ascontiguousarray(data[o:o + V]))
            o += V
        self.cam_pts = [data[:, :3]]
        self.last_globals, self.last_t = g, t
        self.visible = True

    def apply(self, g, t, expr=None):
        """Skin the mesh with node globals g (glTF space) and camera translation t, and upload it."""
        M = (self.scale * self.rt.A).astype(np.float32)
        self.cam_pts = []
        mw = self._morphs(expr)
        for k, (p, vbo) in enumerate(zip(self.m.prims, self.vbo)):
            P, _ = self.m.skin_primitive(p, g, mw.get(k), compute_normals=False)
            data = np.concatenate([P @ M.T + t, p.uvs], 1).astype("f4")
            vbo.write(np.ascontiguousarray(data))
            self.cam_pts.append(data[:, :3])
        self.last_globals, self.last_t = g, t
        self.visible = True

    def bone_cam(self, bone: str):
        n = self.rt.m.humanoid.get(bone)
        if n is None or not self.visible:
            return None
        return self.scale * (self.rt.A @ self.last_globals[n][:3, 3]) + self.last_t

    def render(self, mvp, native=True):
        return self.rz.render(self.meshes, mvp, native)

    def screen_box(self, K, margin=2):
        """Native-pixel box (x0, y0, x1, y1) that holds every rasterised pixel of the current pose (projected
        vertices + margin), clipped to the frame; None if nothing is in view, the full frame if a vertex is at or
        behind the near plane (its projection is unbounded)."""
        rz = self.rz
        P = np.concatenate(self.cam_pts)
        if np.any(P[:, 2] <= rz.near):
            return (0, 0, rz.W, rz.H)
        u = K[0, 0] * P[:, 0] / P[:, 2] + K[0, 2]
        v = K[1, 1] * P[:, 1] / P[:, 2] + K[1, 2]
        x0, x1 = int(np.floor(u.min())) - margin, int(np.ceil(u.max())) + margin
        y0, y1 = int(np.floor(v.min())) - margin, int(np.ceil(v.max())) + margin
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, rz.W), min(y1, rz.H)
        return None if x0 >= x1 or y0 >= y1 else (x0, y0, x1, y1)
