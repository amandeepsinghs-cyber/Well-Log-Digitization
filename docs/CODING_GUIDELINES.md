# Coding Guidelines — Log Digitisation Agent

Binding rules for every file in this project. Applies to me when writing, and to you when reviewing.

Companions: [BUILD.md](./BUILD.md) (the why) · [CHECKLIST.md](./CHECKLIST.md) (the do).

---

## The four rules

| # | Rule | Test |
|---|---|---|
| **1** | **Comment for readability and explainability** | A petrophysicist who doesn't write Python can read the file top to bottom and follow what happens |
| **2** | **Only write code that does something** | Delete any line and something breaks. If nothing breaks, it shouldn't have been written |
| **3** | **Keep it to the point and operational** | The file does its one job, runs today, and has no scaffolding for a job it might do later |
| **4** | **Use a library wherever one exists — never reinvent the wheel** | Before writing an algorithm, name the established library that already does it. Hand-rolling is a decision that must be justified in a comment |

---

## 1. Comments

**Every non-trivial line or block says what it does and why.** Comments carry the domain reasoning
that the code cannot express — pixel geometry, log conventions, SPWLA rules, Vega quirks.

- **File header docstring** — what this file does, what goes in, what comes out, in three lines.
- **Block comments** for each logical step of a function.
- **Inline comments** on any line whose *intent* isn't obvious from the syntax — constants, magic
  numbers, unit conversions, index arithmetic, ordering that matters.
- **Explain the domain, not the syntax.** `# increment i` is noise. `# depth increases downward, so
  row 0 is the shallowest sample` is the point.
- **Every magic number is justified.** No bare `0.7` — say what it is and where it came from.
- **Record the trap.** Where something failed before or is a known pitfall, the comment says so.

```python
# BAD — restates the syntax, explains nothing
i = i + 1  # add one to i

# GOOD — explains the domain
# Depth increases downward in a well log, so image row 0 is the shallowest
# sample. Iterating top-to-bottom therefore walks the log shallow → deep.
for row in range(image.height):
```

---

## 2. Only write code that does something

Do **not** write:

- `TODO` stubs, `pass` bodies, `NotImplementedError` placeholders
- Config flags with one possible value, or options nothing sets
- Abstract base classes, plugin registries, or factories with one implementation
- Getters and setters over plain attributes
- `try/except` that catches, logs, and re-raises unchanged
- Defensive checks for conditions the caller structurally cannot produce
- Unused parameters, imports, or "might need it later" helpers
- Wrapper functions that only forward arguments

**If a feature isn't needed for the current checklist step, it isn't written.**

---

## 3. To the point and operational

- **One file, one responsibility** — the filename states it.
- **`[mod]` files are pure**: take data, return data. No GCS calls, no globals, no printing, no
  imports from other modules in this project except `contracts.py`.
- **`[int]` files call and pass only** — no business logic in the wiring.
- **Short functions.** If it needs section-header comments to be followable, split it.
- **Type-hint every signature**; dataclasses from `contracts.py` for anything structured.
- **Fail loudly.** Raise with the actual values in the message. Never swallow an error and return a
  default — a silent fallback becomes a wrong number in front of a customer.
- **No hardcoded results.** Never return a fixed value where a computation is implied. If it can't
  be computed, raise or report a gap.
- **No print debugging left behind.** Use the logger or remove it.

---

## 4. Use a library — never reinvent the wheel

**Before writing an algorithm, name the library that already does it.** A hand-rolled parser is
three files of someone else's solved problem, re-solved worse. It also breaks rule 2: that code
exists, but it doesn't need to.

### The designated stack for this project

| Job | Library | Never hand-roll |
|---|---|---|
| LAS read / write | **`lasio`** | Section parsing, wrapped data, `NULL` handling, header quirks |
| Well/curve convenience | **`welly`** | Depth indexing, unit bookkeeping |
| Image geometry, gridlines, homography | **`OpenCV`** | Hough transform, perspective warp, morphology |
| Image filtering, thresholding | **`scikit-image`** | Adaptive threshold, deskew, denoise |
| Numerics, fitting, interpolation | **`numpy` / `scipy`** | Least squares, resampling, signal filtering |
| GCS | **`google-cloud-storage`** | Never hand-build REST calls or URLs |
| Agent surface, A2A | **ADK + `agents-cli` scaffold** | Never hand-write the A2A surface |
| Vega validation | **the real Vega-Lite compiler** | Never eyeball a spec and assume it parses |

### When hand-rolling is allowed

Only for things genuinely specific to us, and the comment must say why:

- The **SPWLA mnemonic table** — domain data, not an algorithm.
- The **Vega spec builders** — our three-track layout is not a library's.
- The **A2UI envelope** — the wire format is GE-specific.
- **Thin adapters** that convert a library's object into our `contracts.py` dataclass.

### The rule in practice

```python
# BAD — re-implementing a solved problem
def parse_las(text: str) -> dict:
    for line in text.splitlines():   # 200 lines of section-state machine ahead
        ...

# GOOD — lean on the library, own only the conversion
# lasio already handles ~sections, wrapped data, malformed headers and the
# -999.25 NULL convention. We only map its object onto our dataclass.
import lasio

def parse_las(text: str) -> LasDocument:
    las = lasio.read(io.StringIO(text))
    ...
```

> [!IMPORTANT]
> Adding a dependency is cheaper than owning an algorithm. If a library covers 80% of the job,
> use it for that 80% and write only the remaining 20%.

---

## Worked example — the shape every `[mod]` file takes

```python
"""Convert a pixel column position into a log value on a LOGARITHMIC track.

In : x pixel, the track's pixel bounds, and its header-declared value range.
Out: the curve value in track units.
Used for resistivity tracks, which are plotted on a log decade scale.
"""

import math

from contracts import TrackBounds


def pixel_to_log_value(x_pixel: float, bounds: TrackBounds) -> float:
    # Resistivity tracks are plotted so that equal pixel distances represent
    # equal RATIOS, not equal differences. Interpolating linearly in pixel
    # space is therefore only valid in log10 space.
    span_px = bounds.x_right - bounds.x_left
    if span_px <= 0:
        # A non-positive span means track detection produced garbage bounds.
        # Calibrating on it would silently fabricate values, so stop here.
        raise ValueError(f"Track has non-positive pixel width: {bounds!r}")

    # Fraction of the way across the track, 0.0 at the left edge, 1.0 at the right.
    fraction = (x_pixel - bounds.x_left) / span_px

    # Interpolate between the decade exponents, then undo the log to get the value.
    log_min = math.log10(bounds.value_min)
    log_max = math.log10(bounds.value_max)
    return 10 ** (log_min + fraction * (log_max - log_min))
```

What makes it compliant: the docstring states in/out/purpose · the comment explains *log-scale
petrophysics*, not Python · the guard raises with the offending value instead of returning a
plausible-looking default · there is nothing in the file that isn't used.

---

## Review question

Before any file is marked done:

> **Can you read this file and say what it does, why each step is there, and what would break if you
> deleted any line?**

If no, it isn't finished.
