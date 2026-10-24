"""Facial expressions for the avatars: MediaPipe Face Landmarker's landmark and blendshape models (Apache-2.0,
<ckpt_root>/mediapipe/face_landmarker.task) on face crops placed by the HMR skeleton, -> VRM expression weights.

SAM 3D Body predicts no expression (its face components are zeroed), so the avatars' faces stayed neutral.

  crop        no face detector: MediaPipe's BlazeFace (short range) missed most faces of the test clips (three-quarter
              views, ~100 px faces), while the skeleton already says where every head is. The first crop is centred
              between the projected nose and eyes (keypoints_3d 0-2 + cam_t), half size 0.125 m at the head's depth
              (fitted to the crops the landmark model converged to), rolled by the projected eye line; one more crop
              is then made from the landmarks as MediaPipe tracks a face (landmark box x 1.5, rolled by the eye
              corners 33 -> 263), and the landmarks of that one are used. Skipped: heads facing away (nose farther
              than the ears), faces under 12 px, heads out of the frame
  blendshapes the 52 ARKit coefficients from 146 of the 478 landmarks (the subset of MediaPipe's
              face_blendshapes_graph; the model is scale invariant: the same output for crop or frame pixels)
  per track   presence (the landmark model's face score) -> confidence w = clip((p - 0.2) / 0.5, 0, 1); an
              unreliable or unread face (profile, looking down, low resolution) takes the expression of its reliable
              neighbours (confidence-weighted smoothing), and only a long stretch without one eases to the neutral
              face over ~0.5 s (expression_tracks): no sudden switch to neutral; the eyes' coefficients relative
              to the track's median (low-resolution eyes rarely read as fully open: their blink scores sit at 0.2-0.4)
  to VRM      a perfect-sync model (ARKit names among its expressions, e.g. RAYNOS-chan) gets the 52 coefficients
              as they are; others the presets: blink_l/r, a (jawOpen), i/u/e/o from the mouth shape, joy (smile)

~4 ms per face on the CPU (two landmark calls and one blendshape call, XNNPACK): avatar_app runs it in a thread
beside the removal and fusion v2.
"""
import os
import threading
import zipfile

import cv2
import numpy as np

ARKIT = ["_neutral", "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight",
         "cheekPuff", "cheekSquintLeft", "cheekSquintRight", "eyeBlinkLeft", "eyeBlinkRight", "eyeLookDownLeft",
         "eyeLookDownRight", "eyeLookInLeft", "eyeLookInRight", "eyeLookOutLeft", "eyeLookOutRight", "eyeLookUpLeft",
         "eyeLookUpRight", "eyeSquintLeft", "eyeSquintRight", "eyeWideLeft", "eyeWideRight", "jawForward", "jawLeft",
         "jawOpen", "jawRight", "mouthClose", "mouthDimpleLeft", "mouthDimpleRight", "mouthFrownLeft",
         "mouthFrownRight", "mouthFunnel", "mouthLeft", "mouthLowerDownLeft", "mouthLowerDownRight", "mouthPressLeft",
         "mouthPressRight", "mouthPucker", "mouthRight", "mouthRollLower", "mouthRollUpper", "mouthShrugLower",
         "mouthShrugUpper", "mouthSmileLeft", "mouthSmileRight", "mouthStretchLeft", "mouthStretchRight",
         "mouthUpperUpLeft", "mouthUpperUpRight", "noseSneerLeft", "noseSneerRight"]
