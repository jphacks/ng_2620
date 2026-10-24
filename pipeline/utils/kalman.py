import numpy as np
import torch


def ema_smooth_global_rot_per_obj_id_adaptive(
    mhr_dict,
    num_frames,
    frame_obj_ids,
    keys_to_smooth=None,   # kept for compatibility, not used
    key_name="global_rot",
    vis_flags=None,        # dict[obj_id] -> List[0/1], len = num_frames
    motion_med_th=0.02,    # static segment threshold (median diff)
    motion_max_th=0.08,    # static segment threshold (max diff)
    ema_alpha=0.10,        # strong EMA for static segments
    empty_thresh=1e-6,
):
    """
    Segment-wise, occlusion-aware smoothing for global_rot, per obj_id.

    vis_flags: dict[int -> List[int]], obj_id starts from 1.
        vis_flags[obj_id][t] = 1 -> this obj_id is visible (non-occluded) at frame t
        vis_flags[obj_id][t] = 0 -> this obj_id is occluded / unreliable at frame t

    For each obj_id (1..num_humans), with slot = obj_id - 1:
      - present_mask[t] = (obj_id in frame_obj_ids[t])
      - visible_mask[t] = present_mask[t] AND vis_flags[obj_id][t] == 1
      - occ_mask[t]     = present_mask[t] AND vis_flags[obj_id][t] == 0

    Behavior per obj_id:
      1) On visible_mask==True frames, split into contiguous "visible segments".
         For each visible segment:
             - compute frame-to-frame motion
             - if segment motion is LOW (median < motion_med_th and max < motion_max_th):
                   -> STATIC segment: strong EMA inside this segment only
             - else:
                   -> DYNAMIC segment: leave raw rotation
      2) On occ_mask==True frames, split into contiguous "occlusion segments".
         For each [s,e]:
             - compute prev_rot as mean of up to 5 nearest visible frames before s
             - compute next_rot as mean of up to 5 nearest visible frames after e
             - if both exist: linear interpolation between prev_rot and next_rot
             - if only one side: extend that side's mean rotation
             - if none: do nothing
      3) Additionally, if a human is "always highly dynamic" on visible frames
         (median or max motion above larger thresholds), we SKIP static EMA
         but still do occlusion interpolation (since vis_flags already mark 0/1).
    """
    if key_name not in mhr_dict or vis_flags is None:
        return mhr_dict

    rot = mhr_dict[key_name]  # (B, 3)
    device = rot.device
    B, D = rot.shape
    if D != 3:
        return mhr_dict

    assert B % num_frames == 0, "B must be divisible by num_frames"
    num_humans = B // num_frames

    rot_np = rot.detach().cpu().numpy()  # (B, 3)
    frames_all = np.arange(num_frames, dtype=int)

    # global thresholds for "always moving" humans
    motion_med_th_large = motion_med_th * 3.0
    motion_max_th_large = motion_max_th * 3.0

    support_k = 5  # number of nearest visible frames for local averaging

    for obj_id in range(1, num_humans + 1):
        slot = obj_id - 1  # 0-based slot index

        # frames where this obj_id actually appears
        present_mask = np.array(
            [obj_id in fids for fids in frame_obj_ids],
            dtype=bool
        )  # (T,)

        if present_mask.sum() <= 1:
            continue

        # visibility list for this obj_id; if missing, treat as always visible
        vis_list = vis_flags.get(obj_id, None)
        if vis_list is None:
            vis_arr = np.ones(num_frames, dtype=int)
        else:
            vis_arr = np.asarray(vis_list, dtype=int)
            assert len(vis_arr) == num_frames, "vis_flags[obj_id] length must equal num_frames"

        visible_mask = (present_mask & (vis_arr == 1))
        occ_mask     = (present_mask & (vis_arr == 0))

        frames_vis = frames_all[visible_mask]
        frames_occ = frames_all[occ_mask]

        if len(frames_vis) <= 1:
            # not enough visible frames to judge or interpolate
            continue

        idx_vis = frames_vis * num_humans + slot
        track_vis = rot_np[idx_vis, :]  # (N_vis, 3)

        if np.linalg.norm(track_vis) < empty_thresh:
            continue

        # ----- 1) decide if this human is "always moving" on visible frames -----
        diffs_all = np.linalg.norm(track_vis[1:] - track_vis[:-1], axis=1)  # (N_vis-1,)
        motion_med_all = float(np.median(diffs_all))
        motion_max_all = float(np.max(diffs_all))

        always_moving = (
            motion_med_all >= motion_med_th_large
            or motion_max_all >= motion_max_th_large
        )

        # ----- 2) static vs dynamic visible segments (only if not always moving) -----
        if not always_moving:
            t = 0
            while t < num_frames:
                # find a visible segment where this obj_id is present and visible
                if not visible_mask[t]:
                    t += 1
                    continue
                s = t
                while (
                    t + 1 < num_frames
                    and visible_mask[t + 1]
                ):
                    t += 1
                e = t  # [s,e] is a contiguous visible segment

                seg_frames = frames_all[s:e + 1]
                seg_idx = seg_frames * num_humans + slot
                seg_track = rot_np[seg_idx, :]  # (L, 3)
                L = seg_track.shape[0]

                if L > 1:
                    seg_diffs = np.linalg.norm(seg_track[1:] - seg_track[:-1], axis=1)
                    seg_med = float(np.median(seg_diffs))
                    seg_max = float(np.max(seg_diffs))

                    # low-motion visible segment -> STATIC: strong EMA
                    if (seg_med < motion_med_th) and (seg_max < motion_max_th):
                        smooth = np.zeros_like(seg_track, dtype=np.float32)
                        smooth[0] = seg_track[0]
                        for i in range(1, L):
                            smooth[i] = ema_alpha * seg_track[i] + (1.0 - ema_alpha) * smooth[i - 1]
                        if np.isfinite(smooth).all():
                            rot_np[seg_idx, :] = smooth
                    # else: DYNAMIC visible segment -> keep raw

                t += 1

        # ----- 3) occlusion segments: interpolation / extension with local support -----
        if len(frames_occ) > 0:
            t = 0
            while t < num_frames:
                if not occ_mask[t]:
                    t += 1
                    continue
                s = t
                while t + 1 < num_frames and occ_mask[t + 1]:
                    t += 1
                e = t  # [s,e] is a contiguous occlusion segment

                # all visible frames for this obj_id
                frames_vis_current = frames_all[visible_mask]

                # nearest visible frames before s
                prev_support = frames_vis_current[frames_vis_current < s]
                # nearest visible frames after e
                next_support = frames_vis_current[frames_vis_current > e]

                prev_rot = None
                next_rot = None

                if len(prev_support) > 0:
                    prev_support = prev_support[-support_k:]  # last K before s
                    prev_idx = prev_support * num_humans + slot
                    prev_rot = rot_np[prev_idx].mean(axis=0)
                if len(next_support) > 0:
                    next_support = next_support[:support_k]   # first K after e
                    next_idx = next_support * num_humans + slot
                    next_rot = rot_np[next_idx].mean(axis=0)

                if prev_rot is None and next_rot is None:
                    t += 1
                    continue

                for tt in range(s, e + 1):
                    if not occ_mask[tt]:
                        continue
                    idx = tt * num_humans + slot

                    if prev_rot is not None and next_rot is not None:
                        # interpolate between local mean before and after
                        span_start = prev_support[-1]
                        span_end   = next_support[0]
                        if span_end > span_start:
                            r = float(np.clip(tt - span_start, 0, span_end - span_start)) / float(
                                max(span_end - span_start, 1)
                            )
                        else:
                            r = 0.5
                        rot_np[idx] = (1.0 - r) * prev_rot + r * next_rot
                    elif prev_rot is not None:
                        # only previous support: hold local mean before occlusion
                        rot_np[idx] = prev_rot
                    elif next_rot is not None:
                        # only next support: use local mean after occlusion
                        rot_np[idx] = next_rot

                t += 1

    mhr_dict[key_name] = torch.from_numpy(rot_np).to(device)
    return mhr_dict


