"""Drop SAM3 tracker outputs of past frames that no later frame of the propagation can read.

Sam3TrackerPredictor.propagate_in_video stores every tracked frame's output (maskmem_features bf16 + pred_masks
fp32: ~2 MB per frame for 2 objects) in output_dict["non_cond_frame_outputs"] and, as views, in
output_dict_per_obj. Over 1800 frames that is +3.9 GB of VRAM, so a 5-minute clip would not fit in 12 GB.

During a forward propagation with memory selection (lite's tracker: apply_temporal_disambiguation=True) and
memory_temporal_stride_for_eval 1, frame u reads past non-conditioning outputs only through
Sam3TrackerBase.frame_filter: it walks back from u-1 and takes frames whose eff_iou_score > mf_threshold until it has
max_obj_ptrs_in_encoder - 1 (15) of them, plus u-1 itself (must_include); both the memory features and the object
pointers come from that list. Every later u walks back over the same frames, so once frame t is done the only
non-conditioning frames <= t a later frame can select are t and the 15 most recent frames above the threshold.
prune_propagation keeps t and the last `keep_valid` (16) such frames and deletes the rest from both dicts, after the
caller has consumed frame t's yielded masks. Conditioning frames (the box prompts) and frames consolidated from
prompts are never touched. Masks are bit-identical (scripts/eval/check_tracker_prune.py). Any other configuration
(reverse, a stride, no memory selection, training) passes through unchanged.
"""


def prune_propagation(predictor, inference_state, propagation, reverse=False, keep_valid=16):
    """Iterate `propagation` (predictor.propagate_in_video(...)) and prune after each yielded frame."""
    if (reverse or predictor.training or not getattr(predictor, "use_memory_selection", False)
            or getattr(predictor, "memory_temporal_stride_for_eval", 1) != 1
            or keep_valid < predictor.max_obj_ptrs_in_encoder - 1):
        yield from propagation
        return
    output_dict = inference_state["output_dict"]
    per_obj = inference_state["output_dict_per_obj"]
    prompted = inference_state["consolidated_frame_inds"]["non_cond_frame_outputs"]
    thr = predictor.mf_threshold
    alive, valid = [], []
    for item in propagation:
        yield item
        t = item[0]
        out = output_dict["non_cond_frame_outputs"].get(t)
        if out is None or t in prompted:          # a conditioning or prompted frame: not ours to drop
            continue
        score = out.get("eff_iou_score")
        alive.append(t)
        if score is not None and bool(score > thr):   # frame_filter's test
            valid.append(t)
            del valid[:-keep_valid]
        keep = set(valid)
        keep.add(t)
        drop = [f for f in alive if f not in keep]
        if drop:
            alive = [f for f in alive if f in keep]
            for f in drop:
                output_dict["non_cond_frame_outputs"].pop(f, None)
                for d in per_obj.values():
                    d["non_cond_frame_outputs"].pop(f, None)
