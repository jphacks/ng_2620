"""Check utils.bf16_weights on ViTDet: raw detections (boxes, scores, classes) on 12 frames must be bit-identical.

Run each mode in its own process (SAM3-free, so no process-wide autocast / TF32 state), then compare:
  .venv/Scripts/python scripts/eval/check_bf16_weights.py base a.npz
  .venv/Scripts/python scripts/eval/check_bf16_weights.py bf16 b.npz
  .venv/Scripts/python scripts/eval/check_bf16_weights.py vitdet_attn c.npz   (bf16 + utils/vitdet_attn.py)
  .venv/Scripts/python scripts/eval/check_bf16_weights.py compare a.npz b.npz
2026-10-16: identical; peak allocated 6.54 -> 3.91 GB (bf16), -> 2.07 GB (vitdet_attn). The fusion SAM3 model is checked through its caches
(cands.npz / person.npz bit-identical, eager and compiled decoder; testdata/2026-10-16/vram30).
"""
import glob
import sys

import numpy as np
import torch

REPO = "C:/VScodeFiles/sam-body4d"
sys.path.insert(0, REPO)
sys.path.insert(0, f"{REPO}/scripts")
import offline_app as oa  # noqa: E402,F401  (sys.path for models/*)
from utils.skip_init import skip_random_init  # noqa: E402
from utils.bf16_weights import bf16_weights  # noqa: E402
from models.sam_3d_body.tools.build_detector import HumanDetector  # noqa: E402
from models.sam_3d_body.sam_3d_body.data.utils.io import load_image  # noqa: E402
import detectron2.data.transforms as T  # noqa: E402

mode, out = sys.argv[1], sys.argv[2]
if mode == "compare":
    a, b = np.load(out), np.load(sys.argv[3])
    bad = [k for k in a.files if not np.array_equal(a[k], b[k])]
    print("identical" if not bad and a.files == b.files else f"DIFFERENT: {bad}")
    sys.exit(1 if bad else 0)
P = "C:/VScodeFiles/datasets/PennAction/frames"
imgs = [sorted(glob.glob(f"{P}/{c}/*.jpg"))[k] for c in ("1791", "1786", "0400", "0826", "1765") for k in (0, 20)]
imgs += sorted(glob.glob("C:/VScodeFiles/datasets/3DPW/imageFiles/courtyard_arguing_00/*.jpg"))[:2]
with skip_random_init():
    det = HumanDetector(name="vitdet", device=torch.device("cuda"), path="")
if mode == "vitdet_attn":
    from utils import vitdet_attn  # noqa: E402
    vitdet_attn.install()
if mode in ("bf16", "vitdet_attn"):
    print("freed GB", bf16_weights(det.detector) / 2 ** 30)
torch.cuda.synchronize()
torch.cuda.reset_peak_memory_stats()
res = {}
with torch.autocast("cuda", dtype=torch.bfloat16):
    for j, p in enumerate(imgs):
        img = load_image(p, backend="cv2", image_format="bgr")
        h, w = img.shape[:2]
        t = T.ResizeShortestEdge(short_edge_length=1024, max_size=1024)
        x = torch.as_tensor(t(T.AugInput(img)).apply_image(img).astype("float32").transpose(2, 0, 1))
        with torch.no_grad():
            ins = det.detector([{"image": x, "height": h, "width": w}])[0]["instances"]
        res[f"{j}_box"] = ins.pred_boxes.tensor.float().cpu().numpy()
        res[f"{j}_score"] = ins.scores.float().cpu().numpy()
        res[f"{j}_cls"] = ins.pred_classes.cpu().numpy()
torch.cuda.synchronize()
print(mode, "n_img", len(imgs), "peak_alloc GB", torch.cuda.max_memory_allocated() / 2 ** 30,
      "peak_res GB", torch.cuda.max_memory_reserved() / 2 ** 30)
np.savez(out, **res)