def kalman_smooth_constant_velocity_safe(Y, q_pos=1e-4, q_vel=1e-6, r_obs=1e-2):
    """
    Robust constant-velocity Kalman smoothing on (T, D).

    Y: (T, D) numpy array of valid observations for a single obj_id.
       Missing frames are handled outside this function.
    """
    Y = np.asarray(Y, dtype=np.float32)
    T, D = Y.shape
    if T == 0:
        return Y.copy()

    # Remove NaN / inf from input
    Y = np.nan_to_num(Y, nan=0.0, posinf=0.0, neginf=0.0)

    q_pos = float(max(q_pos, 0.0))
    q_vel = float(max(q_vel, 0.0))
    r_obs = float(max(r_obs, 1e-12))

    # Initial state: first observation as position, zero velocity
    x = Y[0].copy()                   # (D,)
    v = np.zeros(D, dtype=np.float32) # (D,)

    Pxx = np.ones(D, dtype=np.float32)
    Pxv = np.zeros(D, dtype=np.float32)
    Pvv = np.ones(D, dtype=np.float32)

    X = np.zeros_like(Y)
    X[0] = x

    eps = 1e-8
    max_val = 1e6

    for t in range(1, T):
        # ---- Prediction ----
        x_pred = x + v
        v_pred = v

        Pxx_pred = Pxx + 2 * Pxv + Pvv + q_pos
        Pxv_pred = Pxv + Pvv
        Pvv_pred = Pvv + q_vel

        Pxx_pred = np.clip(Pxx_pred, -max_val, max_val)
        Pxv_pred = np.clip(Pxv_pred, -max_val, max_val)
        Pvv_pred = np.clip(Pvv_pred, -max_val, max_val)

        # ---- Update ----
        y = Y[t]
        S = Pxx_pred + r_obs
        S = np.where(np.abs(S) < eps, eps, S)

        K_pos = Pxx_pred / S
        K_vel = Pxv_pred / S

        innovation = y - x_pred

        x = x_pred + K_pos * innovation
        v = v_pred + K_vel * innovation

        Pxx = (1.0 - K_pos) * Pxx_pred
        Pxv = (1.0 - K_pos) * Pxv_pred
        Pvv = Pvv_pred - K_vel * Pxv_pred

        # Clamp to avoid NaNs / inf
        x = np.nan_to_num(x, nan=0.0, posinf=max_val, neginf=-max_val)
        v = np.nan_to_num(v, nan=0.0, posinf=max_val, neginf=-max_val)
        Pxx = np.nan_to_num(Pxx, nan=1.0, posinf=max_val, neginf=0.0)
        Pxv = np.nan_to_num(Pxv, nan=0.0, posinf=max_val, neginf=-max_val)
        Pvv = np.nan_to_num(Pvv, nan=1.0, posinf=max_val, neginf=0.0)

        X[t] = x

    X = np.nan_to_num(X, nan=0.0, posinf=max_val, neginf=-max_val)
    return X


