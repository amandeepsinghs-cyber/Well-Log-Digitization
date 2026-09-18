"""Make the real scan available to every image test as a decoded array.

The scan is the only image this pipeline has, so tests assert against it
directly rather than against synthetic stand-ins. A synthetic grid would prove
the code runs; only the real figure proves it works.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.ingest.load_image import load_image, to_array

# The source of truth lives in GCS, but tests must not need network or
# credentials, so they read the local backup copy kept beside the project.
SCAN_PATH = (
    Path(__file__).parents[2].parent / "Well_log_schlum.jpg"
)


@pytest.fixture(scope="session")
def scan_bytes() -> bytes:
    if not SCAN_PATH.exists():
        pytest.skip(f"Source scan not found at {SCAN_PATH}")
    return SCAN_PATH.read_bytes()


@pytest.fixture(scope="session")
def scan_rgb(scan_bytes: bytes) -> np.ndarray:
    """The scan as an (h, w, 3) uint8 RGB array."""
    return to_array(load_image(scan_bytes))
