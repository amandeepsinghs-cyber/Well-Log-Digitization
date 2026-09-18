"""GCS bucket and object path constants for the Log Digitisation Agent.

In : None (standalone definitions).
Out: Canonical bucket name, region, and prefix constants.
Rule: Single source of truth for storage locations. No I/O performed here.
"""

# Primary data bucket, shared with other petrophysics agents (for example the
# A-12 curve-harmonisation agent, which owns the LAS/, raw/, composites/, plots/
# and reports/ prefixes). This agent confines itself to the two prefixes below.
#
# The bucket is in us-central1 while the agent runs in asia-south1. Cross-region
# reads are intentional and accepted: a bucket's location is immutable, and
# collocating the data outweighed collocating the compute.
GCS_REGION: str = "us-central1"
GCS_BUCKET: str = "og-agentic-petrophysics-data"

# Prefix holding raster scans of historical wireline logs awaiting digitisation.
SCANNED_LOGS_PREFIX: str = "Scanned Well Logs/"

# Prefix where digitised CWLS LAS 2.0 outputs are written.
# Name fixed by CHECKLIST.md (steps 13 and 54) - do not rename without
# updating the checklist, or digitised files land where nothing looks for them.
LAS_OUTPUT_PREFIX: str = "Digitised Well Logs/"

# Raster image extensions we treat as digitisable well log scans.
RASTER_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".tif", ".tiff")

# A scanned log is often filed as a PDF wrapper around exactly the same raster.
# Kept apart from RASTER_EXTENSIONS because the two need different decoders:
# ingest/load_pdf.py renders the page before ingest/load_image.py sees pixels.
PDF_EXTENSION: str = ".pdf"

# Every input format the pipeline can digitise. Listing and classification use
# this; anything outside it is not a well log scan as far as this agent is
# concerned, and handing it to the decoder would fail later and less clearly.
DIGITISABLE_EXTENSIONS: tuple[str, ...] = (*RASTER_EXTENSIONS, PDF_EXTENSION)

# Digitised log file extension.
LAS_EXTENSION: str = ".las"


def gcs_uri(object_name: str) -> str:
    """Build a fully qualified gs:// URI for an object in the agent's bucket."""
    return f"gs://{GCS_BUCKET}/{object_name}"


def well_name_from(image_object_name: str) -> str:
    """Derive a well name from the scan's filename.

    A well log sheet does not necessarily print its own well name — the
    reference scan does not, and neither does any textbook figure — so the
    filename a human chose when they uploaded it is the best identifier
    available. This is recorded in the LAS provenance so nobody mistakes it for
    something read off the paper.

    Uppercased with separators collapsed to underscores, which is how well names
    are conventionally written in a LAS ~WELL section.
    """
    basename = image_object_name.rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    cleaned = "".join(
        character if character.isalnum() else "_" for character in stem
    ).strip("_")
    if not cleaned:
        raise ValueError(
            f"Cannot derive a well name from {image_object_name!r}: the filename "
            "has no alphanumeric characters to use."
        )
    return cleaned.upper()


def las_object_name(image_object_name: str) -> str:
    """Object key the digitised LAS for a given scan is written to.

    Defined here, next to well_name_from, because the output filename IS the
    well name: they are one naming decision, and stating it in two places would
    let a file land under a name nothing looks for.
    """
    return f"{LAS_OUTPUT_PREFIX}{well_name_from(image_object_name)}{LAS_EXTENSION}"
