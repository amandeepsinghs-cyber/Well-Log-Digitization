"""Tests for hollowing out the colour fills and erasing the annotation labels.

The property that matters most is negative: the curves must still be there
afterwards. A fill removal that also takes the curve bounding it produces a LAS
file with a missing interval and no indication anything went wrong.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.ingest.load_image import FORMAT_RGB, from_array, to_array
from app.preprocess.remove_annotations import remove_annotations

# Colours measured from the reference scan.
YELLOW_SAND = (238, 232, 130)
GREY_HYDROCARBON = (150, 150, 150)
BLACK_CURVE = (26, 26, 26)
BROWN_RHOB = (181, 101, 29)
PAPER = (255, 255, 255)


def _sheet(height: int = 300, width: int = 300) -> np.ndarray:
    return np.full((height, width, 3), 255, dtype=np.uint8)


def _clean(sheet: np.ndarray) -> np.ndarray:
    return to_array(remove_annotations(from_array(sheet, FORMAT_RGB)))


def _write_word(sheet: np.ndarray, top: int, left: int, letters: int = 5) -> None:
    """Draw letter-sized blobs side by side on a shared baseline.

    Six pixels wide, eleven tall, two apart: the size and spacing measured from
    the "Shale" and "Brine" labels on the reference sheet.
    """
    for index in range(letters):
        x = left + index * 8
        sheet[top : top + 11, x : x + 6] = BLACK_CURVE


# -- Fills --------------------------------------------------------------------

def test_the_inside_of_a_fill_is_erased() -> None:
    sheet = _sheet()
    sheet[50:250, 80:200] = YELLOW_SAND
    cleaned = _clean(sheet)
    assert tuple(cleaned[150, 140]) == PAPER


def test_the_edge_of_a_fill_is_kept() -> None:
    """The fill's boundary is the curve that bounds it, so it must survive."""
    sheet = _sheet()
    sheet[50:250, 80:200] = YELLOW_SAND
    cleaned = _clean(sheet)
    assert tuple(cleaned[150, 80]) == YELLOW_SAND
    assert tuple(cleaned[150, 199]) == YELLOW_SAND


def test_a_curve_crossing_the_middle_of_a_fill_survives() -> None:
    """The defect this module was rewritten to fix.

    The spontaneous potential curve runs through the yellow sand on the
    reference scan. Erosion alone treated curve plus surrounding fill as one
    thick region and ate the curve, leaving a gap in the middle of the log.
    """
    sheet = _sheet()
    sheet[50:250, 80:200] = YELLOW_SAND
    sheet[50:250, 138:141] = BLACK_CURVE  # a 3 px curve down the fill's middle
    cleaned = _clean(sheet)
    assert tuple(cleaned[150, 139]) == BLACK_CURVE


def test_a_pale_curve_inside_a_fill_survives() -> None:
    """Bulk density's brown is the palest curve on the sheet, at grey 116."""
    sheet = _sheet()
    sheet[50:250, 80:200] = GREY_HYDROCARBON
    sheet[50:250, 138:141] = BROWN_RHOB
    cleaned = _clean(sheet)
    assert tuple(cleaned[150, 139]) == BROWN_RHOB


def test_a_lone_curve_on_paper_is_untouched() -> None:
    sheet = _sheet()
    sheet[20:280, 100:103] = BLACK_CURVE
    cleaned = _clean(sheet)
    assert np.array_equal(cleaned, sheet)


def test_the_colour_sheet_is_required() -> None:
    grey = np.full((100, 100), 255, dtype=np.uint8)
    with pytest.raises(ValueError, match="only distinguishable by"):
        remove_annotations(from_array(grey, "GRAY"))


def test_the_input_is_not_modified() -> None:
    sheet = _sheet()
    sheet[50:250, 80:200] = YELLOW_SAND
    original = sheet.copy()
    _clean(sheet)
    assert np.array_equal(sheet, original)


# -- Labels -------------------------------------------------------------------

def test_a_word_is_erased() -> None:
    sheet = _sheet()
    _write_word(sheet, top=100, left=60)
    cleaned = _clean(sheet)
    assert np.array_equal(cleaned, _sheet())


def test_a_leader_line_is_erased() -> None:
    sheet = _sheet()
    sheet[100:102, 60:150] = BLACK_CURVE  # 90 px long, 2 px tall
    cleaned = _clean(sheet)
    assert np.array_equal(cleaned, _sheet())


def test_a_lone_character_sized_blob_is_kept() -> None:
    """One blob on its own is far more likely to be curve than text.

    Requiring company is what makes this safe to run over a track of dashed and
    dotted curves.
    """
    sheet = _sheet()
    sheet[100:111, 60:66] = BLACK_CURVE
    cleaned = _clean(sheet)
    assert tuple(cleaned[105, 62]) == BLACK_CURVE


def test_a_dashed_curve_is_not_read_as_a_word() -> None:
    """Dashes are stacked down the curve, so they share no baseline.

    This is the single test that stops the label remover from destroying the
    medium resistivity curve, which is printed as an 8-on-4-off dash.
    """
    sheet = _sheet()
    for top in range(20, 280, 12):
        sheet[top : top + 8, 100:102] = BLACK_CURVE
    cleaned = _clean(sheet)
    assert np.array_equal(cleaned, sheet)


def test_a_dotted_curve_running_sideways_is_not_read_as_a_word() -> None:
    """Where a dotted curve steps, its dots do sit side by side.

    They are only 2 px tall, far short of a printed character, which is what
    keeps them.
    """
    sheet = _sheet()
    for left in range(100, 200, 5):
        sheet[150:152, left : left + 2] = BLACK_CURVE
    cleaned = _clean(sheet)
    assert np.array_equal(cleaned, sheet)


# -- Against the real scan ----------------------------------------------------

def test_the_scan_keeps_its_curves(scan_rgb: np.ndarray) -> None:
    """Every track must still hold a full-height run of dark ink afterwards.

    Measured against the source: each track carries at least one curve spanning
    the whole data area, and losing one is the failure this module risks.
    """
    cleaned = _clean(scan_rgb.copy())
    grey = cv2.cvtColor(cleaned, cv2.COLOR_RGB2GRAY)
    data = grey[195:771, :]
    for name, (left, right) in {
        "Track 1": (9, 284),
        "Track 2": (358, 634),
        "Track 3": (636, 911),
    }.items():
        rows_with_ink = ((data[:, left:right] < 135).any(axis=1)).mean()
        assert rows_with_ink > 0.99, f"{name} lost curve ink on some rows"


def test_the_scan_loses_most_of_its_fill(scan_rgb: np.ndarray) -> None:
    """The fills are thousands of pixels; what should remain is only their rims."""
    before = scan_rgb.copy()
    after = _clean(before)
    changed = int((before != after).any(axis=2).sum())
    assert changed > 20_000, f"only {changed} pixels were erased"
