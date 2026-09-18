"""Decode raster bytes from GCS into an in-memory RasterImage.

In : The raw bytes of a JPEG/PNG/TIFF scan, or of a scanned PDF wrapping one,
     as returned by gcs/read_bytes.
Out: A RasterImage holding the decoded PIXEL buffer, plus helpers to move
     between RasterImage and the numpy arrays that OpenCV works on.
Rule: This is the ONLY place that decodes a compressed image, and the only
      place that knows how RasterImage.data is laid out. Every later stage
      moves arrays around with to_array()/from_array() and never re-decodes.
      A PDF is unwrapped by ingest/load_pdf.py and rejoins here, so no stage
      downstream can tell which format the scan arrived in.

      Colour is preserved. Do not convert to greyscale here: the curve
      separation in extract/separate.py distinguishes GR from SP by hue, and
      that information cannot be recovered once it is discarded.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

try:
    from app.contracts import RasterImage
    from app.ingest.load_pdf import is_pdf, rasterise_first_page
except ImportError:
    from contracts import RasterImage
    from ingest.load_pdf import is_pdf, rasterise_first_page

logger = logging.getLogger(__name__)

# RasterImage.format values this module produces and understands. Anything
# else means a stage invented its own convention, which would silently change
# how the buffer is reshaped.
FORMAT_RGB = "RGB"
FORMAT_GRAY = "GRAY"
FORMAT_BINARY = "BINARY"

# Channel count implied by each format. BINARY is single-channel like GRAY but
# is named apart so a stage can assert it received a thresholded image rather
# than a greyscale one.
_CHANNELS_BY_FORMAT = {FORMAT_RGB: 3, FORMAT_GRAY: 1, FORMAT_BINARY: 1}


def load_image(raw: bytes) -> RasterImage:
    """Decode compressed image bytes into a RasterImage in RGB order.

    A scanned log filed as a PDF is accepted here too: the PDF is a wrapper
    around the same raster, so it is rendered to pixels and then follows the
    identical path. Detection is by file signature, not by extension or the
    content type GCS reports, both of which are set by whoever uploaded it.

    Args:
        raw: The encoded file contents — JPEG, PNG, TIFF, or a scanned PDF.

    Returns:
        A RasterImage in FORMAT_RGB whose .data is the raw pixel buffer.

    Raises:
        ValueError: if the bytes are not a decodable image or renderable PDF.
            A scan we cannot open must stop the pipeline here; every later
            stage would otherwise report a different, more confusing symptom.
    """
    if not raw:
        raise ValueError("Cannot decode an image from 0 bytes")

    if is_pdf(raw):
        # Rendered in BGR deliberately, so both input paths converge on the one
        # colour conversion below rather than each doing their own.
        bgr = rasterise_first_page(raw)
    else:
        # imdecode works from memory, so nothing is written to the agent's
        # filesystem. IMREAD_COLOR drops any alpha channel, which a log scan
        # never carries meaningfully.
        buffer = np.frombuffer(raw, dtype=np.uint8)
        bgr = cv2.imdecode(buffer, cv2.IMREAD_COLOR)

        if bgr is None:
            raise ValueError(
                f"OpenCV could not decode {len(raw)} bytes as an image. The "
                "object may be a truncated upload, or not an image at all."
            )

    # OpenCV decodes to BGR. We store RGB because that is what every other
    # library in this project (scikit-image, matplotlib, PIL) assumes, and a
    # silent channel swap would show as green curves turning blue.
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    image = from_array(rgb, FORMAT_RGB)
    logger.info(
        "load_image: OK - %d bytes -> %dx%d %s",
        len(raw),
        image.width,
        image.height,
        image.format,
    )
    return image


def to_array(image: RasterImage) -> np.ndarray:
    """Reconstruct the numpy array held in a RasterImage.

    Returns a (height, width) array for single-channel formats and
    (height, width, 3) for RGB — the shapes OpenCV and scikit-image expect.
    """
    expected = image.height * image.width * image.channels
    if len(image.data) != expected:
        # A mismatch means the buffer and the declared dimensions disagree,
        # which reshape would either refuse or silently misinterpret.
        raise ValueError(
            f"RasterImage buffer is {len(image.data)} bytes but "
            f"{image.height}x{image.width}x{image.channels} needs {expected}"
        )

    flat = np.frombuffer(image.data, dtype=np.uint8)
    if image.channels == 1:
        return flat.reshape(image.height, image.width)
    return flat.reshape(image.height, image.width, image.channels)


def from_array(array: np.ndarray, image_format: str) -> RasterImage:
    """Wrap a numpy array as a RasterImage, checking it matches the format.

    Args:
        array: uint8 pixels, either (h, w) or (h, w, 3).
        image_format: one of FORMAT_RGB, FORMAT_GRAY, FORMAT_BINARY.
    """
    if image_format not in _CHANNELS_BY_FORMAT:
        raise ValueError(
            f"Unknown image format {image_format!r}; expected one of "
            f"{sorted(_CHANNELS_BY_FORMAT)}"
        )

    channels = _CHANNELS_BY_FORMAT[image_format]
    actual_channels = 1 if array.ndim == 2 else array.shape[2]
    if actual_channels != channels:
        raise ValueError(
            f"Array has {actual_channels} channel(s) but format "
            f"{image_format} requires {channels}"
        )

    # Everything downstream indexes pixels as uint8 0-255. A float array would
    # reshape fine and then compare wrongly against every threshold we set.
    if array.dtype != np.uint8:
        raise ValueError(f"Array dtype is {array.dtype}; pixels must be uint8")

    return RasterImage(
        # ascontiguousarray because a cropped or sliced view is not contiguous,
        # and .tobytes() on a non-contiguous view would silently copy in the
        # wrong order.
        data=np.ascontiguousarray(array).tobytes(),
        width=array.shape[1],
        height=array.shape[0],
        channels=channels,
        format=image_format,
    )
