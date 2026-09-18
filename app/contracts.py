"""Data contracts and shared domain models for the Log Digitisation Agent.

In : None (standalone definitions).
Out: Dataclasses defining images, tracks, calibrations, curves, LAS files, QC reports, and A2UI messages.
Rule: This module imports NOTHING from this project. All modules import this for shared contracts.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ==============================================================================
# 1. Raster Image Contracts
# ==============================================================================

@dataclass(frozen=True)
class RasterImage:
    """In-memory representation of a raw or preprocessed well log image.
    
    A well log scan represents depth along the vertical axis (rows, increasing
    downward) and curve values along the horizontal axis (columns).
    """
    # Raw image bytes as read from GCS or processed in memory.
    data: bytes
    # Image dimensions in pixels: width is cross-track span, height is depth span.
    width: int
    height: int
    # Color format (e.g. 'RGB', 'GRAY', 'BINARY') to govern downstream OpenCV operations.
    channels: int
    format: str


# ==============================================================================
# 2. Track Geometry & Axis Calibration Contracts
# ==============================================================================

class ScaleType(str, Enum):
    """Scale type declared in the log header for a curve or track."""
    # Linear scale: equal pixel distances represent equal arithmetic differences (e.g., GR 0-150 gAPI).
    LINEAR = "LINEAR"
    # Logarithmic scale: equal pixel distances represent equal ratios / decades (e.g., RES 0.2-2000 ohm.m).
    LOGARITHMIC = "LOGARITHMIC"


@dataclass(frozen=True)
class TrackBounds:
    """Bounding pixel columns for a single vertical track in a multi-track display.
    
    Standard API/SPWLA well log displays feature 2, 3, or 4 tracks separated by
    heavy vertical boundary lines. Track 1 is typically linear (GR/SP/Caliper),
    Track 2 is logarithmic resistivity, and Track 3 is porosity/density.
    """
    track_index: int       # 1-indexed track number (Track 1, Track 2, Track 3)
    track_name: str        # Descriptive name, e.g. "Track 1", "Track 2 (Resistivity)"
    x_left: int            # Leftmost pixel column of the grid area (inclusive)
    x_right: int           # Rightmost pixel column of the grid area (inclusive)
    scale_type: ScaleType  # LINEAR or LOGARITHMIC


@dataclass(frozen=True)
class GridLines:
    """Pixel positions of the printed rule lines on a log sheet.

    Two populations, and the distinction matters. The HEAVY lines are the sheet's
    structure: the outer frame and the vertical rules dividing one track from the
    next. The LIGHT lines are the reading grid printed inside each track.

    Heavy verticals give the track boundaries. Light horizontals are evenly
    spaced in depth and are what the depth axis is calibrated against — they are
    far more reliable than the printed depth labels, which are nudged around to
    fit on the page.
    """
    # Columns of the heavy vertical rules, left to right.
    track_separators: tuple[int, ...]
    # Rows of the heavy horizontal rules, top to bottom: header dividers and the
    # top and bottom of the data area.
    heavy_horizontals: tuple[int, ...]
    # Rows of the light horizontal reading grid inside the data area.
    depth_grid_rows: tuple[int, ...]
    # Median spacing of depth_grid_rows in pixels. Constant spacing is the
    # evidence that the sheet is undistorted; see calibrate/validate_calibration.
    grid_spacing_px: float


@dataclass(frozen=True)
class ColumnSpan:
    """One vertical column of the sheet, as pure pixel geometry.

    Deliberately separate from TrackBounds, which also carries a ScaleType.
    Region detection can see where a column starts and ends, but it cannot know
    whether that track is linear or logarithmic — that is printed in the header
    and is only known after the header has been read. Putting a scale type on
    this dataclass would force the detector to invent one.
    """
    name: str     # "Track 1", "Track 2", ... or "Depth"
    x_left: int   # Leftmost pixel column, inclusive (the rule that opens it)
    x_right: int  # Rightmost pixel column, inclusive (the rule that closes it)

    @property
    def width(self) -> int:
        return self.x_right - self.x_left


@dataclass(frozen=True)
class PageLayout:
    """How the sheet divides into columns, and which column is which.

    The depth column is NOT necessarily the leftmost. On the reference scan it
    sits between Track 1 and Track 2, so every consumer must read this rather
    than assume an order.
    """
    # Data area: the rows between the header and the bottom frame.
    data_top: int
    data_bottom: int
    # The curve tracks, left to right, excluding the depth column.
    tracks: tuple[ColumnSpan, ...]
    # The column carrying the printed depth labels.
    depth_column: ColumnSpan
    # Rows spanned by the header block, (top, bottom). One band for the whole
    # sheet: each column's header is that band cropped to the column's own
    # x-bounds, which is why the two are derived together. Tracks do not all
    # fill the band — Track 1 holds two curve headers where Track 2 holds three —
    # so the band is sized to the tallest.
    header_rows: tuple[int, int]


@dataclass(frozen=True)
class DepthTick:
    """A reading-grid line that carries a printed depth label.

    The two rows here mean different things and must not be conflated. The
    POSITION of the tick is `grid_row`, taken from the printed reading grid.
    The VALUE of the tick is read from the label between `label_top` and
    `label_bottom`, in header/ocr_header.py.

    They do not coincide. On the reference sheet the 7,300 label is printed
    about 11 px above its own grid line so that it fits inside the frame, and
    the 7,000 label about 14 px below its line for the same reason. Calibrating
    depth against the label's centre instead of the grid line would therefore
    bend the depth scale at both ends of the log.
    """
    grid_row: int      # Row of the reading-grid line this label marks
    label_top: int     # First row of the printed label, inclusive
    label_bottom: int  # Last row of the printed label, inclusive


@dataclass(frozen=True)
class CurveHeaderText:
    """The two lines printed above one curve, exactly as they appear.

    A log header names the curve and then states its scale, e.g. "Gamma Ray"
    over "0  gAPI  150". Both are kept as raw strings: turning "0 gAPI 150" into
    a minimum, a unit and a maximum is header/parse_header.py's job, in ordinary
    deterministic code, so that a model never decides what a scale is.
    """
    track_name: str  # Which ColumnSpan this header sits above, e.g. "Track 2"
    title: str       # Curve name line, e.g. "Resistivity, Deep"
    scale: str       # Scale line, e.g. "0.2  ohm.m  20"


@dataclass(frozen=True)
class SheetText:
    """Everything legible on a log sheet, transcribed and nothing more.

    This is the boundary between the pipeline's one language-model call and all
    the arithmetic that follows. Nothing here has been interpreted, converted or
    reordered, which is what makes the rest of the pipeline auditable: any wrong
    number in the output LAS can be traced to either a misread character here or
    a bug in deterministic code, never to a judgement call in between.
    """
    # One entry per curve header printed on the sheet, in reading order.
    curves: tuple[CurveHeaderText, ...]
    # The depth column's own header, e.g. "Depth, ft" — where the depth unit is.
    depth_header: str
    # The printed depth labels, one per DepthTick and in the same order, e.g.
    # ("7,000", "7,100", "7,200", "7,300"). Positional alignment with the ticks
    # is what pairs a value with a pixel row, so the lengths must match.
    depth_labels: tuple[str, ...]


@dataclass(frozen=True)
class DepthCalibration:
    """Mathematical mapping from vertical pixel row coordinates to measured depth.
    
    In wireline logs, depth increases downward. Row 0 corresponds to the shallowest
    point on the page. Calibration fits a line or spline to detected depth tick marks.
    """
    depth_min: float       # Shallowest measured depth in the display (e.g., 7000.0)
    depth_max: float       # Deepest measured depth in the display (e.g., 7300.0)
    depth_units: str       # Depth measurement unit: 'FT' (feet) or 'M' (meters)
    # Slope (depth units per pixel) and intercept (depth at row 0) from least-squares fit.
    depth_per_pixel: float
    y_origin_depth: float
    # Root-mean-square error of tick marks in depth units to audit calibration quality.
    fit_rmse: float


@dataclass(frozen=True)
class AxisCalibration:
    """Mathematical mapping from horizontal pixel columns to petrophysical values.
    
    Determined from the track header text: curve mnemonic, value range, and scale type.
    """
    mnemonic: str          # SPWLA standardized curve name (e.g., "GR", "ILD", "RHOB")
    unit: str              # Engineering unit (e.g., "gAPI", "OHMM", "G/C3", "V/V")
    value_min: float       # Scale minimum at track's left border (e.g., 0.0 or 0.2)
    value_max: float       # Scale maximum at track's right border (e.g., 150.0 or 2000.0)
    scale_type: ScaleType  # LINEAR or LOGARITHMIC
    track_bounds: TrackBounds


# ==============================================================================
# 3. Curve Samples & Traces
# ==============================================================================

# Standard CWLS LAS 2.0 / SPWLA missing data sentinel.
# When a curve cannot be traced or is off-scale, we emit NULL_VALUE rather than
# fabricating or interpolating data across unobserved intervals.
CWLS_NULL_VALUE: float = -999.25


@dataclass(frozen=True)
class PixelPath:
    """One curve as followed down the page, still in pixel coordinates.

    This is what extract/trace.py produces and what the depth and value
    calibrations are then applied to. Keeping it in pixels means the tracer
    never needs to know what the curve measures or how its axis is scaled, and
    a tracing error can be told apart from a calibration error by looking at
    this one object.

    Both tuples have one entry per depth row of the track's data area, in
    order, so index 0 is the shallowest row. The row a given index refers to is
    therefore PageLayout.data_top + 1 + index.
    """
    # Pixel column the curve was found at on each row, to sub-pixel precision.
    columns: tuple[float, ...]
    # How good the evidence was on each row, 0.0 to 1.0. Zero means the curve
    # was not visible there and the path was carried across the gap on the
    # continuity prior alone; such a row must become NULL in the LAS, never an
    # interpolated value.
    confidence: tuple[float, ...]


@dataclass(frozen=True)
class CurveSample:
    """A single discrete depth-value measurement point for a curve."""
    depth: float           # Measured depth in feet or meters
    value: float           # Measured petrophysical reading, or CWLS_NULL_VALUE if missing
    confidence: float      # Tracing confidence score between 0.0 (untraced/guess) and 1.0 (crisp ink)


@dataclass
class CurveTrace:
    """Continuous traced log curve across a depth interval."""
    mnemonic: str          # Standard curve mnemonic (e.g., "GR", "NPHI", "RHOB")
    unit: str              # Measurement unit (e.g., "GAPI", "V/V", "G/C3")
    description: str       # Full name (e.g., "Gamma Ray", "Neutron Porosity")
    samples: list[CurveSample] = field(default_factory=list)

    @property
    def coverage_fraction(self) -> float:
        """Fraction of depth samples with valid (non-null) readings."""
        if not self.samples:
            return 0.0
        valid_count = sum(1 for s in self.samples if s.value != CWLS_NULL_VALUE)
        return valid_count / len(self.samples)

    @property
    def mean_confidence(self) -> float:
        """Average confidence score across all valid samples."""
        valid_samples = [s for s in self.samples if s.value != CWLS_NULL_VALUE]
        if not valid_samples:
            return 0.0
        return sum(s.confidence for s in valid_samples) / len(valid_samples)


# ==============================================================================
# 4. LAS Document & QC Audit Contracts
# ==============================================================================

@dataclass
class LasDocument:
    """In-memory representation of a standardized CWLS LAS 2.0 well log file."""
    well_name: str
    depth_min: float
    depth_max: float
    depth_step: float
    depth_units: str                      # 'FT' or 'M'
    null_value: float = CWLS_NULL_VALUE   # -999.25
    curves: list[CurveTrace] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    provenance_comments: list[str] = field(default_factory=list)


class FindingSeverity(str, Enum):
    """Severity classification for quality control issues."""
    INFO = "INFO"          # Informational note (e.g. header remarks parsed)
    WARNING = "WARNING"    # Non-blocking defect (e.g. low ink contrast in a 5ft interval)
    ERROR = "ERROR"        # Blocking defect (e.g. impossible rock density > 3.5 g/cm3)


@dataclass(frozen=True)
class QcFinding:
    """A quality sentinel observation validating physical limits or tracing integrity."""
    severity: FindingSeverity
    mnemonic: str          # Associated curve mnemonic or "DEPTH" / "HEADER"
    depth_interval: tuple[float, float]
    message: str           # Plain-language explanation for the petrophysicist


@dataclass
class DigitisationResult:
    """Complete output package from an image digitization run."""
    source_gcs_uri: str    # Originating image URI in gs://...
    output_las_uri: str    # Destination LAS file URI in gs://...
    well_name: str
    depth_range: tuple[float, float]
    las_document: LasDocument
    qc_findings: list[QcFinding] = field(default_factory=list)
    overall_confidence: float = 0.0


# ==============================================================================
# 5. A2UI Frontend Message Contracts
# ==============================================================================

class A2uiCatalogVersion(str, Enum):
    """Gemini Enterprise A2UI catalog version."""
    V0_8 = "v0.8"   # Legacy Lit renderer: uses beginRendering, surfaceUpdate, dataModelUpdate
    V0_9 = "v0.9"   # Active Angular renderer: uses createSurface, updateComponents, updateDataModel


# Active production target determined in Step 3.
# Verified against Google internal codebase (//depot/google3/.../ucs_widget/components/a2ui/v0_9/):
# v0.9 is the active production runtime supporting VegaChart, Canvas, and Material 3.
ACTIVE_A2UI_CATALOG_VERSION: A2uiCatalogVersion = A2uiCatalogVersion.V0_9

# Standard MIME type accepted by Gemini Enterprise for A2UI data payloads.
A2UI_MIME_TYPE: str = "application/json+a2ui"


@dataclass(frozen=True)
class A2uiMessage:
    """A single protocol message emitted to the Gemini Enterprise A2UI surface.
    
    Each lifecycle message is serialized into an independent A2A DataPart envelope.
    """
    message_type: str                  # e.g. "createSurface" or "updateComponents"
    surface_id: str                    # Unique surface identifier per conversation turn
    payload: dict[str, Any]            # Version-specific message contents
    catalog_version: A2uiCatalogVersion = ACTIVE_A2UI_CATALOG_VERSION


@dataclass(frozen=True)
class CurvePlot:
    """One curve, resolved to everything needed to draw it and nothing else.

    This is the handover between track_layout (which decides what goes where)
    and the Vega builders (which decide how it looks). By the time a CurvePlot
    exists, every lookup has already happened: no renderer consults the
    mnemonic table, and no renderer touches a LasDocument.
    """
    mnemonic: str
    unit: str
    description: str
    colour: str                      # Hex, as printed on a paper log
    dash: tuple[int, ...]            # Vega strokeDash; empty means solid
    scale_type: ScaleType
    # The printed scale at the track's left and right edge. For NPHI the min is
    # GREATER than the max: the neutron scale is reversed by convention so that
    # a neutron/density crossover reads as hydrocarbon. Passing these straight
    # through as a Vega domain preserves that reversal with no special case.
    display_min: float
    display_max: float
    # Values are aligned 1:1 with the shared depth index of the TrackPlot's
    # parent chart. None marks a depth the tool did not measure; it becomes
    # JSON null, which Vega-Lite renders as a break in the line rather than a
    # straight segment across an interval nobody logged.
    values: tuple[float | None, ...]


@dataclass(frozen=True)
class TrackPlot:
    """One printed track and the curves sharing it, in draw order."""
    number: int                      # SPWLA track number: 1, 2 or 3
    curves: tuple[CurvePlot, ...]


@dataclass(frozen=True)
class LogPlot:
    """A complete multi-track log display, ready to become a Vega-Lite spec.

    One depth index is shared by every curve on every track. That is what makes
    a single shared y scale legal across the concatenated tracks, and it is why
    CurvePlot carries bare values rather than depth/value pairs.
    """
    well_name: str
    depth_units: str                 # 'FT' or 'M'
    depths: tuple[float, ...]
    tracks: tuple[TrackPlot, ...]


# ==============================================================================
# 6. GCS Storage Inventory Contracts
# ==============================================================================

@dataclass(frozen=True)
class GcsObjectInfo:
    """Metadata for a single object observed in the agent's GCS bucket.

    Populated from a live objects.list call. We deliberately avoid bucket-level
    metadata (buckets.get) because the agent service account is granted
    roles/storage.objectUser, which confers object permissions only.
    """
    name: str                          # Object name relative to bucket root
    size_bytes: int                    # Object size in bytes
    content_type: str                  # MIME type reported by GCS, e.g. 'image/jpeg'
    updated: str | None = None      # ISO 8601 last-modified timestamp, if available

    @property
    def size_kib(self) -> float:
        """Object size expressed in kibibytes."""
        return self.size_bytes / 1024.0

    @property
    def basename(self) -> str:
        """Trailing filename component, excluding any prefix directories."""
        return self.name.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class BucketInventory:
    """Result of scanning the agent's bucket for digitisable inputs and LAS outputs.

    'ok' distinguishes a genuine empty bucket from a failed scan. When a scan
    fails (permissions, network, region outage), 'error' carries the reason so
    the UI can surface an honest diagnostic rather than an empty success state.
    """
    bucket: str
    region: str
    scans: list[GcsObjectInfo] = field(default_factory=list)
    las_files: list[GcsObjectInfo] = field(default_factory=list)
    ok: bool = True
    error: str | None = None

    @property
    def total_objects(self) -> int:
        """Count of all catalogued objects across both categories."""
        return len(self.scans) + len(self.las_files)

    @property
    def total_bytes(self) -> int:
        """Aggregate size in bytes across all catalogued objects."""
        return sum(o.size_bytes for o in self.scans) + sum(
            o.size_bytes for o in self.las_files
        )