IDX = {n: k for k, n in enumerate(ARKIT)}
EYES = [k for k, n in enumerate(ARKIT) if n.startswith("eye")]
# landmark subset the blendshape model reads (mediapipe/tasks/cc/vision/face_landmarker/face_blendshapes_graph.cc)
SUBSET = np.array([0, 1, 4, 5, 6, 7, 8, 10, 13, 14, 17, 21, 33, 37, 39, 40, 46, 52, 53, 54, 55, 58, 61, 63, 65, 66, 67,
                   70, 78, 80, 81, 82, 84, 87, 88, 91, 93, 95, 103, 105, 107, 109, 127, 132, 133, 136, 144, 145, 146,
                   148, 149, 150, 152, 153, 154, 155, 157, 158, 159, 160, 161, 162, 163, 168, 172, 173, 176, 178, 181,
                   185, 191, 195, 197, 234, 246, 249, 251, 263, 267, 269, 270, 276, 282, 283, 284, 285, 288, 291, 293,
                   295, 296, 297, 300, 308, 310, 311, 312, 314, 317, 318, 321, 323, 324, 332, 334, 336, 338, 356, 361,
                   362, 365, 373, 374, 375, 377, 378, 379, 380, 381, 382, 384, 385, 386, 387, 388, 389, 390, 397, 398,
                   400, 402, 405, 409, 415, 454, 466, 468, 469, 470, 471, 472, 473, 474, 475, 476, 477])
HALF_M = 0.125      # first crop: half size [m] at the head's depth
CENTER = -0.4       # first crop: centre = nose + CENTER * (nose - eye midpoint)


def _ckpt():
    root = os.environ.get("BODY4D_CKPT_ROOT")
    if not root:
        from omegaconf import OmegaConf
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        root = OmegaConf.load(os.path.join(repo, "configs", "body4d.yaml")).paths.ckpt_root
    return os.path.join(root, "mediapipe", "face_landmarker.task")


class FaceModel:
    """The landmark and blendshape TFLite models of face_landmarker.task (one instance per thread)."""

    def __init__(self, path=None, threads=2):
        from ai_edge_litert.interpreter import Interpreter
        with zipfile.ZipFile(path or _ckpt()) as z:
            lm, bs = z.read("face_landmarks_detector.tflite"), z.read("face_blendshapes.tflite")
        self.lm = Interpreter(model_content=lm, num_threads=threads)
        self.bs = Interpreter(model_content=bs, num_threads=1)
        self.lm.allocate_tensors()
        self.bs.allocate_tensors()
        self.li = self.lm.get_input_details()[0]["index"]
        self.lo = [d["index"] for d in self.lm.get_output_details()[:2]]   # landmarks (1434), face score (logit)
        self.bi = self.bs.get_input_details()[0]["index"]
        self.bo = self.bs.get_output_details()[0]["index"]

    def landmarks(self, crop_bgr):
        """256x256 BGR crop -> (478, 3) landmarks in crop pixels, face presence 0-1."""
        self.lm.set_tensor(self.li, (cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255)[None])
        self.lm.invoke()
        L = self.lm.get_tensor(self.lo[0]).reshape(478, 3).copy()
        return L, float(1 / (1 + np.exp(-float(self.lm.get_tensor(self.lo[1]).ravel()[0]))))

    def blendshapes(self, pts):
        self.bs.set_tensor(self.bi, pts[SUBSET, :2][None].astype(np.float32))
        self.bs.invoke()
        return self.bs.get_tensor(self.bo).copy()

    def face(self, img, head, f, cx, cy, refine=1):
        """img: BGR frame; head: (5, 3) camera-space nose, eyes, ears (MHR70 0-4); f, cx, cy: intrinsics of img.
        -> (52 coefficients, presence) or None (skipped, module docstring)."""
        if np.any(head[:, 2] <= 0.05) or head[0, 2] > head[3:5, 2].mean():   # behind the camera / facing away
            return None
        u = f * head[:, 0] / head[:, 2] + cx
        v = f * head[:, 1] / head[:, 2] + cy
        nose, le, re = np.array([u[0], v[0]]), np.array([u[1], v[1]]), np.array([u[2], v[2]])
        c = nose + CENTER * (nose - (le + re) / 2)
        half = f * HALF_M / head[:3, 2].mean()
        H, W = img.shape[:2]
        if half < 8 or not (-half < c[0] < W + half and -half < c[1] < H + half):
            return None
        d = le - re   # the person's left eye is on the image right of a frontal face
        ang = float(np.degrees(np.arctan2(d[1], d[0]))) if np.hypot(*d) > 0.15 * half else 0.0
        for k in range(refine + 1):
            M = cv2.getRotationMatrix2D((float(c[0]), float(c[1])), ang, 128 / half)
            M[:, 2] += 128 - c
            L, pres = self.landmarks(cv2.warpAffine(img, M, (256, 256), flags=cv2.INTER_LINEAR))
            if k == refine:
                break
            Mi = cv2.invertAffineTransform(M)
            P = L[:, :2] @ Mi[:, :2].T + Mi[:, 2]
            e = P[263] - P[33]
            ang = float(np.degrees(np.arctan2(e[1], e[0])))
            R = cv2.getRotationMatrix2D((0, 0), ang, 1.0)[:, :2]
            Q = P @ R.T
            lo, hi = Q.min(0), Q.max(0)
            c = ((lo + hi) / 2) @ np.linalg.inv(R).T
            half = 0.75 * float((hi - lo).max())
        return self.blendshapes(L), pres


