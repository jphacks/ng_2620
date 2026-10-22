"""Offscreen moderngl rasterizer for VRM avatars (unlit base colour + per-pixel camera depth).

Ported from virtual-vlog (github.com/Akatuki25/virtual-vlog @ a5650f3, src/vlogvrm/core/raster.py), pass A only:
the shadow / shading pass B is not ported (SAM-Body4D has no ground plane or light estimate). Kept as is:
  - MToon is drawn unlit (base colour texture as is), like virtual-vlog
  - alpha modes OPAQUE / MASK (cutout) / BLEND; opaque and cutout first with depth writes, then the translucent
    materials (eyes, brows, lash lines) without depth writes
  - back-face culling for single-sided materials (VRoid hair collapses into a blob when drawn double-sided)
  - textures uploaded as is with glTF UVs as is (flipping only one of the two turns cutouts upside down)
  - SSAA: render at ssaa x resolution and area-average down (hard 1 px edges look cut out over video)
Added: the depth buffer is read back and converted to camera Z (metres) for compositing and occlusion.

Camera: OpenCV convention (x right, y down, z forward), intrinsics K with the principal point anywhere.
Vertices are given directly in camera coordinates.
"""

from __future__ import annotations

import os

import numpy as np

_VERT = """
#version 330
uniform mat4 mvp;
in vec3 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    v_uv = in_uv;
    gl_Position = mvp * vec4(in_pos, 1.0);
}
"""

_FRAG_UNLIT = """
#version 330
uniform sampler2D tex;
uniform vec4 base_color;
uniform int has_tex;
uniform float alpha_cutoff;
uniform int alpha_mask;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec4 c = base_color;
    if (has_tex == 1) c *= texture(tex, v_uv);
    // alpha_mask: 0=OPAQUE (ignore alpha) 1=MASK (cutout) 2=BLEND
    if (alpha_mask == 0) c.a = 1.0;
    else if (alpha_mask == 1 && c.a < alpha_cutoff) discard;
    else if (alpha_mask == 2 && c.a < 0.004) discard;
    f_color = c;
}
"""


def perspective_from_K(K, W, H, near=0.05, far=200.0):
    """OpenGL projection from OpenCV intrinsics. Camera frame X right / Y down / Z forward (OpenCV); the view matrix
    flips Y and Z to OpenGL's Y up / -Z forward before this projection."""
    fx, fy = float(K[0, 0]), float(K[1, 1])
    cx, cy = float(K[0, 2]), float(K[1, 2])
    m = np.zeros((4, 4), np.float32)
    m[0, 0] = 2 * fx / W
    m[1, 1] = 2 * fy / H
    m[0, 2] = 1 - 2 * cx / W
    m[1, 2] = 2 * cy / H - 1
    m[2, 2] = -(far + near) / (far - near)
    m[2, 3] = -2 * far * near / (far - near)
    m[3, 2] = -1
    return m


def camera_mvp(K, W, H, near=0.05, far=200.0):
    view = np.diag(np.array([1, -1, -1, 1], np.float32))   # OpenCV camera -> OpenGL camera
    return (perspective_from_K(K, W, H, near, far) @ view).astype(np.float32)


