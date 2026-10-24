"""
Utility functions for SAM 3D Body demo notebook
"""

import os
from typing import Any, Dict, List, Optional

import cv2
import matplotlib.pyplot as plt
import numpy as np
import torch
import json

from sam_3d_body import load_sam_3d_body_hf, SAM3DBodyEstimator
from sam_3d_body.metadata.mhr70 import pose_info as mhr70_pose_info
from sam_3d_body.visualization.renderer import Renderer
from sam_3d_body.visualization.skeleton_visualizer import SkeletonVisualizer

from utils.painter import color_list

from PIL import Image

LIGHT_BLUE = (0.65098039, 0.74117647, 0.85882353)


def setup_sam_3d_body(
    hf_repo_id: str = "facebook/sam-3d-body-vith",
    detector_name: str = "vitdet",
    segmentor_name: str = "sam2",
    fov_name: str = "moge2",
    detector_path: str = "",
    segmentor_path: str = "",
    fov_path: str = "",
    device: str = "cuda",
):
    """
    Set up SAM 3D Body estimator with optional components.

    Args:
        hf_repo_id: HuggingFace repository ID for the model
        detector_name: Name of detector to use (default: "vitdet")
        segmentor_name: Name of segmentor to use (default: "sam2")
        fov_name: Name of FOV estimator to use (default: "moge2")
        detector_path: URL or path for human detector model
        segmentor_path: Path to human segmentor model (optional)
        fov_path: path for FOV estimator
        device: Device to use (default: auto-detect cuda/cpu)

    Returns:
        estimator: SAM3DBodyEstimator instance ready for inference
    """
    print(f"Loading SAM 3D Body model from {hf_repo_id}...")

    # Auto-detect device if not specified
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    # Load core model from HuggingFace
    model, model_cfg = load_sam_3d_body_hf(hf_repo_id, device=device)

    # Initialize optional components
    human_detector, human_segmentor, fov_estimator = None, None, None

    if detector_name:
        print(f"Loading human detector from {detector_name}...")
        from tools.build_detector import HumanDetector

        human_detector = HumanDetector(name=detector_name, device=device)

    if segmentor_path:
        print(f"Loading human segmentor from {segmentor_path}...")
        from tools.build_sam import HumanSegmentor

        human_segmentor = HumanSegmentor(
            name=segmentor_name, device=device, path=segmentor_path
        )

    if fov_name:
        print(f"Loading FOV estimator from {fov_name}...")
        from tools.build_fov_estimator import FOVEstimator

        fov_estimator = FOVEstimator(name=fov_name, device=device)

    # Create estimator wrapper
    estimator = SAM3DBodyEstimator(
        sam_3d_body_model=model,
        model_cfg=model_cfg,
        human_detector=human_detector,
        human_segmentor=human_segmentor,
        fov_estimator=fov_estimator,
    )

    print(f"Setup complete!")
    print(
        f"  Human detector: {'✓' if human_detector else '✗ (will use full image or manual bbox)'}"
    )
    print(
        f"  Human segmentor: {'✓' if human_segmentor else '✗ (mask inference disabled)'}"
    )
    print(f"  FOV estimator: {'✓' if fov_estimator else '✗ (will use default FOV)'}")

    return estimator


def setup_visualizer():
    """Set up skeleton visualizer with MHR70 pose info"""
    visualizer = SkeletonVisualizer(line_width=2, radius=5)
    visualizer.set_pose_meta(mhr70_pose_info)
    return visualizer


