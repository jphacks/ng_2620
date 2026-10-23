"""VRM Spring Bone（髪・スカート・リボンの二次運動）の Verlet シミュレーション。

Ported unchanged from virtual-vlog (github.com/Akatuki25/virtual-vlog @ a5650f3, src/vlogvrm/core/springbone.py).

VRM 0.x の `secondaryAnimation`（VRoid 出力は VRMSpringBoneTool/VRMSkirtTool が
生成する）をそのまま解釈する。UniVRM の `VRMSpringBoneLogic.Update` と同じ更新式:

    next = cur + (cur - prev) * (1 - drag)                # 慣性
           + parentRot * localRot * boneAxis * (stiffness * dt)   # rest 方向への復元
           + gravityDir * (gravityPower * dt)              # 外力
    next = head + normalize(next - head) * length          # 骨長を保つ
    球コライダと交差していれば押し出して再び骨長へ投影
    rot = FromTo(parentRot*localRot*boneAxis, next - head) * parentRot*localRot

パラメータは VRM ファイルに**作者が入れた値**を使う（このモジュール側の調律定数は
無い）。ここで決めるのは座標系と時間刻みだけ:
  - 空間は glTF 空間（VRM のメートル）。アバターのワールド平行移動は glTF 空間へ
    戻して足す＝歩行の慣性が髪に乗る。`center="hips"` で腰基準にすると
    ルート並進の慣性を切れる（VRM 1.0 の center と同じ意味）
  - dt = 1/fps。UniVRM は Time.deltaTime を掛けるので、この時間刻みでの挙動が
    作者が Unity で見ていたものと一致する

パイプラインのワールドはカメラ固定なので、カメラのパンでもアバターが「動く」。
手持ち Vlog では人物が画面内に留まる（カメラが追う）ため、実際の歩行慣性は
大半が失われ、残るのは体の上下動と回転。`center` の選択は目視で決める。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .vrm import VrmModel, _quat_to_mat


def _from_to(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """単位ベクトル a を b へ回す 3x3 回転（Rodrigues）。"""
    v = np.cross(a, b)
    s = float(np.linalg.norm(v))
    c = float(np.dot(a, b))
    if s < 1e-8:
        if c > 0:
            return np.eye(3, dtype=np.float32)
        # 反対向き: a に直交する任意軸で 180°
        axis = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0.0, 1.0, 0.0])
        axis = axis / np.linalg.norm(axis)
        return (2.0 * np.outer(axis, axis) - np.eye(3)).astype(np.float32)
    k = v / s
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]], np.float64)
    R = np.eye(3) + s * K + (1.0 - c) * (K @ K)
    return R.astype(np.float32)


@dataclass
class _Joint:
    node: int
    parent: int
    group: int
    local_pos: np.ndarray          # ノードの translation（glTF・親ローカル）
    local_scale: np.ndarray
    local_rot: np.ndarray          # rest のローカル回転 3x3
    bone_axis: np.ndarray          # 子方向の単位ベクトル（ローカル）
    length: float
    cur: np.ndarray = field(default_factory=lambda: np.zeros(3, np.float32))
    prev: np.ndarray = field(default_factory=lambda: np.zeros(3, np.float32))


class SpringBoneSim:
    """VRM 1体ぶんの Spring Bone 状態。フレーム順に `step` を呼ぶ。"""

    LEAF_TAIL = 0.07   # 末端ボーンの仮想的な尾の長さ（UniVRM と同じ 7cm）
    MAX_DT = 1.0 / 30  # 積分の刻みの上限。低 fps ではこの刻みでサブステップを回し fps 非依存にする

    def __init__(self, model: VrmModel, center: str = "none",
                 max_collider_ratio: float = 0.25, vectorized: bool = True):
        """max_collider_ratio: 身長に対するこの比を超える半径の球コライダは無視する。

        VRoid の VRMSkirtTool は半径 1.0m の球を体の前後 0.9m に置く（スカートの
        可動域を囲う意図らしいが、UniVRM の「外へ押し出す」衝突ではスカート全体が
        球の内側に入っていて即座に体の反対側へ弾き出される）。体の部位を表す
        コライダは身長の 1/4 を超えない。無視した数は `ignored_colliders` に残す。
        """
        self.m = model
        self.center = center
        sp = model.spring or {}
        self.groups: list[dict] = sp.get("groups", [])
        self.collider_groups: list[dict] = sp.get("collider_groups", [])
        self.ignored_colliders = 0
        rest_h = _rest_height(model)
        max_r = float(max_collider_ratio) * rest_h
        self.parent: dict[int, int] = {}
        for i, n in enumerate(model.nodes):
            for c in n.get("children", []):
                self.parent[c] = i
        rest = model.rest_globals()
        self.joints: list[_Joint] = []
        seen: set[int] = set()
        for gi, g in enumerate(self.groups):
            for root in g.get("bones", []):
                stack = [root]
                while stack:
                    i = stack.pop()
                    if i in seen or i not in self.parent:
                        continue
                    seen.add(i)
                    self.joints.append(self._make_joint(i, gi, rest))
                    stack.extend(model.nodes[i].get("children", []))
        # 親→子の順（topo）に並べ直す。同じチェーン内で親が先に更新されている必要がある
        order = {n: k for k, n in enumerate(_topo(model.nodes))}
        self.joints.sort(key=lambda j: order[j.node])
        self.by_node = {j.node: j for j in self.joints}
        # 球コライダを一意に平坦化し、各グループはその index 列を持つ
        # （毎ステップのワールド位置計算を 1 回にまとめるため）
        sph_nodes, sph_off, sph_rad = [], [], []
        cg_index: list[list[int]] = []
        for cg in self.collider_groups:
            ids = []
            for off, rad in cg["colliders"]:
                if float(rad) > max_r:
                    self.ignored_colliders += 1
                    continue
                ids.append(len(sph_nodes))
                sph_nodes.append(int(cg["node"]))
                sph_off.append(np.append(np.asarray(off, np.float32), 1.0))
                sph_rad.append(float(rad))
            cg_index.append(ids)
        self.sph_nodes = np.array(sph_nodes, np.int64)
        self.sph_off = (np.stack(sph_off) if sph_off else np.zeros((0, 4), np.float32))
        self.sph_rad = np.array(sph_rad, np.float32)
        self.group_sph: list[np.ndarray] = []
        for g in self.groups:
            ids = [i for cg in g.get("collider_groups", [])
                   if 0 <= cg < len(cg_index) for i in cg_index[cg]]
            self.group_sph.append(np.array(sorted(set(ids)), np.int64))
        self.hips = model.humanoid.get("hips")
        self.initialized = False
        self.vectorized = vectorized
        if vectorized:
            self._build_levels()

    @property
    def n_joints(self) -> int:
        return len(self.joints)

    def _make_joint(self, i: int, gi: int, rest: np.ndarray) -> _Joint:
        n = self.m.nodes[i]
        pos = np.array(n.get("translation", [0, 0, 0]), np.float32)
        scl = np.array(n.get("scale", [1, 1, 1]), np.float32)
        rot = _quat_to_mat(np.array(n.get("rotation", [0, 0, 0, 1]), np.float32))
        ch = n.get("children", [])
        if ch:
            cp = np.array(self.m.nodes[ch[0]].get("translation", [0, 0, 0]), np.float32) * scl
        else:
            # 末端: 親→自分 の方向を延長した仮想の尾（ローカル座標へ戻す）
            p = self.parent[i]
            d = rest[i][:3, 3] - rest[p][:3, 3]
            d = d / max(float(np.linalg.norm(d)), 1e-6) * self.LEAF_TAIL
            cp = rest[i][:3, :3].T @ d
        length = float(np.linalg.norm(cp))
        axis = cp / max(length, 1e-6)
        return _Joint(i, self.parent[i], gi, pos, scl, rot, axis.astype(np.float32), length)

    # ------------------------------------------------------------------
    def reset(self) -> None:
        self.initialized = False

    def _sim_offset(self, globals_: np.ndarray, t_gltf: np.ndarray) -> np.ndarray:
        """シミュレーション空間の原点シフト。center=hips なら腰位置を引く。"""
        if self.center == "hips" and self.hips is not None:
            return -globals_[self.hips][:3, 3].astype(np.float32)
        return t_gltf.astype(np.float32)

    def step(self, globals_: np.ndarray, t_gltf: np.ndarray, dt: float,
             warmup: int = 0) -> np.ndarray:
        """1フレーム進めて、Spring Bone を反映したグローバル行列 (N,4,4) を返す。

        globals_: 人体ポーズ適用後（Spring 未適用）のグローバル行列（glTF 空間）
        t_gltf  : このフレームのアバター平行移動を glTF 空間・未スケール単位に
                  戻したもの（慣性の源）
        warmup  : 初期化時に同じ姿勢で空回しする回数（重力で髪が落ち着くまで）
        """
        G = globals_.copy()
        off = self._sim_offset(globals_, t_gltf)
        # 剛性・重力は dt に比例して効くので、6fps を 1 刻みで積分すると 30fps と別の
        # 動きになる（振幅が 2 割違う、実測）。刻みを MAX_DT 以下に割って同じ物理にする
        k = max(1, int(np.ceil(dt / self.MAX_DT - 1e-6)))
        dts = dt / k
        if not self.initialized:
            self._init_tails(G, off)
            self.initialized = True
            for _ in range(int(warmup) * k):
                G = self._advance(globals_.copy(), off, dts)
        for _ in range(k):
            G = self._advance(globals_.copy() if k > 1 else G, off, dts)
        return G

    def _init_tails(self, G: np.ndarray, off: np.ndarray) -> None:
        if getattr(self, "vectorized", False):
            self.state = None    # rebuilt from the joints' cur / prev on the next _advance
        for j in self.joints:
            head = G[j.node][:3, 3] + off
            rest_rot = G[j.parent][:3, :3] @ j.local_rot
            tail = head + rest_rot @ j.bone_axis * j.length
            j.cur = tail.astype(np.float32)
            j.prev = tail.astype(np.float32)

    # ------------------------------------------------------------------
    # Vectorized _advance: the same update per joint, computed for all joints of one tree level at once (a joint's
    # parent is either not a spring joint or a spring joint of an earlier level, so the order the loop depends on is
    # kept). Float rounding differs from the loop (scripts/eval/check_springbone.py measures both).
    def _build_levels(self) -> None:
        J = self.joints
        n = len(J)
        self.jnode = np.array([j.node for j in J], np.int64)
        self.jparent = np.array([j.parent for j in J], np.int64)
        self.jlen = np.array([j.length for j in J], np.float64)
        self.jlpos4 = np.array([np.append(j.local_pos, 1.0) for j in J], np.float64).reshape(n, 4)
        self.jlpos = np.array([j.local_pos for j in J], np.float32).reshape(n, 3)
        self.jlscale = np.array([j.local_scale for j in J], np.float32).reshape(n, 3)
        self.jlrot = np.array([j.local_rot for j in J], np.float32).reshape(n, 3, 3)
        self.jaxis = np.array([j.bone_axis for j in J], np.float32).reshape(n, 3)
        gr = [self.groups[j.group] for j in J]
        self.jdrag = np.array([g["drag"] for g in gr], np.float64)
        self.jstiff = np.array([g["stiffness"] for g in gr], np.float64)
        self.jgrav = np.array([np.asarray(g["gravity_dir"], np.float64) * g["gravity_power"] for g in gr]).reshape(n, 3)
        self.jhit = np.array([g["hit_radius"] for g in gr], np.float64)
        ms = max([len(self.group_sph[j.group]) for j in J] + [0])
        self.jsph = np.full((n, ms), -1, np.int64)
        for k, j in enumerate(J):
            ids = self.group_sph[j.group]
            self.jsph[k, :len(ids)] = ids
        self.jzero_local = {j.node: self.m.local_matrix(j.node, None) for j in J if j.length < 1e-4}
        lvl = {}
        for j in J:    # topological order: a spring parent already has its level
            pj = self.by_node.get(j.parent)
            lvl[j.node] = lvl[pj.node] + 1 if pj is not None else 0
        depth = np.array([lvl[j.node] for j in J])
        self.levels = [np.nonzero(depth == d)[0] for d in range(depth.max() + 1)] if n else []
        # the non-spring descendants of spring nodes, in _topo order, with their rest local matrices
        spring = set(self.by_node)
        dirty = set(spring)
        self.follow = []
        for i in _topo(self.m.nodes):
            p = self.parent.get(i)
            if p is None or i in spring or p not in dirty:
                continue
            self.follow.append((i, p, self.m.local_matrix(i, None)))
            dirty.add(i)
        self.state = None

    def _advance_vec(self, G: np.ndarray, off: np.ndarray, dt: float) -> np.ndarray:
        if self.state is None:
            self.state = (np.array([j.cur for j in self.joints], np.float32).reshape(-1, 3),
                          np.array([j.prev for j in self.joints], np.float32).reshape(-1, 3))
        cur, prev = self.state
        if len(self.sph_nodes):
            sph_pos = (np.einsum("nij,nj->ni", G[self.sph_nodes], self.sph_off)[:, :3]
                       + off[None, :]).astype(np.float32)
        else:
            sph_pos = np.zeros((0, 3), np.float32)
        for idx in self.levels:
            zero = self.jlen[idx] < 1e-4
            for k in idx[zero]:
                G[self.jnode[k]] = G[self.jparent[k]] @ self.jzero_local[self.jnode[k]]
            idx = idx[~zero]
            if not len(idx):
                continue
            Pm = G[self.jparent[idx]]
            pr = Pm[:, :3, :3]
            sc = np.linalg.norm(pr, axis=1)                                  # column norms
            bad = np.any(np.abs(sc - 1.0) > 1e-3, axis=1)
            if bad.any():
                pr = pr.copy()
                pr[bad] = pr[bad] / np.maximum(sc[bad], 1e-6)[:, None, :]
            head = np.einsum("nij,nj->ni", Pm, self.jlpos4[idx])[:, :3] + off
            rest_rot = pr @ self.jlrot[idx]
            rest_dir = np.einsum("nij,nj->ni", rest_rot, self.jaxis[idx])
            c, pv = cur[idx], prev[idx]
            nxt = (c + (c - pv) * (1.0 - self.jdrag[idx])[:, None]
                   + rest_dir * (self.jstiff[idx] * dt)[:, None] + self.jgrav[idx] * dt)
            L = self.jlen[idx][:, None]
            d = nxt - head
            nxt = head + d / np.maximum(np.linalg.norm(d, axis=1), 1e-6)[:, None] * L
            sid = self.jsph[idx]
            if sid.shape[1]:
                valid = sid >= 0
                P = sph_pos[np.maximum(sid, 0)]                              # (n, S, 3)
                r = self.sph_rad[np.maximum(sid, 0)] + self.jhit[idx][:, None]
                hit0 = valid & (np.linalg.norm(nxt[:, None, :] - P, axis=2) < r)
                for k in range(sid.shape[1]):     # as the loop: the first test's hits, pushed in sphere order
                    h = hit0[:, k]
                    if not h.any():
                        continue
                    dv = nxt[h] - P[h, k]
                    nrm = dv / np.maximum(np.linalg.norm(dv, axis=1), 1e-6)[:, None]
                    d = P[h, k] + nrm * r[h, k][:, None] - head[h]
                    nxt[h] = head[h] + d / np.maximum(np.linalg.norm(d, axis=1), 1e-6)[:, None] * L[h]
            prev[idx] = c
            cur[idx] = nxt.astype(np.float32)
            nd = nxt - head
            nd = (nd / np.maximum(np.linalg.norm(nd, axis=1), 1e-6)[:, None]).astype(np.float32)
            R = _from_to_batch(rest_dir.astype(np.float32), nd)
            local_rot = np.swapaxes(pr, 1, 2) @ (R @ rest_rot)
            Lm = np.tile(np.eye(4, dtype=np.float32), (len(idx), 1, 1))
            Lm[:, :3, :3] = local_rot * self.jlscale[idx][:, None, :]
            Lm[:, :3, 3] = self.jlpos[idx]
            G[self.jnode[idx]] = Pm @ Lm
        for i, p, Lr in self.follow:
            G[i] = G[p] @ Lr
        return G

    def _advance(self, G: np.ndarray, off: np.ndarray, dt: float) -> np.ndarray:
        if self.vectorized:
            return self._advance_vec(G, off, dt)
        # コライダのワールド位置（人体ボーン上なので Spring の影響を受けない）
        if len(self.sph_nodes):
            sph_pos = (np.einsum("nij,nj->ni", G[self.sph_nodes], self.sph_off)[:, :3]
                       + off[None, :]).astype(np.float32)
        else:
            sph_pos = np.zeros((0, 3), np.float32)

        dirty: set[int] = set()
        for j in self.joints:
            g = self.groups[j.group]
            Pm = G[j.parent]
            if j.length < 1e-4:
                # 長さ 0 の骨（VRoid のスカート根元 Skirt0 等）は方向が定義できない。
                # UniVRM でも normalized が零ベクトルになり回転は恒等＝rest のまま
                G[j.node] = Pm @ self.m.local_matrix(j.node, None)
                dirty.add(j.node)
                continue
            parent_rot = Pm[:3, :3]
            # 親にスケールが入っていても回転だけ取り出す（VRoid は 1）
            sc = np.linalg.norm(parent_rot, axis=0)
            if np.any(np.abs(sc - 1.0) > 1e-3):
                parent_rot = parent_rot / np.maximum(sc, 1e-6)[None, :]
            head = (Pm @ np.append(j.local_pos, 1.0))[:3] + off
            rest_rot = parent_rot @ j.local_rot
            rest_dir = rest_rot @ j.bone_axis

            nxt = (j.cur + (j.cur - j.prev) * (1.0 - g["drag"])
                   + rest_dir * (g["stiffness"] * dt)
                   + g["gravity_dir"] * (g["gravity_power"] * dt))
            d = nxt - head
            nxt = head + d / max(float(np.linalg.norm(d)), 1e-6) * j.length

            ids = self.group_sph[j.group]
            if len(ids):
                P, R = sph_pos[ids], self.sph_rad[ids]
                r = R + g["hit_radius"]
                dv = nxt[None, :] - P
                dist = np.linalg.norm(dv, axis=1)
                hit = np.nonzero(dist < r)[0]
                for k in hit:
                    nrm = dv[k] / max(float(dist[k]), 1e-6)
                    pushed = P[k] + nrm * r[k]
                    d = pushed - head
                    nxt = head + d / max(float(np.linalg.norm(d)), 1e-6) * j.length
                    dv = nxt[None, :] - P
                    dist = np.linalg.norm(dv, axis=1)

            j.prev, j.cur = j.cur, nxt.astype(np.float32)
            new_dir = (nxt - head)
            new_dir = new_dir / max(float(np.linalg.norm(new_dir)), 1e-6)
            world_rot = _from_to(rest_dir.astype(np.float32), new_dir.astype(np.float32)) @ rest_rot
            local_rot = parent_rot.T @ world_rot
            L = np.eye(4, dtype=np.float32)
            L[:3, :3] = local_rot * j.local_scale[None, :]
            L[:3, 3] = j.local_pos
            G[j.node] = Pm @ L
            dirty.add(j.node)

        # Spring ノードの子で Spring でないもの（メッシュ端点等）は親に追従させる
        if dirty:
            for i in _topo(self.m.nodes):
                p = self.parent.get(i)
                if p is None or i in self.by_node or p not in dirty:
                    continue
                G[i] = G[p] @ self.m.local_matrix(i, None)
                dirty.add(i)
        return G


def _from_to_batch(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """_from_to for (n, 3) unit vectors a, b -> (n, 3, 3)."""
    n = len(a)
    a64, b64 = a.astype(np.float64), b.astype(np.float64)
    v = np.cross(a64, b64)
    s = np.linalg.norm(v, axis=1)
    c = np.einsum("ni,ni->n", a64, b64)
    out = np.tile(np.eye(3), (n, 1, 1))
    gen = s >= 1e-8
    if gen.any():
        k = v[gen] / s[gen][:, None]
        K = np.zeros((len(k), 3, 3))
        K[:, 0, 1], K[:, 0, 2], K[:, 1, 2] = -k[:, 2], k[:, 1], -k[:, 0]
        K[:, 1, 0], K[:, 2, 0], K[:, 2, 1] = k[:, 2], -k[:, 1], k[:, 0]
        out[gen] = np.eye(3) + s[gen][:, None, None] * K + (1.0 - c[gen])[:, None, None] * (K @ K)
    for i in np.nonzero(~gen & (c <= 0))[0]:   # opposite directions: the loop's special case
        out[i] = _from_to(a[i], b[i])
    return out.astype(np.float32)


def _rest_height(model: VrmModel) -> float:
    """rest 姿勢のノード位置の高さ幅（メッシュを焼かずに済む近似）。"""
    g = model.rest_globals()
    ys = g[:, 1, 3]
    return float(ys.max() - ys.min()) if len(ys) else 1.5


def _topo(nodes: list[dict]) -> list[int]:
    child = {c for n in nodes for c in n.get("children", [])}
    order, stack = [], [i for i in range(len(nodes)) if i not in child]
    while stack:
        i = stack.pop()
        order.append(i)
        stack.extend(nodes[i].get("children", []))
    return order


def parse_spring(gltf: dict) -> dict | None:
    """VRM 0.x `secondaryAnimation` / VRM 1.0 `VRMC_springBone` を共通の辞書へ。

    groups: [{stiffness, gravity_power, gravity_dir(3,), drag, hit_radius,
              bones:[root node...], collider_groups:[idx...]}]
    collider_groups: [{node, colliders:[(offset(3,), radius), ...]}]
    """
    ext = gltf.get("extensions", {})
    if "VRM" in ext and "secondaryAnimation" in ext["VRM"]:
        sa = ext["VRM"]["secondaryAnimation"]
        cgs = []
        for cg in sa.get("colliderGroups", []):
            cols = []
            for c in cg.get("colliders", []):
                # VRM 0.x の offset / gravityDir は Unity 座標のまま格納されている。
                # UniGLTF の Unity↔glTF 変換は Z 反転（ReverseZ）なので、glTF 空間へ
                # 持ち込むには z の符号を反転する（three-vrm の v0 importer と同じ）
                o = c.get("offset", {})
                cols.append(([o.get("x", 0.0), o.get("y", 0.0), -o.get("z", 0.0)],
                             float(c.get("radius", 0.0))))
            cgs.append({"node": int(cg.get("node", -1)), "colliders": cols})
        groups = []
        for bg in sa.get("boneGroups", []):
            gd = bg.get("gravityDir", {"x": 0, "y": -1, "z": 0})
            groups.append({
                "stiffness": float(bg.get("stiffiness", bg.get("stiffness", 1.0))),
                "gravity_power": float(bg.get("gravityPower", 0.0)),
                "gravity_dir": np.array([gd.get("x", 0), gd.get("y", -1), -gd.get("z", 0)], np.float32),
                "drag": float(bg.get("dragForce", 0.4)),
                "hit_radius": float(bg.get("hitRadius", 0.02)),
                "bones": [int(b) for b in bg.get("bones", [])],
                "collider_groups": [int(c) for c in bg.get("colliderGroups", [])],
            })
        return {"groups": groups, "collider_groups": cgs}
    if "VRMC_springBone" in ext:
        sb = ext["VRMC_springBone"]
        cols = sb.get("colliders", [])
        cgs = []
        for cg in sb.get("colliderGroups", []):
            per_node: dict[int, list] = {}
            for ci in cg.get("colliders", []):
                c = cols[ci]
                sh = c.get("shape", {}).get("sphere")
                if sh is None:
                    continue   # capsule は未対応（球で近似しない）
                per_node.setdefault(int(c["node"]), []).append(
                    (sh.get("offset", [0, 0, 0]), float(sh.get("radius", 0.0))))
            for node, cl in per_node.items():
                cgs.append({"node": node, "colliders": cl})
        groups = []
        for sp in sb.get("springs", []):
            js = sp.get("joints", [])
            if not js:
                continue
            j0 = js[0]
            groups.append({
                "stiffness": float(j0.get("stiffness", 1.0)),
                "gravity_power": float(j0.get("gravityPower", 0.0)),
                "gravity_dir": np.array(j0.get("gravityDir", [0, -1, 0]), np.float32),
                "drag": float(j0.get("dragForce", 0.4)),
                "hit_radius": float(j0.get("hitRadius", 0.0)),
                "bones": [int(js[0]["node"])],
                "collider_groups": list(range(len(cgs))) if sp.get("colliderGroups") else [],
            })
        return {"groups": groups, "collider_groups": cgs}
    return None