class Rasterizer:
    def __init__(self, width: int, height: int, ssaa: int = 2, near: float = 0.05, far: float = 200.0):
        import moderngl

        if os.environ.get("MODERNGL_BACKEND"):
            self.ctx = moderngl.create_context(standalone=True, require=330, backend=os.environ["MODERNGL_BACKEND"])
        else:
            self.ctx = moderngl.create_context(standalone=True, require=330)
        self.mgl = moderngl
        self.W, self.H = int(width), int(height)
        self.ssaa = max(1, int(ssaa))
        self.RW, self.RH = self.W * self.ssaa, self.H * self.ssaa
        self.near, self.far = near, far
        self.prog = self.ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG_UNLIT)
        self.color = self.ctx.texture((self.RW, self.RH), 4, dtype="f1")
        self.depth = self.ctx.depth_texture((self.RW, self.RH))
        self.fbo = self.ctx.framebuffer([self.color], self.depth)
        self._tex_cache: dict[int, "moderngl.Texture"] = {}

    def texture(self, idx: int, image: np.ndarray):
        if idx not in self._tex_cache:
            img = np.ascontiguousarray(image)
            t = self.ctx.texture((img.shape[1], img.shape[0]), 4, img.tobytes())
            t.build_mipmaps()
            t.filter = (self.mgl.LINEAR_MIPMAP_LINEAR, self.mgl.LINEAR)
            t.anisotropy = 8.0
            self._tex_cache[idx] = t
        return self._tex_cache[idx]

    def _draw(self, meshes):
        for mesh in meshes:
            if mesh.get("cull"):
                self.ctx.enable(self.mgl.CULL_FACE)
                self.ctx.cull_face = "back"
            else:
                self.ctx.disable(self.mgl.CULL_FACE)
            for key, val in mesh["uniforms"].items():
                if key in self.prog:
                    self.prog[key].value = val
            if mesh.get("texture") is not None:
                mesh["texture"].use(0)
                self.prog["tex"].value = 0
            mesh["vao"].render(self.mgl.TRIANGLES)

    def render_box(self, meshes, mvp, box):
        """render() read back only inside box = (x0, y0, x1, y1) in supersampled pixels, top-left origin (the
        drawing is the same: the full frame is rasterised). -> (rgba uint8, depth float32) of the box."""
        x0, y0, x1, y1 = box
        self._draw_all(meshes, mvp)
        vp = (x0, self.RH - y1, x1 - x0, y1 - y0)
        img = np.frombuffer(self.fbo.read(viewport=vp, components=4, dtype="f1"), np.uint8).reshape(y1 - y0, x1 - x0, 4)
        d = np.frombuffer(self.fbo.read(viewport=vp, components=1, attachment=-1, dtype="f4"),
                          np.float32).reshape(y1 - y0, x1 - x0)
        return self._readback(img, d)

    def render_box_gpu(self, meshes, mvp, box, dev):
        """render_box with the depth conversion and the flip done on the GPU (torch): -> (rgba uint8, depth
        float32) tensors on dev. The same float32 expression as _readback."""
        import torch
        x0, y0, x1, y1 = box
        self._draw_all(meshes, mvp)
        vp = (x0, self.RH - y1, x1 - x0, y1 - y0)
        img = torch.frombuffer(bytearray(self.fbo.read(viewport=vp, components=4, dtype="f1")), dtype=torch.uint8)
        d = torch.frombuffer(bytearray(self.fbo.read(viewport=vp, components=1, attachment=-1, dtype="f4")),
                             dtype=torch.float32)
        img = img.to(dev).reshape(y1 - y0, x1 - x0, 4).flip(0)
        d = d.to(dev).reshape(y1 - y0, x1 - x0).flip(0)
        n, f = np.float32(self.near), np.float32(self.far)
        z = (2.0 * n * f) / ((f + n) - (2.0 * d - 1.0) * (f - n))
        return img, torch.where(d < 1.0, z, torch.full_like(z, float("inf")))

    def _readback(self, img, d):
        n, f = self.near, self.far
        z = 2.0 * n * f / (f + n - (2.0 * d - 1.0) * (f - n))      # window depth -> eye distance along the axis
        z = np.where(d < 1.0, z, np.inf).astype(np.float32)
        return np.ascontiguousarray(img[::-1]), np.ascontiguousarray(z[::-1])

    def render(self, meshes, mvp, native=True):
        """-> (rgba uint8 (H,W,4), depth float32 (H,W) camera Z in metres, inf where nothing is drawn).
        native=False returns the supersampled buffers (H*ssaa, W*ssaa) instead, for compositing several avatars
        before the downsample (per-pixel depth order at native resolution aliases where two avatars intersect)."""
        self._draw_all(meshes, mvp)
        img = np.frombuffer(self.fbo.read(components=4, dtype="f1"), np.uint8).reshape(self.RH, self.RW, 4)
        d = np.frombuffer(self.depth.read(), np.float32).reshape(self.RH, self.RW)
        img, z = self._readback(img, d)
        return self.downsample(img, z) if native else (img, z)

    def _draw_all(self, meshes, mvp):
        self.fbo.use()
        self.ctx.clear(0, 0, 0, 0, depth=1.0)
        self.ctx.enable(self.mgl.DEPTH_TEST)
        # alpha is coverage: blend it with its own equation so it is not squared
        self.ctx.blend_func = (self.mgl.SRC_ALPHA, self.mgl.ONE_MINUS_SRC_ALPHA,
                               self.mgl.ONE, self.mgl.ONE_MINUS_SRC_ALPHA)
        self.prog["mvp"].write(np.ascontiguousarray(mvp.T))
        self.ctx.disable(self.mgl.BLEND)
        solid = [m for m in meshes if m["uniforms"]["alpha_mask"] != 2]
        blend = [m for m in meshes if m["uniforms"]["alpha_mask"] == 2]
        self._draw(solid)
        if blend:
            self.ctx.enable(self.mgl.BLEND)
            self.ctx.depth_mask = False
            self._draw(blend)
            self.ctx.depth_mask = True
            self.ctx.disable(self.mgl.BLEND)

    def downsample(self, img, z):
        """Supersampled (rgba, depth) -> native: area average of the colour, nearest surface of the depth."""
        if self.ssaa == 1:
            return img, z
        import cv2
        s = self.ssaa
        return (cv2.resize(img, (self.W, self.H), interpolation=cv2.INTER_AREA),
                np.ascontiguousarray(z.reshape(self.H, s, self.W, s).min(axis=(1, 3))))