def visualize_2d_results(
    img_cv2: np.ndarray, outputs: List[Dict[str, Any]], visualizer: SkeletonVisualizer
) -> List[np.ndarray]:
    """Visualize 2D keypoints and bounding boxes"""
    results = []

    for pid, person_output in enumerate(outputs):
        img_vis = img_cv2.copy()

        # Draw keypoints
        keypoints_2d = person_output["pred_keypoints_2d"]
        keypoints_2d_vis = np.concatenate(
            [keypoints_2d, np.ones((keypoints_2d.shape[0], 1))], axis=-1
        )
        img_vis = visualizer.draw_skeleton(img_vis, keypoints_2d_vis)

        # Draw bounding box
        bbox = person_output["bbox"]
        img_vis = cv2.rectangle(
            img_vis,
            (int(bbox[0]), int(bbox[1])),
            (int(bbox[2]), int(bbox[3])),
            (0, 255, 0),  # Green color
            2,
        )

        # Add person ID text
        cv2.putText(
            img_vis,
            f"Person {pid}",
            (int(bbox[0]), int(bbox[1] - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
        )

        results.append(img_vis)

    return results


def visualize_3d_mesh(
    img_cv2: np.ndarray, outputs: List[Dict[str, Any]], faces: np.ndarray
) -> List[np.ndarray]:
    """Visualize 3D mesh overlaid on image and side view"""
    results = []

    for pid, person_output in enumerate(outputs):
        # Create renderer for this person
        renderer = Renderer(focal_length=person_output["focal_length"], faces=faces)

        # 1. Original image
        img_orig = img_cv2.copy()

        # 2. Mesh overlay on original image
        img_mesh_overlay = (
            renderer(
                person_output["pred_vertices"],
                person_output["pred_cam_t"],
                img_cv2.copy(),
                mesh_base_color=LIGHT_BLUE,
                scene_bg_color=(1, 1, 1),
            )
            * 255
        ).astype(np.uint8)

        # 3. Mesh on white background (front view)
        white_img = np.ones_like(img_cv2) * 255
        img_mesh_white = (
            renderer(
                person_output["pred_vertices"],
                person_output["pred_cam_t"],
                white_img,
                mesh_base_color=LIGHT_BLUE,
                scene_bg_color=(1, 1, 1),
            )
            * 255
        ).astype(np.uint8)

        # 4. Side view
        img_mesh_side = (
            renderer(
                person_output["pred_vertices"],
                person_output["pred_cam_t"],
                white_img.copy(),
                mesh_base_color=LIGHT_BLUE,
                scene_bg_color=(1, 1, 1),
                side_view=True,
            )
            * 255
        ).astype(np.uint8)

        # Combine all views
        combined = np.concatenate(
            [img_orig, img_mesh_overlay, img_mesh_white, img_mesh_side], axis=1
        )
        results.append(combined)

    return results


def save_mesh_results(
    outputs: List[Dict[str, Any]],
    faces: np.ndarray,
    save_dir: str,
    focal_dir: str, 
    image_path: str,
    id_current: List,
):
    """Save 3D mesh results to files and return PLY file paths"""

    if outputs is None:
        return

    for pid, person_output in enumerate(outputs):
        # Create renderer for this person
        renderer = Renderer(focal_length=person_output["focal_length"], faces=faces)

        # Store individual mesh
        color = tuple(c / 255.0 for c in color_list[id_current[pid]+4])
        tmesh = renderer.vertices_to_trimesh(
            person_output["pred_vertices"], person_output["pred_cam_t"], color
        )
        mesh_path = f"{save_dir}/{pid+1}/{os.path.basename(image_path)[:-4]}.ply"
        tmesh.export(mesh_path)

        focal_length = {'focal_length': person_output["focal_length"].item(), 'camera': [float(x) for x in person_output['pred_cam_t']]}
        with open(f"{focal_dir}/{pid+1}/{os.path.basename(image_path)[:-4]}.json", "w") as f:
            json.dump(focal_length, f, indent=4)


def display_results_grid(
    images: List[np.ndarray], titles: List[str], figsize_per_image: tuple = (6, 6)
):
    """Display multiple images in a grid"""
    n_images = len(images)
    if n_images == 0:
        print("No images to display")
        return

    # Calculate grid dimensions
    cols = min(3, n_images)  # Max 3 columns
    rows = (n_images + cols - 1) // cols

    fig, axes = plt.subplots(
        rows, cols, figsize=(figsize_per_image[0] * cols, figsize_per_image[1] * rows)
    )

    # Handle single image case
    if n_images == 1:
        axes = [axes]
    elif rows == 1:
        axes = [axes] if cols == 1 else list(axes)
    else:
        axes = axes.flatten()

    for i, (img, title) in enumerate(zip(images, titles)):
        if len(img.shape) == 3 and img.shape[2] == 3:
            # Convert BGR to RGB if needed
            if img.dtype == np.uint8 and np.mean(img) > 1:
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            else:
                img_rgb = img
        else:
            img_rgb = img

        axes[i].imshow(img_rgb)
        axes[i].set_title(title)
        axes[i].axis("off")

    # Hide unused subplots
    for i in range(n_images, len(axes)):
        axes[i].axis("off")

    plt.tight_layout()
    plt.show()


def process_image_with_mask(estimator, image_path: str, mask_path: str, idx_path, idx_dict, mhr_shape_scale_dict, occ_dict, batch_kps=None, kps_id=None, cam_int=None, iou_dict=None, predictor=None):
    """
    Process image with external mask input.

    Note: The refactored code requires bboxes to be provided along with masks.
    This function automatically computes bboxes from the mask.
    """
    n_frames = len(image_path)
    obj_ids = sorted(map(int, occ_dict.keys()))
    empty_dict = {}
    id_batch = []
    mask_outputs_dict = {}

    # infer per object
    for obj_id in obj_ids:
        # prepare data (HMR for non-occ first, followed by occ)
        # load in batches
        no_occ_image_batch = []
        no_occ_bbox_batch = []
        no_occ_mask_batch = []
        no_occ_kps_batch = []
        no_occ_id_batch = []
        no_occ_empty_frame_list = []
        _occ_image_batch = []
        _occ_bbox_batch = []
        _occ_mask_batch = []
        _occ_kps_batch = []
        _occ_image_batch_ori = []
        _occ_id_batch = []
        _occ_empty_frame_list = []

        occ_idx = occ_dict[obj_id]
        # for each frame:
        for i in range(n_frames):
            # Load mask
            mask = np.array(Image.open(mask_path[i]).convert('P'))

            no_occ_mask_list = []
            no_occ_bbox_list = []
            no_occ_kp_list = []
            no_occ_id_current = []
            _occ_mask_list = []
            _occ_bbox_list = []
            _occ_kp_list = []
            _occ_id_current = []

            if occ_idx[i] == 0:
                if kps_id is not None:
                    mask_com = np.array(Image.open(os.path.join(idx_path[obj_id]['masks'], f"{kps_id[0]:08d}.png")).convert('P'))     
                else:
                    mask_com = np.array(Image.open(os.path.join(idx_path[obj_id]['masks'], f"{i:08d}.png")).convert('P')) 
                zero_mask = np.zeros_like(mask_com)
                zero_mask[mask_com==obj_id] = 255
                mask_binary = zero_mask.astype(np.uint8)
                _occ_mask_list.append(mask_binary)
                # Compute bounding box from mask (required by refactored code)
                # Find all non-zero pixels in the mask
                coords = cv2.findNonZero(mask_binary)
                if mask_binary.max() > 0:
                    _occ_id_current.append(obj_id)
                # Get bounding box from mask contours
                x, y, w, h = cv2.boundingRect(coords)
                bbox = np.array([[x, y, x + w, y + h]], dtype=np.float32)
                # print(f"Computed bbox from mask: {bbox[0]}")
                _occ_bbox_list.append(bbox)
                if batch_kps is not None:
                    _occ_kp_list.append(batch_kps[obj_id-1][i])  # N x 3
                
                if len(_occ_bbox_list) == 0:
                    _occ_empty_frame_list.append(i)
                else:
                    _occ_id_batch.append(_occ_id_current)
                    bbox = np.stack(_occ_bbox_list, axis=0)  # TODO: sometimes empty
                    if batch_kps is not None:
                        _occ_kps_batch.append(np.stack(_occ_kp_list, axis=0))
                    mask_binary = np.stack(_occ_mask_list, axis=0)
                    # Process with external mask and computed bbox
                    # Note: The mask needs to match the number of bboxes (1 bbox -> 1 mask)
                    _occ_image_batch.append(os.path.join(idx_path[obj_id]['images'], f"{i:08d}.jpg"))
                    _occ_image_batch_ori.append(image_path[i])
                    _occ_mask_batch.append(mask_binary)
                    _occ_bbox_batch.append(bbox)
            else:
                zero_mask = np.zeros_like(mask)
                zero_mask[mask==obj_id] = 255
                mask_binary = zero_mask.astype(np.uint8)

                # mute objects near margin
                H, W = mask_binary.shape
                zero_mask_cp = np.zeros_like(mask)
                zero_mask_cp[mask==obj_id] = 255
                mask_binary_cp = zero_mask_cp.astype(np.uint8)
                mask_binary_cp[:int(H*0.05), :] = mask_binary_cp[-int(H*0.05):, :] = mask_binary_cp[:, :int(W*0.05)] = mask_binary_cp[:, -int(W*0.05):] = 0
                if mask_binary_cp.max() == 0:   # margin objects
                    mask_binary = mask_binary_cp

                no_occ_mask_list.append(mask_binary)
                # Compute bounding box from mask (required by refactored code)
                # Find all non-zero pixels in the mask
                coords = cv2.findNonZero(mask_binary)
                
                if mask_binary.max() > 0:
                    no_occ_id_current.append(obj_id)

                # Get bounding box from mask contours
                x, y, w, h = cv2.boundingRect(coords)
                bbox = np.array([[x, y, x + w, y + h]], dtype=np.float32)

                # print(f"Computed bbox from mask: {bbox[0]}")
                no_occ_bbox_list.append(bbox)
                if batch_kps is not None:
                    no_occ_kp_list.append(batch_kps[obj_id-1][i])  # N x 3

                if len(no_occ_bbox_list) == 0:
                    no_occ_empty_frame_list.append(i)
                else:
                    no_occ_id_batch.append(no_occ_id_current)
                    bbox = np.stack(no_occ_bbox_list, axis=0)  # TODO: sometimes empty
                    if batch_kps is not None:
                        no_occ_kps_batch.append(np.stack(no_occ_kp_list, axis=0))
                    mask_binary = np.stack(no_occ_mask_list, axis=0)
                    # Process with external mask and computed bbox
                    # Note: The mask needs to match the number of bboxes (1 bbox -> 1 mask)
                    no_occ_image_batch.append(image_path[i])
                    no_occ_mask_batch.append(mask_binary)
                    no_occ_bbox_batch.append(bbox)

        if len(no_occ_empty_frame_list) > 0:
            for occ_k, occ_v in occ_dict.items():
                for i in sorted(no_occ_empty_frame_list, reverse=True):
                    occ_v.pop(i)
        if len(_occ_empty_frame_list) > 0:
            for occ_k, occ_v in occ_dict.items():
                for i in sorted(_occ_empty_frame_list, reverse=True):
                    occ_v.pop(i)

        empty_dict[f"{obj_id}-occ"] = _occ_empty_frame_list
        empty_dict[f"{obj_id}-no_occ"] = no_occ_empty_frame_list

        if batch_kps is None:
            no_occ_kps_batch = None
            _occ_kps_batch = None

        if len(no_occ_image_batch) > 0:
            no_occ_outputs = estimator.process_frames(no_occ_image_batch, bboxes=no_occ_bbox_batch, masks=no_occ_mask_batch, id_batch=[[1] for idb in range(len(no_occ_image_batch))], idx_path={}, idx_dict={}, mhr_shape_scale_dict=mhr_shape_scale_dict, kps_batch=no_occ_kps_batch, occ_dict=None, use_mask=True, kps_id=kps_id, cam_int=cam_int)
        if len(_occ_image_batch) > 0:
            _occ_outputs = estimator.process_frames(_occ_image_batch, bboxes=_occ_bbox_batch, masks=_occ_mask_batch, id_batch=[[1] for idb in range(len(_occ_image_batch))], idx_path={}, idx_dict={}, mhr_shape_scale_dict=mhr_shape_scale_dict, kps_batch=_occ_kps_batch, occ_dict=None, use_mask=True, kps_id=kps_id, _occ_image_batch_ori=_occ_image_batch_ori, cam_int=cam_int)
        
        oid_outputs = []
        ia, ib = 0, 0
        for oi in occ_idx:
            if oi == 1:
                oid_outputs.append(no_occ_outputs[ia])
                ia += 1
            else:
                oid_outputs.append(_occ_outputs[ib])
                ib += 1
        
        mask_outputs_dict[obj_id] = oid_outputs

    final_outputs = []
    id_batch = []
    empty_frame_list = []
    for i in range(n_frames):
        i_outputs = []
        i_batch = []
        for obj_id in obj_ids:  # sorted
            if mask_outputs_dict[obj_id][i][0]['bbox'][0]+mask_outputs_dict[obj_id][i][0]['bbox'][2] > 0:   # 0 0 0 0 (no objects)
                i_outputs.append(mask_outputs_dict[obj_id][i][0])   # always = 1
                i_batch.append(obj_id)

        final_outputs.append(i_outputs)
        id_batch.append(i_batch)
        if len(i_batch) == 0:
            empty_frame_list.append(i)

    return final_outputs, id_batch, empty_frame_list


def process_image_with_bbox(estimator, image_path: str, bboxes, idx_path, idx_dict, mhr_shape_scale_dict, occ_dict, batch_kps=None, flip=False, cam_int=None):
    """
    Process image with external mask input.

    Note: The refactored code requires bboxes to be provided along with masks.
    This function automatically computes bboxes from the mask.
    """
    # load in batches
    image_batch = []
    bbox_batch = []
    kps_batch = []
    n = len(image_path)
    id_batch = []
    empty_frame_list = []
    obj_ids = [oi+1 for oi in range(len(bboxes))]
    for i in range(n):
        bbox_list = []
        kp_list = []
        id_current = []
        
        for obj_id in obj_ids:
            id_current.append(obj_id)
            # Get bounding box from mask contours
            x, y, x2, y2 = bboxes[obj_id-1][i][0].item(), bboxes[obj_id-1][i][1].item(), bboxes[obj_id-1][i][2].item(), bboxes[obj_id-1][i][3].item()
            bbox = np.array([[x, y, x2, y2]], dtype=np.float32)
            # print(f"Computed bbox from mask: {bbox[0]}")
            bbox_list.append(bbox)
            if batch_kps is not None:
                kp_list.append(batch_kps[obj_id-1][i])  # N x 3

        if len(bbox_list) == 0:
            empty_frame_list.append(i)
            continue

        id_batch.append(id_current)
        bbox = np.stack(bbox_list, axis=0)  # TODO: sometimes empty
        if batch_kps is not None:
            kps_batch.append(np.stack(kp_list, axis=0))
        # mask_binary = np.stack(mask_list, axis=0)
        # Process with external mask and computed bbox
        # Note: The mask needs to match the number of bboxes (1 bbox -> 1 mask)
        image_batch.append(image_path[i])
        bbox_batch.append(bbox)
    
    if len(empty_frame_list) > 0:
        for occ_k, occ_v in occ_dict.items():
            for i in sorted(empty_frame_list, reverse=True):
                occ_v.pop(i)

    outputs = estimator.process_frames(image_batch, bboxes=bbox_batch, masks=None, id_batch=id_batch, idx_path=idx_path, idx_dict=idx_dict, mhr_shape_scale_dict=mhr_shape_scale_dict, occ_dict=occ_dict, kps_batch=kps_batch, flip=flip, cam_int=cam_int)   # use_mask=False default

    return outputs, id_batch, empty_frame_list