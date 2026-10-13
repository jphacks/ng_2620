"""Render the final mesh-overlay video from offline_app outputs (no model inference).

Reads <pipeline_out>/images, mesh_4d_individual/<pid>/*.ply and
focal_4d_individual/<pid>/*.json, and draws the meshes over the frames with the
repo Renderer, using the same per-person colours as offline_app
(color_list[pid + 4]).

  python scripts/eval/render_overlay.py --pipeline_out <dir> --out <file.mp4> [--fps 25]
"""
import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np
import trimesh

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path += [ROOT, os.path.join(ROOT, "models", "sam_3d_body")]

from utils import images_to_mp4  # noqa: E402
from utils.painter import color_list  # noqa: E402
from sam_3d_body.visualization.renderer import Renderer  # noqa: E402

# save_mesh_results exports (pred_vertices + pred_cam_t) rotated 180 deg about x;
# undoing it gives camera-space vertices, which Renderer draws with cam_t = 0.
FLIP = np.array([1.0, -1.0, -1.0])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pipeline_out", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--fps", type=int, default=25)
    args = p.parse_args()

    mesh_root = os.path.join(args.pipeline_out, "mesh_4d_individual")
    focal_root = os.path.join(args.pipeline_out, "focal_4d_individual")
    pids = sorted((os.path.basename(d) for d in glob.glob(os.path.join(mesh_root, "*"))), key=int)
    frames = sorted(glob.glob(os.path.join(args.pipeline_out, "images", "*.jpg")))

    out_frames = []
    for fp in frames:
        stem = os.path.splitext(os.path.basename(fp))[0]
        img = cv2.imread(fp)
        verts, faces, colors, focal = [], [], [], None
        for pid in pids:
            ply = os.path.join(mesh_root, pid, stem + ".ply")
            if not os.path.exists(ply):
                continue
            mesh = trimesh.load(ply, process=False)  # keep the 18439-vertex order Renderer expects
            with open(os.path.join(focal_root, pid, stem + ".json")) as f:
                focal = json.load(f)["focal_length"]
            faces.append(np.asarray(mesh.faces) + sum(len(v) for v in verts))
            verts.append(np.asarray(mesh.vertices) * FLIP)
            colors.append(color_list[int(pid) + 4])
        if verts:
            renderer = Renderer(focal_length=focal, faces=np.concatenate(faces))
            img = renderer(np.concatenate(verts), np.zeros(3), img,
                           mesh_base_color=colors if len(colors) > 1 else colors[0],
                           scene_bg_color=(1, 1, 1)) * 255
        out_frames.append(cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2RGB))

    images_to_mp4(out_frames, args.out, fps=args.fps)


if __name__ == "__main__":
    main()
