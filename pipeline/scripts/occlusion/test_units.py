"""python -m pytest pipeline/scripts/occlusion/test_units.py (numpy only, no GPU)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import units as U  # noqa: E402


def box(x0, y0, x1, y1, shape=(40, 40)):
    m = np.zeros(shape, bool)
    m[y0:y1, x0:x1] = True
    return m


def test_two_tracks_continuing_one_object_make_one_unit():
    masks = [box(5, 5, 20, 20), box(6, 5, 21, 20), box(30, 30, 35, 35)]
    ev = {"units": [
        {"i": 0, "tr": 1, "ci": 0, "pid": 1, "part": 0}, {"i": 9, "tr": 1, "ci": 0, "pid": 1, "part": 0},
        {"i": 11, "tr": 2, "ci": 1, "pid": 1, "part": 0}, {"i": 20, "tr": 2, "ci": 1, "pid": 1, "part": 1},
        {"i": 11, "tr": 3, "ci": 2, "pid": 2, "part": 0},                # far away: its own unit
        {"i": 5, "tr": 7, "ci": 2, "pid": 2, "part": 0, "person": 1},  # person evidence is not an object
    ]}
    dec = {(1, 1, 0, "o"): (False, "D-", -2.0, 0.1, 3), (2, 1, 1, "o"): (True, "D+", 2.4, 0.5, 40),
           (3, 2, 0, "o"): (False, "S-", np.nan, 0.2, 2)}
    units, by_frame = U.build_units(ev, dec, masks, 21, [1, 2])
    assert [u["tracks"] for u in units] == [[1, 2], [3]]
    u1 = units[0]
    assert u1["frames"] == [0, 20] and u1["perPerson"]["1"]["auto"] == "front" and u1["perPerson"]["1"]["why"] == "D+"
    assert u1["perPerson"]["1"]["confidence"] == 0.8 and u1["perPerson"]["1"]["overlapFrames"] == [0, 20]
    assert u1["perPerson"]["2"]["auto"] == "none"
    assert units[1]["perPerson"]["2"]["auto"] == "behind" and units[1]["perPerson"]["2"]["confidence"] == 0.2
    assert by_frame[11] == [(1, 1), (2, 2)]


def test_tracks_too_far_apart_in_time_stay_separate():
    masks = [box(5, 5, 20, 20), box(5, 5, 20, 20)]
    ev = {"units": [{"i": 0, "tr": 1, "ci": 0, "pid": 1, "part": 0},
                    {"i": 10, "tr": 2, "ci": 1, "pid": 1, "part": 0}]}
    assert U.link_tracks(U.object_tracks(ev), masks) == [[1], [2]]


def test_smaller_unit_wins_where_they_overlap():
    masks = [box(0, 0, 30, 30), box(10, 10, 15, 15)]
    lab = U.label_image([(1, 0), (2, 1)], masks, (40, 40))
    assert lab.dtype == np.uint16 and lab[12, 12] == 2 and lab[2, 2] == 1 and lab[35, 35] == 0
