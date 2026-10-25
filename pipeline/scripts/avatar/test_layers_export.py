"""python -m pytest pipeline/scripts/avatar/test_layers_export.py (no GPU)."""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from avatar.layers_export import LayerWriter  # noqa: E402


def test_avatar_layer_keeps_the_render_colour(tmp_path):
    """The render is RGBA; the webp the editor decodes must show the same colour (a red avatar stays red)."""
    im = np.zeros((4, 4, 4), np.uint8)
    im[...] = (255, 0, 0, 255)                     # RGBA red, supersampled 2x over a 2x2 box
    w = LayerWriter(str(tmp_path), (8, 6), [1])
    w.frame(0, np.zeros((6, 8, 3), np.uint8), {1: (im, np.ones((4, 4), np.float32), (1, 1, 3, 3), None)}, {}, 2)
    w.close()
    bgra = cv2.imread(str(tmp_path / "avatar" / "1" / "00000.webp"), cv2.IMREAD_UNCHANGED)
    b, g, r, a = (int(v) for v in bgra[2, 2])
    assert a == 255 and r > 200 and b < 50 and g < 50
    assert bgra[0, 0, 3] == 0