def track_faces(frames, heads, f, cx, cy, log=print):
    """frames[i]: BGR frame i; heads: {(pid, i): (5, 3) camera-space head points}; f, cx, cy: the frames' intrinsics.
    -> {(pid, i): (52 raw coefficients, presence)} (skipped faces absent)."""
    import time
    t0 = time.perf_counter()
    m = FaceModel()
    out = {}
    by_frame = {}
    for (pid, i), h in heads.items():
        by_frame.setdefault(i, []).append((pid, h))
    for i in sorted(by_frame):
        img = np.asarray(frames[i])
        for pid, h in by_frame[i]:
            r = m.face(img, h, f, cx, cy)
            if r is not None:
                out[(pid, i)] = r
    good = sum(p > 0.5 for _, p in out.values())
    log(f"[face] {len(heads)} heads, {len(out)} faces read, {good} with presence > 0.5 "
        f"({time.perf_counter() - t0:.1f} s)", flush=True)
    return out


def expression_tracks(raw, pids, n, sigma=1.0, p0=0.2, p1=0.7, tracked=None, hold=15.0, fade=0.3):
    """raw: track_faces' output -> {(pid, i): 52 coefficients} for every tracked frame of each person.
    tracked: the (pid, i) keys where the person is tracked (default: raw's keys); frames with no face read count
    with confidence 0. Per run of tracked frames, with w = clip((presence - p0) / (p1 - p0)):
      fine    G_sigma(w C) / G_sigma(w)  (confidence-weighted smoothing: an unreliable frame takes its neighbours'
              expression instead of dropping to the neutral face)
      coarse  the same with sigma `hold` frames: fills the gaps a few frames wide from both sides
      value   fine where G_sigma(w) >= 0.5, blended into coarse below; times the envelope
              min(1, G_hold(w) / fade), so only a long stretch with no reliable face eases to neutral, over ~hold
              frames, instead of switching at once
    The eyes' coefficients are taken relative to the track's median over reliable frames (module docstring)."""
    from .smooth import _gauss, _runs
    keys = set(raw) if tracked is None else set(tracked)
    out = {}
    for pid in pids:
        t = sorted(i for p, i in keys if p == pid and i < n)
        if not t:
            continue
        C = np.zeros((len(t), len(ARKIT)))
        w = np.zeros(len(t))
        for k, i in enumerate(t):
            if (pid, i) in raw:
                C[k] = raw[(pid, i)][0]
                w[k] = np.clip((raw[(pid, i)][1] - p0) / (p1 - p0), 0, 1)
        good = w > 0.5
        if good.sum() >= 5:   # the eyes' resting values of this track
            base = np.median(C[good][:, EYES], 0)
            C[:, EYES] = np.clip((C[:, EYES] - base) / np.maximum(1 - base, 0.2), 0, 1)
        pos = {fr: k for k, fr in enumerate(t)}
        for run in _runs(t):
            ks = np.array([pos[fr] for fr in run])
            wr, Cr = w[ks], C[ks] * w[ks][:, None]
            ws, wl = _gauss(wr, sigma), _gauss(wr, hold)
            fine = _gauss(Cr, sigma) / np.maximum(ws, 1e-6)[:, None]
            coarse = _gauss(Cr, hold) / np.maximum(wl, 1e-6)[:, None]
            a = np.clip(ws / 0.5, 0, 1)[:, None]
            val = (a * fine + (1 - a) * coarse) * np.clip(wl / fade, 0, 1)[:, None]
            for j, fr in enumerate(run):
                out[(pid, fr)] = np.clip(val[j], 0, 1).astype(np.float32)
    return out


