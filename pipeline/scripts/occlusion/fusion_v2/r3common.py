"""Production shim for the frozen R3_fusion v2 occlusion code (2026-10-13 study, frozen/fusion_v2).

evidence.py, fusion.py, gpu_stage.py, prep.py, kp2vert.npy and vertex_parts.npy are byte-identical copies of the
frozen candidate (see MD5.txt). Only this module differs: the study's Run looked a run up by name under testdata/;
here Run reads any pipeline output directory (images/, masks/, mesh_4d_individual/, focal_4d_individual/), with the
same conventions:
  * mesh_4d_individual/<pid>/<stem>.ply: vertices in OpenGL camera coords; Run.mesh() returns OpenCV camera coords
    (x right, y down, z forward, metres); a vertex X projects to u = f X/Z + W/2, v = f Y/Z + H/2
  * fx = fy = focal_4d_individual/<pid>/<stem>.json["focal_length"], principal point = image centre
  * masks/<stem>.png: SAM3 visible masks as a label map (0 = background, k = pid k)
Hide masks: <method_dir>/hide/<run name>/<pid>/<stem>.png, 255 = a real surface is in front of the person's mesh.
"""
import contextlib
import glob
import json
import os

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))).replace("\\", "/")

_GL = np.array([1.0, -1.0, -1.0])  # OpenGL <-> OpenCV camera axes


def ckpt(rel):
    """<ckpt_root>/<rel>: paths.ckpt_root of configs/body4d.yaml (the root the mesh pipeline reads its checkpoints
    from), overridden by $BODY4D_CKPT_ROOT."""
    root = os.environ.get("BODY4D_CKPT_ROOT")
    if not root:
        from omegaconf import OmegaConf
        root = OmegaConf.load(f"{REPO}/configs/body4d.yaml").paths.ckpt_root
    return f"{root}/{rel}"


class Run:
    def __init__(self, pout, name=None):
        self.pout = os.path.abspath(pout).replace("\\", "/")
        self.dir = os.path.dirname(self.pout)
        self.name = name or os.path.basename(self.dir)
        self.stems = [os.path.basename(p)[:-4] for p in sorted(glob.glob(f"{self.pout}/images/*.jpg"))]
        self.pids = sorted(int(os.path.basename(d)) for d in glob.glob(f"{self.pout}/mesh_4d_individual/*"))
        self.H, self.W = cv2.imread(f"{self.pout}/images/{self.stems[0]}.jpg").shape[:2]

    def __len__(self):
        return len(self.stems)

    def image(self, i):
        return cv2.imread(f"{self.pout}/images/{self.stems[i]}.jpg")

    def mask(self, i):
        from PIL import Image
        return np.array(Image.open(f"{self.pout}/masks/{self.stems[i]}.png"))

    def focal(self, pid, i):
        p = f"{self.pout}/focal_4d_individual/{pid}/{self.stems[i]}.json"
        return json.load(open(p))["focal_length"] if os.path.exists(p) else None

    def mesh(self, pid, i):
        import trimesh
        p = f"{self.pout}/mesh_4d_individual/{pid}/{self.stems[i]}.ply"
        if not os.path.exists(p):
            return None
        m = trimesh.load(p, process=False)  # keep vertex order
        if len(m.vertices) == 0:            # empty export (NaN person, pre-fix runs): treat as absent
            return None
        return np.asarray(m.vertices) * _GL, np.asarray(m.faces)

    def mesh_depth(self, pid, i):
        """Per-pixel camera-space depth Z [m] of person pid's mesh (0 where the mesh does not cover the pixel)."""
        m = self.mesh(pid, i)
        if m is None:
            return np.zeros((self.H, self.W), np.float32)
        return render_depth(m[0], m[1], self.focal(pid, i), self.W, self.H)


_R = {}


def _renderer(W, H):
    import pyrender
    if (W, H) not in _R:
        _R[(W, H)] = pyrender.OffscreenRenderer(W, H)
    return _R[(W, H)]


def render_depth(verts_cv, faces, focal, W, H):
    """Depth (Z along the optical axis, metres) of a mesh given in OpenCV camera coords; 0 = empty."""
    import pyrender
    import trimesh
    scene = pyrender.Scene()
    scene.add(pyrender.Mesh.from_trimesh(trimesh.Trimesh(verts_cv * _GL, faces, process=False)))
    scene.add(pyrender.IntrinsicsCamera(fx=focal, fy=focal, cx=W / 2, cy=H / 2, zfar=1e4), pose=np.eye(4))
    d = _renderer(W, H).render(scene, flags=pyrender.RenderFlags.DEPTH_ONLY)
    return d.astype(np.float32)


def render_depth_fast(verts_cv, faces, focal, W, H):
    """render_depth without the vertex normals that pyrender.Mesh.from_trimesh computes (smooth shading, ~7 ms per
    mesh, half of the call): a depth-only render reads only positions and indices, so the depth is the same
    (scripts/eval/check_prep_fast.py)."""
    import pyrender
    scene = pyrender.Scene()
    prim = pyrender.Primitive(positions=(verts_cv * _GL).astype(np.float32), indices=np.asarray(faces, np.uint32),
                              mode=pyrender.constants.GLTF.TRIANGLES)
    scene.add(pyrender.Mesh([prim]))
    scene.add(pyrender.IntrinsicsCamera(fx=focal, fy=focal, cx=W / 2, cy=H / 2, zfar=1e4), pose=np.eye(4))
    d = _renderer(W, H).render(scene, flags=pyrender.RenderFlags.DEPTH_ONLY)
    return d.astype(np.float32)


def save_hide(method_dir, run, pid, i, hide):
    d = f"{method_dir}/hide/{run.name}/{pid}"
    os.makedirs(d, exist_ok=True)
    h = hide.astype(np.float32)
    if h.max() <= 1.0:
        h = h * 255.0
    cv2.imwrite(f"{d}/{run.stems[i]}.png", np.clip(h, 0, 255).astype(np.uint8))


@contextlib.contextmanager
def local_gpu(owner, stale_s=None):
    """The study shared one GPU between agents through a lock directory; a pipeline run owns the GPU, so this only
    frees the cached memory when the GPU stage ends."""
    try:
        yield
    finally:
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