def adaptive_strong_smoothing(
    track_valid,
    strong_q_pos=1e-7,
    strong_q_vel=1e-8,
    strong_r_obs=10.0,
    motion_low=0.15,
    motion_high=0.50,
    noise_raw_scale=0.05,
    min_stable_len=2,
):
    """
    Very aggressive adaptive smoothing used by kalman_smooth_mhr_params_per_obj_id_adaptive.

    - Always compute a VERY strong Kalman track 'heavy'.
    - Default behavior is already strongly biased to 'heavy'.
    - If motion pattern looks like stable -> burst -> stable,
      the middle burst segment is forced fully to 'heavy'.
    """
    track_valid = np.asarray(track_valid, dtype=np.float32)
    T, D = track_valid.shape
    if T <= 1:
        return track_valid.copy()

    # 1) Very strong Kalman (heavy)
    heavy = kalman_smooth_constant_velocity_safe(
        track_valid,
        q_pos=strong_q_pos,
        q_vel=strong_q_vel,
        r_obs=strong_r_obs,
    )

    # 2) Motion magnitude on raw track
    diff_raw = np.linalg.norm(track_valid[1:] - track_valid[:-1], axis=1)  # (T-1,)
    motion_raw = np.concatenate(([diff_raw[0]], diff_raw))                 # (T,)

    # 3) Base w_raw from motion_raw (strong bias toward heavy)
    denom = max(motion_high - motion_low, 1e-8)
    w_raw = (motion_raw - motion_low) / denom
    w_raw = np.clip(w_raw, 0.0, 1.0)      # (T,)
    # Make it even closer to 0 for low/medium motion
    w_raw = w_raw ** 2
    # Cap max raw weight to 0.3 -> at least 70% heavy even for large motion
    w_raw = np.minimum(w_raw, 0.3)
    w_raw = w_raw[:, None]                # (T, 1)

    # ---------- Very permissive "stable -> burst -> stable" detection ----------
    low_th = motion_low
    high_th = motion_high

    high_mask = motion_raw > high_th
    if high_mask.any():
        t_start = int(high_mask.argmax())
        t_end = int(len(motion_raw) - 1 - high_mask[::-1].argmax())
        if t_end > t_start:
            prefix = motion_raw[:t_start]
            suffix = motion_raw[t_end+1:]
            mid    = motion_raw[t_start:t_end+1]

            if len(prefix) >= min_stable_len and len(suffix) >= min_stable_len:
                prefix_stable_ratio = (prefix < low_th).mean() if len(prefix) > 0 else 0.0
                suffix_stable_ratio = (suffix < low_th).mean() if len(suffix) > 0 else 0.0
                mid_high_ratio      = (mid    > high_th).mean() if len(mid)    > 0 else 0.0

                # thresholds are relaxed so occlusion-like pattern is easier to trigger
                if prefix_stable_ratio > 0.5 and suffix_stable_ratio > 0.5 and mid_high_ratio > 0.5:
                    # Occlusion-like middle segment: almost fully trust heavy
                    w_raw[t_start:t_end+1, :] *= noise_raw_scale  # e.g. 0.05 → 95% heavy

    # 4) Final blend (already heavily biased to heavy)
    out = w_raw * track_valid + (1.0 - w_raw) * heavy
    return out


