"""VRM (glTF 2.0) loading and linear blend skinning, no Blender.

Ported from virtual-vlog (github.com/Akatuki25/virtual-vlog @ a5650f3, src/vlogvrm/core/vrm.py); the loader and the
skinning are unchanged. Only the pipeline-specific world frame (GLTF_TO_WORLD, Z-up) was removed: here the
retargeter (retarget_mhr.py) maps glTF space to the MHR model frame itself.

  1. node hierarchy and skins (inverse bind matrices, joint nodes)
  2. mesh primitives (positions, normals, UVs, joint indices, weights, morph targets)
  3. VRM Humanoid bone mapping (`VRMC_vrm` / `VRM`), bone names in snake_case

Materials are MToon; like virtual-vlog the base colour texture is drawn as is (unlit).
glTF space: Y up, right-handed. A VRM 0.x model faces -Z in glTF space (Unity origin), a VRM 1.0 model faces +Z.
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_COMP = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
         5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


@dataclass
class Primitive:
    positions: np.ndarray          # (V,3)
    normals: np.ndarray            # (V,3)
    uvs: np.ndarray                # (V,2)
    indices: np.ndarray            # (F,3) int32
    joints: np.ndarray | None      # (V,4) int32
    weights: np.ndarray | None     # (V,4) float32
    material: int | None
    skin: int | None
    mesh: int | None = None
    targets: list[np.ndarray] = field(default_factory=list)   # モーフターゲット (V,3) 位置差分


@dataclass
class Material:
    base_color_factor: np.ndarray
    base_color_texture: int | None
    alpha_mode: str
    alpha_cutoff: float
    double_sided: bool
    name: str = ""


@dataclass
class VrmModel:
    nodes: list[dict]
    prims: list[Primitive]
    materials: list[Material]
    images: list[np.ndarray]                  # RGBA uint8
    skins: list[dict]
    humanoid: dict[str, int] = field(default_factory=dict)   # bone name -> node index
    roots: list[int] = field(default_factory=list)
    spring: dict | None = None                # Spring Bone（springbone.parse_spring の形）
    blendshapes: dict[str, list] = field(default_factory=dict)  # 表情名 -> [(prim idx, target idx, weight)]

    # --- ノード階層 ---------------------------------------------------
    def local_matrix(self, i: int, pose: dict[int, np.ndarray] | None = None) -> np.ndarray:
        """ノード i のローカル行列。pose で回転(3x3)を差し替えられる。"""
        n = self.nodes[i]
        if "matrix" in n and pose is None:
            return np.array(n["matrix"], np.float32).reshape(4, 4).T
        t = np.array(n.get("translation", [0, 0, 0]), np.float32)
        s = np.array(n.get("scale", [1, 1, 1]), np.float32)
        if pose is not None and i in pose:
            R = pose[i]
        else:
            q = np.array(n.get("rotation", [0, 0, 0, 1]), np.float32)   # xyzw
            R = _quat_to_mat(q)
        m = np.eye(4, dtype=np.float32)
        m[:3, :3] = R * s[None, :]
        m[:3, 3] = t
        return m

    def global_matrices(self, pose: dict[int, np.ndarray] | None = None) -> np.ndarray:
        """全ノードのグローバル行列 (N,4,4)。親→子の順に合成する。"""
        N = len(self.nodes)
        out = np.tile(np.eye(4, dtype=np.float32), (N, 1, 1))
        parent = {}
        for i, n in enumerate(self.nodes):
            for c in n.get("children", []):
                parent[c] = i
        order = _topo_order(self.nodes)
        for i in order:
            local = self.local_matrix(i, pose)
            p = parent.get(i)
            out[i] = local if p is None else out[p] @ local
        return out

    def rest_globals(self) -> np.ndarray:
        return self.global_matrices(None)

    # --- スキニング -----------------------------------------------------
    def skin_primitive(self, prim: Primitive, globals_: np.ndarray,
                       morph: dict[int, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
        """線形ブレンドスキニング。戻り値は (頂点, 法線)。

        morph: {target index: weight} をスキニング前の頂点位置に足す（表情）。
        """
        base = prim.positions
        if morph and prim.targets:
            base = base.copy()
            for ti, wt in morph.items():
                if wt > 1e-4 and ti < len(prim.targets):
                    base += prim.targets[ti] * np.float32(wt)
        if prim.skin is None or prim.joints is None:
            return base.copy(), prim.normals.copy()
        skin = self.skins[prim.skin]
        joints = np.asarray(skin["joints"], np.int32)
        ibm = skin["_ibm"]                                     # (J,4,4)
        M = globals_[joints] @ ibm                             # (J,4,4)
        w = prim.weights
        idx = prim.joints
        # (V,4,4,4) を作らず、影響4本を順に足す
        P = np.zeros_like(prim.positions)
        Nn = np.zeros_like(prim.normals)
        ones = np.ones((len(prim.positions), 1), np.float32)
        ph = np.concatenate([base, ones], 1)                    # (V,4)
        for k in range(idx.shape[1]):
            wk = w[:, k:k + 1]
            if not np.any(wk):
                continue
            Mk = M[idx[:, k]]                                   # (V,4,4)
            P += wk * np.einsum("vij,vj->vi", Mk, ph)[:, :3]
            Nn += wk * np.einsum("vij,vj->vi", Mk[:, :3, :3], prim.normals)
        nl = np.linalg.norm(Nn, axis=1, keepdims=True)
        Nn = Nn / np.maximum(nl, 1e-8)
        return P, Nn


# ---------------------------------------------------------------------------


def _quat_to_mat(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], np.float32)


def _topo_order(nodes: list[dict]) -> list[int]:
    child = {c for n in nodes for c in n.get("children", [])}
    order, stack = [], [i for i in range(len(nodes)) if i not in child]
    while stack:
        i = stack.pop()
        order.append(i)
        stack.extend(nodes[i].get("children", []))
    return order


def _read_accessor(gltf: dict, buffers: list[bytes], idx: int) -> np.ndarray:
    acc = gltf["accessors"][idx]
    n = acc["count"]
    ncomp = _NCOMP[acc["type"]]
    fmt, size = _COMP[acc["componentType"]]
    if "bufferView" not in acc:
        return np.zeros((n, ncomp), np.float32).squeeze()
    bv = gltf["bufferViews"][acc["bufferView"]]
    buf = buffers[bv.get("buffer", 0)]
    start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = bv.get("byteStride") or (size * ncomp)
    dt = {"b": np.int8, "B": np.uint8, "h": np.int16,
          "H": np.uint16, "I": np.uint32, "f": np.float32}[fmt]
    if stride == size * ncomp:
        arr = np.frombuffer(buf, dt, count=n * ncomp, offset=start).reshape(n, ncomp)
    else:
        raw = np.frombuffer(buf, np.uint8, count=stride * n, offset=start).reshape(n, stride)
        arr = raw[:, :size * ncomp].copy().view(dt).reshape(n, ncomp)
    arr = arr.astype(np.float32) if dt == np.float32 else arr
    if acc.get("normalized") and dt in (np.uint8, np.uint16):
        arr = arr.astype(np.float32) / np.iinfo(dt).max
    return arr.squeeze() if ncomp == 1 else arr


def load_vrm(path: str | Path) -> VrmModel:
    """.vrm / .glb / .gltf を読む。"""
    path = Path(path)
    data = path.read_bytes()
    if data[:4] == b"glTF":
        # GLB: 12バイトヘッダ + チャンク列
        _, _, _ = struct.unpack("<III", data[:12])
        off, gltf, bin_chunk = 12, None, b""
        while off < len(data):
            clen, ctype = struct.unpack("<II", data[off:off + 8])
            body = data[off + 8:off + 8 + clen]
            if ctype == 0x4E4F534A:
                gltf = json.loads(body.decode("utf-8"))
            elif ctype == 0x004E4942:
                bin_chunk = body
            off += 8 + clen + ((4 - clen % 4) % 4 if clen % 4 else 0)
        assert gltf is not None
    else:
        gltf = json.loads(data.decode("utf-8"))
        bin_chunk = b""

    buffers = []
    for b in gltf.get("buffers", []):
        uri = b.get("uri")
        if uri is None:
            buffers.append(bin_chunk)
        elif uri.startswith("data:"):
            buffers.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            buffers.append((path.parent / uri).read_bytes())

    # --- 画像 ---
    images: list[np.ndarray] = []
    for im in gltf.get("images", []):
        raw = None
        if "bufferView" in im:
            bv = gltf["bufferViews"][im["bufferView"]]
            raw = buffers[bv.get("buffer", 0)][bv.get("byteOffset", 0):
                                               bv.get("byteOffset", 0) + bv["byteLength"]]
        elif im.get("uri", "").startswith("data:"):
            raw = base64.b64decode(im["uri"].split(",", 1)[1])
        elif "uri" in im:
            raw = (path.parent / im["uri"]).read_bytes()
        if raw is None:
            images.append(np.zeros((1, 1, 4), np.uint8))
            continue
        import cv2
        arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
        if arr is None:
            arr = np.zeros((1, 1, 4), np.uint8)
        elif arr.ndim == 2:
            arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGBA)
        elif arr.shape[2] == 3:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGBA)
        else:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGBA)
        images.append(arr)

    # --- マテリアル ---
    mats: list[Material] = []
    for m in gltf.get("materials", []):
        pbr = m.get("pbrMetallicRoughness", {})
        tex = pbr.get("baseColorTexture", {}).get("index")
        src = None
        if tex is not None:
            src = gltf["textures"][tex].get("source")
        mats.append(Material(
            base_color_factor=np.array(pbr.get("baseColorFactor", [1, 1, 1, 1]), np.float32),
            base_color_texture=src,
            alpha_mode=m.get("alphaMode", "OPAQUE"),
            alpha_cutoff=float(m.get("alphaCutoff", 0.5)),
            double_sided=bool(m.get("doubleSided", False)),
            name=m.get("name", ""),
        ))

    # --- スキン ---
    skins = []
    for sk in gltf.get("skins", []):
        d = dict(sk)
        if "inverseBindMatrices" in sk:
            ibm = _read_accessor(gltf, buffers, sk["inverseBindMatrices"])
            d["_ibm"] = ibm.reshape(-1, 4, 4).transpose(0, 2, 1).astype(np.float32)
        else:
            d["_ibm"] = np.tile(np.eye(4, np.float32), (len(sk["joints"]), 1, 1))
        skins.append(d)

    # --- メッシュ（ノードに紐づくものだけ） ---
    node_skin = {i: n.get("skin") for i, n in enumerate(gltf.get("nodes", []))}
    prims: list[Primitive] = []
    for i, n in enumerate(gltf.get("nodes", [])):
        if "mesh" not in n:
            continue
        for p in gltf["meshes"][n["mesh"]].get("primitives", []):
            a = p["attributes"]
            pos = _read_accessor(gltf, buffers, a["POSITION"]).astype(np.float32).reshape(-1, 3)
            nrm = (_read_accessor(gltf, buffers, a["NORMAL"]).astype(np.float32).reshape(-1, 3)
                   if "NORMAL" in a else np.zeros_like(pos))
            uv = (_read_accessor(gltf, buffers, a["TEXCOORD_0"]).astype(np.float32).reshape(-1, 2)
                  if "TEXCOORD_0" in a else np.zeros((len(pos), 2), np.float32))
            jo = (_read_accessor(gltf, buffers, a["JOINTS_0"]).astype(np.int32).reshape(-1, 4)
                  if "JOINTS_0" in a else None)
            we = (_read_accessor(gltf, buffers, a["WEIGHTS_0"]).astype(np.float32).reshape(-1, 4)
                  if "WEIGHTS_0" in a else None)
            if we is not None:
                we = we / np.maximum(we.sum(1, keepdims=True), 1e-8)
            idx = (_read_accessor(gltf, buffers, p["indices"]).astype(np.int32).reshape(-1, 3)
                   if "indices" in p else np.arange(len(pos), dtype=np.int32).reshape(-1, 3))
            targets = []
            for tg in p.get("targets", []):
                if "POSITION" in tg:
                    targets.append(_read_accessor(gltf, buffers, tg["POSITION"])
                                   .astype(np.float32).reshape(-1, 3))
                else:
                    targets.append(np.zeros_like(pos))
            prims.append(Primitive(pos, nrm, uv, idx, jo, we, p.get("material"), node_skin.get(i),
                                   mesh=n["mesh"], targets=targets))

    # --- VRM Humanoid（0.x と 1.0 の両方） ---
    ext = gltf.get("extensions", {})
    humanoid: dict[str, int] = {}
    if "VRMC_vrm" in ext:
        for name, hb in ext["VRMC_vrm"].get("humanoid", {}).get("humanBones", {}).items():
            if isinstance(hb, dict) and "node" in hb:
                humanoid[name] = hb["node"]
    elif "VRM" in ext:
        for hb in ext["VRM"].get("humanoid", {}).get("humanBones", []):
            if "bone" in hb and "node" in hb:
                # VRM0 は camelCase。snake_case に寄せる
                humanoid[_camel_to_snake(hb["bone"])] = hb["node"]

    # --- 表情（BlendShape）: 名前 -> [(prim index, target index, weight 0-1)] ---
    blendshapes: dict[str, list] = {}
    if "VRM" in ext:
        for grp in ext["VRM"].get("blendShapeMaster", {}).get("blendShapeGroups", []):
            binds = []
            for b in grp.get("binds", []):
                for pi, pr in enumerate(prims):
                    if pr.mesh == b.get("mesh") and b.get("index", -1) < len(pr.targets):
                        binds.append((pi, int(b["index"]), float(b.get("weight", 100)) / 100.0))
            if binds:
                blendshapes[str(grp.get("name", grp.get("presetName", "")))] = binds
    elif "VRMC_vrm" in ext:
        for name, ex in ext["VRMC_vrm"].get("expressions", {}).get("preset", {}).items():
            binds = []
            for b in ex.get("morphTargetBinds", []):
                node = b.get("node")
                mesh = gltf["nodes"][node].get("mesh") if node is not None else None
                for pi, pr in enumerate(prims):
                    if pr.mesh == mesh and b.get("index", -1) < len(pr.targets):
                        binds.append((pi, int(b["index"]), float(b.get("weight", 1.0))))
            if binds:
                blendshapes[name[:1].upper() + name[1:]] = binds

    from .springbone import parse_spring
    roots = gltf.get("scenes", [{}])[gltf.get("scene", 0)].get("nodes", [])
    return VrmModel(nodes=gltf.get("nodes", []), prims=prims, materials=mats,
                    images=images, skins=skins, humanoid=humanoid, roots=list(roots),
                    spring=parse_spring(gltf), blendshapes=blendshapes)


def _camel_to_snake(s: str) -> str:
    out = []
    for ch in s:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)
