"""Render a scanned PDF page to pixels so the raster pipeline can digitise it.

In : The raw bytes of a PDF, as returned by gcs/read_bytes.
Out: A BGR numpy array of page 1 — the same colour order cv2.imdecode produces,
     so ingest/load_image.py converts to RGB in exactly one place.
Rule: A scanned well log PDF is a raster in a wrapper. This module unwraps it
      and nothing more. It does NOT read vector path objects: a born-digital
      PDF stores its curves as geometry, which would digitise far more
      accurately, but that is a separate extraction backend and not what this
      does. A vector PDF rendered here is treated as a picture of a log.
"""

from __future__ import annotations

import logging

import numpy as np
import pypdfium2 as pdfium

logger = logging.getLogger(__name__)

# PDF files begin with this signature. Sniffed rather than trusting the object's
# extension or GCS content type, either of which can be wrong on a file a human
# uploaded through a console.
PDF_MAGIC: bytes = b"%PDF-"

# Width to render to when the page's own resolution cannot be used — either the
# page is not a single scanned raster, or that raster is too large to feed the
# pipeline as-is. The extraction thresholds in extract/separate.py and the
# line-thickness heuristics in preprocess/remove_annotations.py were tuned
# against a 919 px wide sheet, so this sits deliberately close to it.
TARGET_WIDTH_PX: int = 1000

# Above this width we downscale to TARGET_WIDTH_PX. A 300 dpi US Letter scan is
# ~2550 px; handing that straight to the pipeline would shift every coverage
# figure for reasons nothing in the logs would explain.
MAX_WIDTH_PX: int = 2000

# Bounds on the render scale, as a multiple of the PDF's own 72 dpi units.
# Below the floor a page is being rendered smaller than a usable scan; above the
# ceiling we are inflating a thumbnail into a blur and spending hundreds of
# megabytes doing it.
_MIN_SCALE: float = 0.25
_MAX_SCALE: float = 12.0


def is_pdf(raw: bytes) -> bool:
    """True if these bytes are a PDF, judged by the file signature."""
    return raw.startswith(PDF_MAGIC)


def rasterise_first_page(
    raw: bytes, target_width: int = TARGET_WIDTH_PX
) -> np.ndarray:
    """Render page 1 of a PDF to a BGR pixel array.

    A scanned PDF is a raster in a wrapper, so where that raster can be
    identified the page is rendered at ITS resolution and the pixels come back
    unresampled — byte-for-byte the digitisation problem we would have had if
    the same scan had been uploaded as a JPEG.

    Args:
        raw: the encoded PDF file contents.
        target_width: width to fall back to when the page's own resolution
            cannot be used. The aspect ratio is preserved either way.

    Returns:
        A (height, width, 3) uint8 array in BGR order.

    Raises:
        ValueError: if the PDF cannot be opened, is empty, reports a
            zero-width page, or holds more than one page. A multi-page file is
            refused rather than silently digitised page-by-page: returning
            page 1 of five and labelling it 'the well log' would publish a LAS
            that quietly omits four fifths of the well.
    """
    if not raw:
        raise ValueError("Cannot render a PDF from 0 bytes")

    try:
        document = pdfium.PdfDocument(raw)
    except Exception as exc:
        raise ValueError(
            f"Could not open {len(raw)} bytes as a PDF: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        page_count = len(document)
        if page_count == 0:
            raise ValueError("PDF contains no pages")
        if page_count > 1:
            raise ValueError(
                f"PDF has {page_count} pages. This agent digitises one log sheet "
                "per file; split the PDF and submit the page holding the log."
            )

        page = document[0]
        width_points, height_points = page.get_size()
        if width_points <= 0 or height_points <= 0:
            raise ValueError(
                f"PDF page 1 reports a {width_points}x{height_points} point size, "
                "which cannot be rendered."
            )

        width = _render_width(page, target_width)

        # PDF coordinates are in points at 72 per inch, so scale is simply the
        # ratio of the pixels we want to the points the page declares.
        scale = width / width_points
        if not _MIN_SCALE <= scale <= _MAX_SCALE:
            raise ValueError(
                f"Rendering a {width_points:.0f}x{height_points:.0f} point page at "
                f"{width} px needs a scale of {scale:.2f}, outside the supported "
                f"{_MIN_SCALE}-{_MAX_SCALE} range. The page is either a thumbnail "
                "or a plotter-sized sheet."
            )

        bitmap = page.render(scale=scale)
        # to_numpy() hands back BGR, matching cv2.imdecode. Copied because the
        # array is a view onto the bitmap's buffer, which is freed when the
        # document closes below.
        pixels = np.ascontiguousarray(bitmap.to_numpy()[:, :, :3])
    finally:
        document.close()

    logger.info(
        "load_pdf: OK - %d bytes -> page 1 at %dx%d px",
        len(raw),
        pixels.shape[1],
        pixels.shape[0],
    )
    return pixels


def _render_width(page, target_width: int) -> int:
    """Choose the pixel width to render at, preferring the page's own raster.

    Resampling a scan is not free. Upscaling a 919 px sheet to 1000 px moved the
    depth grid enough that a header text block fell within half a grid pitch of
    a rule and was mistaken for a depth label, which failed the whole sheet.
    Rendering at the embedded raster's own size avoids resampling entirely.

    Never upscales: a page whose raster is smaller than target_width is rendered
    at its own size rather than inflated, because interpolated pixels would give
    the tracer detail the scanner never captured.
    """
    native = _embedded_raster_width(page)
    if native is None:
        # Vector content, or several images composited. There is no single
        # native resolution to honour, so the tuned width is the safer choice.
        return target_width
    if native > MAX_WIDTH_PX:
        logger.info(
            "load_pdf: embedded raster is %d px wide, downscaling to %d",
            native,
            target_width,
        )
        return target_width
    return native


def _embedded_raster_width(page) -> int | None:
    """Pixel width of the page's raster, if the page is exactly one image.

    Exactly one, deliberately. A page carrying a scan plus a stamp, or two
    half-page images, has no single native resolution, and picking one of them
    would silently resample the other.
    """
    images = [
        obj for obj in page.get_objects() if isinstance(obj, pdfium.PdfImage)
    ]
    if len(images) != 1:
        return None

    try:
        return int(images[0].get_metadata().width)
    except Exception as exc:
        # Metadata is optional in the PDF spec; fall back rather than fail.
        logger.info("load_pdf: could not read image metadata (%s)", exc)
        return None