def kalman_smooth_mhr_params_per_obj_id_adaptive(
    mhr_dict,
    num_frames,
    frame_obj_ids,
    keys_to_smooth=None,     # e.g. ["body_pose", "hand"]
    kalman_cfg=None,         # kept for compatibility, not used in this EMA version
    vis_flags=None,          # dict[obj_id] -> List[0/1], len = num_frames
    motion_med_th=0.08,
    motion_max_th=0.25,
    ema_alpha=0.18,
    empty_thresh=1e-6,
):
    """
    Segment-wise, occlusion-aware smoothing for high-dim MHR parameters
    (typically body_pose and hand), per obj_id.
    Overall: relatively loose smoothing, mainly suppresses jitter and spikes.
    """
    if keys_to_smooth is None:
        keys_to_smooth = ["body_pose", "hand"]

    if vis_flags is None:
        vis_flags = {}

    any_key = next(iter(mhr_dict.keys()))
    B_total = mhr_dict[any_key].shape[0]
    assert B_total % num_frames == 0, "B must be divisible by num_frames"
    num_humans = B_total // num_frames

    frames_all = np.arange(num_frames, dtype=int)

    # thresholds for "always moving" humans (global gating)
    motion_med_th_large = motion_med_th * 3.0
    motion_max_th_large = motion_max_th * 3.0

    support_k = 5                  # local window size for occlusion interpolation
    diff_per_dim_th = 0.05         # for occlusion: interp vs orig difference
    spike_factor = 2.2             # for visible segments: detect local spikes
    alpha_static = ema_alpha       # static segments EMA (looser)
    alpha_spike = ema_alpha * 0.5  # local spikes EMA

    # boundary diffusion: loose & soft
    boundary_radius = 2            # visible frames on each side
    boundary_blend = 0.45          # blend weight at immediate boundary
    boundary_decay = 0.5           # per-frame decay for boundary smoothing

    new_mhr = {}

    for k, v in mhr_dict.items():
        if k not in keys_to_smooth:
            new_mhr[k] = v
            continue

        param = v  # tensor (B, D)
        device = param.device
        B, D = param.shape
        assert B == B_total, f"Inconsistent B for key {k}"

        param_np = param.detach().cpu().numpy()  # (B, D)

        for obj_id in range(1, num_humans + 1):
            slot = obj_id - 1  # 0-based index within each frame

            # frames where this obj_id actually appears
            present_mask = np.array(
                [obj_id in fids for fids in frame_obj_ids],
                dtype=bool
            )  # (T,)

            if present_mask.sum() <= 1:
                continue

            # visibility list for this obj_id; if missing, treat as always visible
            vis_list = vis_flags.get(obj_id, None)
            if vis_list is None:
                vis_arr = np.ones(num_frames, dtype=int)
            else:
                vis_arr = np.asarray(vis_list, dtype=int)
                assert len(vis_arr) == num_frames, "vis_flags[obj_id] length must equal num_frames"

            visible_mask = (present_mask & (vis_arr == 1))
            occ_mask     = (present_mask & (vis_arr == 0))

            frames_vis = frames_all[visible_mask]
            frames_occ = frames_all[occ_mask]

            if len(frames_vis) <= 1:
                # not enough visible frames to judge or interpolate
                continue

            idx_vis = frames_vis * num_humans + slot
            track_vis = param_np[idx_vis, :]  # (N_vis, D)

            if np.linalg.norm(track_vis) < empty_thresh:
                continue

            # ----- 1) decide if this (key, obj_id) is "always moving" on visible frames -----
            diffs_all = np.linalg.norm(track_vis[1:] - track_vis[:-1], axis=1)  # (N_vis-1,)
            motion_med_all = float(np.median(diffs_all))
            motion_max_all = float(np.max(diffs_all))

            always_moving = (
                motion_med_all >= motion_med_th_large
                or motion_max_all >= motion_max_th_large
            )

            # ----- 2) visible segments (vis=1) -----
            if not always_moving:
                t = 0
                while t < num_frames:
                    # find a visible segment where this obj_id is present and visible
                    if not visible_mask[t]:
                        t += 1
                        continue
                    s = t
                    while t + 1 < num_frames and visible_mask[t + 1]:
                        t += 1
                    e = t  # [s,e] is a contiguous visible segment

                    seg_frames = frames_all[s:e + 1]
                    seg_idx = seg_frames * num_humans + slot
                    seg_track = param_np[seg_idx, :]  # (L, D)
                    L = seg_track.shape[0]

                    if L > 1:
                        seg_diffs = np.linalg.norm(seg_track[1:] - seg_track[:-1], axis=1)
                        seg_med = float(np.median(seg_diffs))
                        seg_max = float(np.max(seg_diffs))

                        if (seg_med < motion_med_th) and (seg_max < motion_max_th):
                            # mostly static segment -> loose EMA
                            smooth = np.zeros_like(seg_track, dtype=np.float32)
                            smooth[0] = seg_track[0]
                            for i in range(1, L):
                                smooth[i] = (
                                    alpha_static * seg_track[i]
                                    + (1.0 - alpha_static) * smooth[i - 1]
                                )
                            if np.isfinite(smooth).all():
                                param_np[seg_idx, :] = smooth
                        else:
                            # dynamic segment -> only suppress clear spikes
                            smooth = seg_track.copy()
                            if L > 2:
                                mean_diff = float(np.mean(seg_diffs))
                                spike_th = max(spike_factor * mean_diff, motion_max_th)
                                for i in range(1, L):
                                    if seg_diffs[i - 1] > spike_th:
                                        smooth[i] = (
                                            alpha_spike * seg_track[i]
                                            + (1.0 - alpha_spike) * smooth[i - 1]
                                        )
                            if np.isfinite(smooth).all():
                                param_np[seg_idx, :] = smooth

                    t += 1

            # ----- 3) occlusion segments (vis=0): local support + adaptive blend + loose boundary diffusion -----
            if len(frames_occ) > 0:
                t = 0
                while t < num_frames:
                    if not occ_mask[t]:
                        t += 1
                        continue
                    s = t
                    while t + 1 < num_frames and occ_mask[t + 1]:
                        t += 1
                    e = t  # [s,e] is a contiguous occlusion segment

                    # all visible frames for this obj_id and this key
                    frames_vis_current = frames_all[visible_mask]

                    # nearest visible frames before s
                    prev_support = frames_vis_current[frames_vis_current < s]
                    # nearest visible frames after e
                    next_support = frames_vis_current[frames_vis_current > e]

                    prev_vec = None
                    next_vec = None

                    if len(prev_support) > 0:
                        prev_support = prev_support[-support_k:]  # last K before s
                        prev_idx = prev_support * num_humans + slot
                        prev_vec = param_np[prev_idx, :].mean(axis=0)
                    if len(next_support) > 0:
                        next_support = next_support[:support_k]   # first K after e
                        next_idx = next_support * num_humans + slot
                        next_vec = param_np[next_idx, :].mean(axis=0)

                    if prev_vec is None and next_vec is None:
                        t += 1
                        continue

                    is_at_start = (s == 0)

                    # fill occluded frames
                    for tt in range(s, e + 1):
                        if not occ_mask[tt]:
                            continue
                        idx = tt * num_humans + slot
                        orig = param_np[idx, :].copy()

                        # 1) build interpolated candidate
                        if (prev_vec is not None) and (next_vec is not None):
                            span_start = prev_support[-1]
                            span_end   = next_support[0]
                            if span_end > span_start:
                                r = float(np.clip(tt - span_start, 0, span_end - span_start)) / float(
                                    max(span_end - span_start, 1)
                                )
                            else:
                                r = 0.5
                            interp_vec = (1.0 - r) * prev_vec + r * next_vec
                        elif prev_vec is not None:
                            interp_vec = prev_vec
                        else:
                            interp_vec = next_vec

                        # 2) distance between interp and original
                        diff_norm = float(np.linalg.norm(interp_vec - orig) / max(D, 1))

                        # 3) adaptive weight（整体更松）
                        if diff_norm < diff_per_dim_th:
                            w = 0.82    # similar -> strong but不极端
                        elif diff_norm < 2 * diff_per_dim_th:
                            w = 0.65    # moderate difference
                        else:
                            w = 0.45    # large difference -> 原值占比较大

                        # 4) if occlusion starts at beginning and only next side exists -> even weaker
                        if is_at_start and (prev_vec is None) and (next_vec is not None):
                            w = min(w, 0.35)

                        # 5) final blend
                        param_np[idx, :] = w * interp_vec + (1.0 - w) * orig

                    # ----- loose boundary diffusion: visible frames around interface -----
                    # left side (before s)
                    if len(prev_support) > 0:
                        edge_occ_idx = s * num_humans + slot
                        edge_val = param_np[edge_occ_idx, :].copy()
                        center_frame = prev_support[-1]  # nearest visible before s
                        for d in range(boundary_radius + 1):
                            f = center_frame - d
                            if f < 0:
                                break
                            if not visible_mask[f]:
                                continue
                            idx_vis_f = f * num_humans + slot
                            orig_vis = param_np[idx_vis_f, :].copy()
                            w_b = boundary_blend * (boundary_decay ** d)
                            if w_b <= 0.0:
                                continue
                            param_np[idx_vis_f, :] = w_b * edge_val + (1.0 - w_b) * orig_vis

                    # right side (after e)
                    if len(next_support) > 0:
                        edge_occ_idx = e * num_humans + slot
                        edge_val = param_np[edge_occ_idx, :].copy()
                        center_frame = next_support[0]  # nearest visible after e
                        for d in range(boundary_radius + 1):
                            f = center_frame + d
                            if f >= num_frames:
                                break
                            if not visible_mask[f]:
                                continue
                            idx_vis_f = f * num_humans + slot
                            orig_vis = param_np[idx_vis_f, :].copy()
                            w_b = boundary_blend * (boundary_decay ** d)
                            if w_b <= 0.0:
                                continue
                            param_np[idx_vis_f, :] = w_b * edge_val + (1.0 - w_b) * orig_vis

                    t += 1

        new_mhr[k] = torch.from_numpy(param_np).to(device)

    # pass-through for other keys
    for k, v in mhr_dict.items():
        if k not in new_mhr:
            new_mhr[k] = v

    return new_mhr


