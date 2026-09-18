"""Tests for image decoding and the RasterImage buffer round trip.

Two things must hold, and every later stage depends on both:

1. Colour survives. Curve separation tells GR from SP by hue, so a decoder that
   quietly greyscaled would break extraction with no visible error here.
2. The buffer round-trips exactly. Every stage reshapes RasterImage.data, and a
   channel-order or contiguity slip would show up as scrambled pixels far from
   the cause.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from app.ingest.load_image import (
    FORMAT_BINARY,
    FORMAT_GRAY,
    FORMAT_RGB,
    from_array,
    load_image,
    to_array,
)

# -- The real scan ------------------------------------------------------------

def test_decodes_the_real_scan(scan_bytes: bytes) -> None:
    image = load_image(scan_bytes)

    assert image.format == FORMAT_RGB
    assert image.channels == 3
    # Roughly 916x775; asserted as a range so a re-scan at another size does not
    # fail the test, while a wildly wrong decode still does.
    assert 800 < image.width < 1000
    assert 700 < image.height < 900


def test_the_scan_keeps_its_colour(scan_rgb: np.ndarray) -> None:
    """GR is green and RHOB brown. If decode greyscaled, R==G==B everywhere."""
    red, green, blue = scan_rgb[:, :, 0], scan_rgb[:, :, 1], scan_rgb[:, :, 2]
    coloured = np.count_nonzero((red != green) | (green != blue))

    # The figure is mostly white paper, so only a small minority of pixels carry
    # colour. A few thousand is plenty to separate curves by; zero means the
    # channel information is gone.
    assert coloured > 1000, "decoded image has no colour — curve separation would fail"


def test_channel_order_is_rgb_not_bgr(scan_rgb: np.ndarray) -> None:
    """OpenCV decodes BGR; storing it unswapped turns green curves blue.

    The scan's fills include a large orange/red gas block, so red-dominant
    pixels must outnumber blue-dominant ones. Under BGR the comparison inverts.
    """
    red = scan_rgb[:, :, 0].astype(int)
    blue = scan_rgb[:, :, 2].astype(int)

    red_dominant = np.count_nonzero(red > blue + 40)
    blue_dominant = np.count_nonzero(blue > red + 40)
    assert red_dominant > blue_dominant


# -- Round trip ---------------------------------------------------------------

def test_round_trip_preserves_every_pixel(scan_rgb: np.ndarray) -> None:
    restored = to_array(from_array(scan_rgb, FORMAT_RGB))
    np.testing.assert_array_equal(restored, scan_rgb)


def test_round_trip_of_a_non_contiguous_crop() -> None:
    """A cropped view is not contiguous; tobytes() on it would reorder pixels."""
    full = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    crop = full[1:3, 2:5]  # a view, not a copy
    assert not crop.flags["C_CONTIGUOUS"]

    np.testing.assert_array_equal(to_array(from_array(crop, FORMAT_RGB)), crop)


@pytest.mark.parametrize("image_format", [FORMAT_GRAY, FORMAT_BINARY])
def test_single_channel_round_trip(image_format: str) -> None:
    array = np.array([[0, 128, 255], [255, 0, 128]], dtype=np.uint8)
    restored = to_array(from_array(array, image_format))

    assert restored.shape == (2, 3), "single-channel images stay 2-D"
    np.testing.assert_array_equal(restored, array)


# -- Failing loudly -----------------------------------------------------------

def test_empty_bytes_raise() -> None:
    with pytest.raises(ValueError, match="0 bytes"):
        load_image(b"")


def test_undecodable_bytes_raise_here_not_later() -> None:
    """Bytes that are neither an image nor a PDF must stop at the decoder."""
    with pytest.raises(ValueError, match="could not decode"):
        load_image(b"\x00\x01\x02 this is not an image")


def test_bytes_claiming_to_be_a_pdf_but_broken_fail_as_a_pdf() -> None:
    """The PDF branch is chosen by signature, so it must own the error too."""
    with pytest.raises(ValueError, match="Could not open"):
        load_image(b"%PDF-1.4 this is not a real PDF")


def test_a_scanned_pdf_loads_like_an_image() -> None:
    """A log filed as a PDF must reach the pipeline as ordinary RGB pixels.

    The colours are asserted, not just the shape: load_pdf hands back BGR and
    load_image converts it, so a regression there would show as a scan whose
    red curves had turned blue rather than as an exception.
    """
    page = Image.new("RGB", (400, 300), (255, 0, 0))
    page.paste(Image.new("RGB", (60, 60), (0, 0, 255)), (100, 100))
    buffer = io.BytesIO()
    page.save(buffer, "PDF", resolution=72.0)

    image = load_image(buffer.getvalue())

    assert image.format == "RGB"
    # Rendered at the embedded raster's own resolution, not resampled.
    assert image.width == 400, "a scanned page keeps its own pixels"
    assert image.height == 300

    pixels = to_array(image)
    # PDF colour conversion shifts values by a unit or two, so assert the
    # dominant channel rather than an exact triple.
    background = pixels[10, 10]
    assert background[0] > 200 and background[2] < 50, "background stays red"
    # The patch was pasted at (100, 100) and is 60 px square.
    patch = pixels[130, 130]
    assert patch[2] > 200 and patch[0] < 50, "the blue patch stays blue"


def test_unknown_format_raises() -> None:
    array = np.zeros((2, 2), dtype=np.uint8)
    with pytest.raises(ValueError, match="Unknown image format"):
        from_array(array, "CMYK")


def test_channel_count_must_match_the_format() -> None:
    rgb = np.zeros((2, 2, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="requires 1"):
        from_array(rgb, FORMAT_GRAY)


def test_non_uint8_pixels_raise() -> None:
    """A float array reshapes fine and then fails every threshold comparison."""
    array = np.zeros((2, 2), dtype=np.float32)
    with pytest.raises(ValueError, match="must be uint8"):
        from_array(array, FORMAT_GRAY)


def test_a_buffer_that_disagrees_with_its_dimensions_raises() -> None:
    """Catches a stage that rewrote .data without updating width/height."""
    image = from_array(np.zeros((4, 4), dtype=np.uint8), FORMAT_GRAY)
    corrupted = type(image)(
        data=image.data[:-1],
        width=image.width,
        height=image.height,
        channels=image.channels,
        format=image.format,
    )
    with pytest.raises(ValueError, match="needs 16"):
        to_array(corrupted)
