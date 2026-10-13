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

    def pose(self, joint_rots, coords_cam, arm_ik=True, leg_ik=True):
        """Pose the avatar for one frame (frames must be fed in order: spring bones keep state)."""
        rt = self.rt
        g, t, self.ik_err = rt.solve(joint_rots, coords_cam, self.scale, arm_ik=arm_ik, leg_ik=leg_ik)
        if self.spring is not None:
            # inertia source: the avatar's translation back in glTF units (A is a signed permutation, A^-1 = A^T)
            t_gltf = (rt.A.T @ t) / self.scale
            g = self.spring.step(g, t_gltf.astype(np.float32), self.dt, warmup=self.spring_warmup)
        self.apply(g, t)
        return g, t

    def apply(self, g, t):
        """Skin the mesh with node globals g (glTF space) and camera translation t, and upload it."""
        M = (self.scale * self.rt.A).astype(np.float32)
        for p, vbo in zip(self.m.prims, self.vbo):
            P, _ = self.m.skin_primitive(p, g)
            data = np.concatenate([P @ M.T + t, p.uvs], 1).astype("f4")
            vbo.write(np.ascontiguousarray(data))
        self.last_globals, self.last_t = g, t
        self.visible = True

    def bone_cam(self, bone: str):
        n = self.rt.m.humanoid.get(bone)
        if n is None or not self.visible:
            return None
        return self.scale * (self.rt.A @ self.last_globals[n][:3, 3]) + self.last_t

    def render(self, mvp, native=True):
        return self.rz.render(self.meshes, mvp, native)