def local_window_smooth(Y, window=9, weights=None):
    """
    Strong local smoothing over a temporal window.

    Args:
        Y:       np.ndarray, shape (T, D)
        window:  odd int, temporal window size (e.g., 7 or 9)
                 for frame t, we average over [t-half, t+half]
        weights: optional np.ndarray, shape (T,)
                 per-frame reliability/visibility in [0, 1].
                 If provided, use weighted average inside the window.

    Returns:
        Smoothed Y of shape (T, D)
    """
    Y = np.asarray(Y, dtype=np.float32)
    T, D = Y.shape
    out = np.zeros_like(Y)
    half = window // 2

    if weights is not None:
        w = np.asarray(weights, dtype=np.float32)
        w = np.clip(w, 0.0, 1.0)
    else:
        w = None

    for t in range(T):
        s = max(0, t - half)
        e = min(T, t + half + 1)  # [s, e)

        if w is None:
            out[t] = Y[s:e].mean(axis=0)
        else:
            ww = w[s:e]
            ww_sum = ww.sum()
            if ww_sum < 1e-6:
                # if all weights ~0, fall back to simple mean
                out[t] = Y[s:e].mean(axis=0)
            else:
                ww_norm = ww / ww_sum
                out[t] = (Y[s:e] * ww_norm[:, None]).sum(axis=0)

    return out


