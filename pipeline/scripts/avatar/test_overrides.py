"""python -m pytest pipeline/scripts/avatar/test_overrides.py (no GPU)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "occlusion", "fusion_v2"))
from avatar.occlusion import Overrides  # noqa: E402


def test_last_correction_covering_the_frame_wins():
    o = Overrides([{"personId": 1, "tracks": [3, 4], "range": [10, 20], "mode": "front"},
                   {"personId": 1, "tracks": [4], "range": [15, 30], "mode": "behind"}])
    assert o.mode(3, 1, 12) == "front" and o.mode(4, 1, 12) == "front"
    assert o.mode(4, 1, 16) == "behind" and o.mode(3, 1, 25) is None
    assert o.mode(3, 2, 12) is None and o.mode(9, 1, 12) is None