def _find(names, *cands):
    low = {k.lower(): k for k in names}
    for c in cands:
        if c.lower() in low:
            return low[c.lower()]
    return None


def vrm_mapping(blendshapes):
    """VRM expression names -> a function 52 coefficients -> {expression name: weight}."""
    names = list(blendshapes)
    arkit = {n: _find(names, n) for n in ARKIT[1:]}
    if sum(v is not None for v in arkit.values()) >= 40:   # perfect sync
        pairs = [(IDX[n], e) for n, e in arkit.items() if e is not None]

        def perfect(c):
            return {e: float(c[k]) for k, e in pairs if c[k] > 0.01}
        return perfect, "perfect_sync"
    ex = {k: _find(names, *v) for k, v in {
        "blink_l": ("blink_l", "blinkLeft"), "blink_r": ("blink_r", "blinkRight"), "blink": ("blink",),
        "a": ("a", "aa"), "i": ("i", "ih"), "u": ("u", "ou"), "e": ("e", "ee"), "o": ("o", "oh"),
        "joy": ("joy", "happy"), "sorrow": ("sorrow", "sad"), "angry": ("angry",), "surprised": ("surprised",)}.items()}

    def preset(c):
        g = lambda n: float(c[IDX[n]])
        w = {}
        bl, br = g("eyeBlinkLeft"), g("eyeBlinkRight")
        if ex["blink_l"] and ex["blink_r"]:
            w[ex["blink_l"]], w[ex["blink_r"]] = bl, br
        elif ex["blink"]:
            w[ex["blink"]] = min(bl, br)
        jaw = g("jawOpen")
        smile = (g("mouthSmileLeft") + g("mouthSmileRight")) / 2
        funnel, pucker = g("mouthFunnel"), g("mouthPucker")
        stretch = (g("mouthStretchLeft") + g("mouthStretchRight")) / 2
        vow = {"a": jaw * (1 - funnel), "o": jaw * funnel, "u": pucker * (1 - jaw),
               "i": stretch * (1 - jaw), "e": min(stretch, jaw)}
        for k, val in vow.items():
            if ex[k]:
                w[ex[k]] = float(np.clip(val, 0, 1))
        if ex["joy"]:
            w[ex["joy"]] = float(np.clip(smile, 0, 1))
        return {k: v for k, v in w.items() if v > 0.01}
    return preset, "preset"


class FaceThread:
    """track_faces in a thread: start() at once, result() when the expressions are needed."""

    def __init__(self, frames, heads, f, cx, cy, pids, n, sigma=1.0):
        self.args = (frames, heads, f, cx, cy)
        self.pids, self.n, self.sigma = pids, n, sigma
        self.res = {}
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        import time
        t0 = time.perf_counter()
        try:
            raw = track_faces(*self.args)
            self.res["raw"] = raw
            self.res["expr"] = expression_tracks(raw, self.pids, self.n, self.sigma, tracked=self.args[1].keys())
        except BaseException as e:   # re-raised by result() on the caller's thread
            self.res["err"] = e
        self.res["s"] = time.perf_counter() - t0

    def start(self):
        self.t.start()
        return self

    def result(self):
        self.t.join()
        if "err" in self.res:
            raise self.res["err"]
        return self.res