def smooth_scale_shape_local(mhr, num_frames, window=9,
                             vis_scale=None, vis_shape=None):
    """
    Apply strong local window smoothing on 'scale' and 'shape' for multi-human case.

    Args:
        mhr:         dict with 'scale' and 'shape' tensors of shape (B, D)
        num_frames:  int, T
        window:      odd int, temporal window size
        vis_scale:   optional (B,) or (T,) visibility/confidence for scale
        vis_shape:   optional (B,) or (T,) visibility/confidence for shape

    Returns:
        new_scale, new_shape: tensors with the same shape as input
    """
    scale = mhr["scale"]
    shape = mhr["shape"]
    device = scale.device

    B, D_scale = scale.shape
    _, D_shape = shape.shape
    assert B % num_frames == 0, "B must be divisible by num_frames"
    num_humans = B // num_frames

    scale_np = scale.detach().cpu().numpy().reshape(num_frames, num_humans, D_scale)
    shape_np = shape.detach().cpu().numpy().reshape(num_frames, num_humans, D_shape)

    # Optional visibility weights per frame (shared across humans)
    if vis_scale is not None:
        vs = np.asarray(vis_scale, dtype=np.float32).reshape(num_frames)
    else:
        vs = None

    if vis_shape is not None:
        vh = np.asarray(vis_shape, dtype=np.float32).reshape(num_frames)
    else:
        vh = None

    for h in range(num_humans):
        scale_np[:, h, :] = local_window_smooth(scale_np[:, h, :], window=window, weights=vs)
        shape_np[:, h, :] = local_window_smooth(shape_np[:, h, :], window=window, weights=vh)

    scale_smooth = torch.from_numpy(scale_np.reshape(B, D_scale)).to(device)
    shape_smooth = torch.from_numpy(shape_np.reshape(B, D_shape)).to(device)
    return scale_smooth, shape_smooth
