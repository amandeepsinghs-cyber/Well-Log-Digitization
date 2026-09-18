# Log Digitisation Agent — Build Checklist

Companion to [BUILD.md](./BUILD.md). **BUILD.md is the *why*. This file is the *do*.**

Tick each box as it completes. When the last box is ticked, the agent is deployed, registered in
Gemini Enterprise, and answering.

| | |
|---|---|
| **Project** | `og-agentic-ecosystem` |
| **Agent region** | `asia-south1` (Mumbai, India) |
| **Bucket** | `og-agentic-petrophysics-data` — **`us-central1`**, shared with other petrophysics agents |
| **Service account** | `log-digitiser-agent@og-agentic-ecosystem.iam.gserviceaccount.com` |
| **Source** | `gs://og-agentic-petrophysics-data/Scanned Well Logs/Well_log_schlum.jpg` |
| **Output** | `gs://og-agentic-petrophysics-data/Digitised Well Logs/` |
| **Total steps** | 8 prerequisites + 60 active build *(4 deferred or merged away)* + 4 added (9b, 9c, 44b, 51b) + 9 release = **81** |

> [!NOTE]
> **Bucket decision, 2026-09-17.** A Mumbai bucket (`…-data-asia-south1`) was created
> mid-build for data residency, then reverted: this project now uses the original shared
> US bucket. A bucket's location is immutable, so the Mumbai bucket still exists — it is
> **unused and retained pending a decision**, not deleted.
>
> Consequences of sharing, both handled:
> - The agent **confines itself to two prefixes**, `Scanned Well Logs/` and
>   `Digitised Well Logs/`. The A-12 curve-harmonisation agent owns `LAS/`, `raw/`,
>   `composites/`, `plots/` and `reports/`. `scan_bucket_inventory()` lists per prefix;
>   an earlier whole-bucket listing wrongly reported 7 scans and 20 LAS files by
>   picking up A-12's data.
> - Compute is in `asia-south1` and storage in `us-central1`. Cross-region reads are
>   accepted deliberately.
>
> This makes P8 (audit logging) load-bearing rather than precautionary — it is the only
> control that detects one agent straying into another's prefixes.

> [!IMPORTANT]
> **Where we are — 2026-09-18. The full conversation is built; three live defects found and fixed; ready to redeploy.**
> Phases 0–4 complete; **Gates 0, 1a, 2, 3 and C all passed**. The agent reads an unseen log
> sheet, traces its curves, and writes a real CWLS LAS 2.0 file to the bucket **under its own
> service account**. That file renders: the digitised LAS compiles to a three-track Vega-Lite
> chart that the real Vega engine draws correctly, and the A2UI payload validates against the
> live Gemini Enterprise catalog. A scanned log filed as a **PDF** is accepted as readily as an
> image, and the scan **itself** can now be displayed, not merely described.
>
> **The first live GE run found three faults that no offline test could have caught.** The
> chart failed because `updateDataModel` used the key `data` where A2UI requires `value`; the
> model fabricated A2UI payloads into its own prose by copying the ones it could see in its
> context; and there was no way to show a scan at all. All three are fixed under steps 26a–26e,
> each with measured evidence. The lifecycle envelope now has the schema test whose absence let
> the first one through.
>
> | | |
> |---|---|
> | Last completed | **Steps 26a–26e** — the three GE render faults, plus the measured payload cap. Previously: step 26 DEPLOYED 2026-09-18 09:16 UTC; steps 18–25d built the render path and the conversational workflow |
> | Next | **Redeploy**, then **GATE 1** — the six-turn conversation, judged by you in Gemini Enterprise chat |
> | Tests | **485 unit tests pass** (`uv run pytest tests/unit`) |
> | Blocker | **Gate 1 can only be judged by you, in the Gemini Enterprise chat surface.** Turn 3 — whether GE's CSP permits a `data:` image — is the one thing that cannot be measured offline |
>
> Every stage has been run against the real scan and its output inspected as an image, not just
> asserted on: see the Gate 2 note, step 44b, the Gate 3 note, GATE C condition 3, and the two
> silent-render defects recorded under step 22.
>
> **From here everything is delivery, not discovery.** The remaining work makes the product
> reachable from Gemini Enterprise rather than from a terminal.

> [!NOTE]
> **Progress — regenerate with `grep -c '^- \[x\]' CHECKLIST.md` and `grep -c '^- \[ \]' CHECKLIST.md`.**
> Counts include each phase's gate line as one item. Ticks are per *work increment*, so a merged
> step such as **42+43** counts once, not twice.
>
> | Phase | Items | Done | |
> |---|---|---|---|
> | D · Decisions | 3 | 2 | D3 (commit to repo?) still open — needs *you* |
> | P · Prerequisites | 9 | 9 | ✅ |
> | 0 · Wire-format spike | 12 | 12 | ✅ Gate 0 |
> | 1 · GCS + LAS domain | 9 | 9 | ✅ Gate 1a |
> | 2 · Image + calibration | 14 | 14 | ✅ Gate 2 |
> | 3 · Curve extraction | 5 | 5 | ✅ Gate 3 |
> | 4 · LAS write | 5 | 5 | ✅ **GATE C — core offering delivered** |
> | **1b · Display + workflow** | **20** | **18** | **◀ GE faults fixed; Gate 1 + fixture delete remain** |
> | 5 · QC and audit | 6 | 0 | where curve tuning gets measured |
> | 6 · Interaction loop | 3 | 0 | |
> | R · Release | 10 | 0 | ends at AGENT LIVE |
> | **In-scope total** | **96** | **74** | **77%** |
> | *Deferred (Phase 7)* | *9* | *0* | *post-launch, excluded from the total* |
>
> **To AGENT LIVE — the full deployed product: 77%.** The remaining 23% is Gate 1, the QC
> surface, the interaction loop and release. **None of it is discovery** — the product works
> today from a terminal, it renders correctly under the real Vega engine, and the hard problems
> (colour and dash separation, logarithmic calibration, tracing through crossings) are all
> behind us.


> [!IMPORTANT]
> **Reordering decision, 2026-09-18 — the core offering comes first.**
>
> The original plan built the display path (steps 18–26) before the digitiser. That put
> **every genuinely uncertain part of the project last**: colour and dash separation,
> logarithmic calibration, and tracing curves through crossings. It also meant the renderer
> would first be proved against a LAS we hand-wrote ourselves — scaffolding inside
> scaffolding.
>
> **The product is image → LAS.** Everything else is delivery of that. So:
>
> | Order | What | Ends at |
> |---|---|---|
> | ~~1~~ ✅ | Phase 0 · A2UI handshake | Gate 0 |
> | ~~2~~ ✅ | Phase 1 · GCS + LAS domain layer (10–17) | Gate 1a |
> | **3 ◀ NOW** | **Phases 2–4 · scan → LAS** (27–55, plus new 44b and 51b) | **GATE C — the core offering** |
> | 4 | Phase 1b · display (18–26, 56) | Gate 1 *(absorbs the old Gate 4)* |
> | 5 | Phases 5–6 · QC, audit, interaction (57–64) | Gates 5 and 6 |
> | 6 | Phase R · release | AGENT LIVE |
>
> **Step numbers are unchanged** — BUILD.md and the notes throughout this file reference
> them, so only the running order moved. Two steps are new: **44b** (`remove_annotations.py`)
> and **51b** (`qc_plot.py`), both added after actually opening the source scan.
>
> What the old ordering was buying — being able to *see* whether extraction worked — is now
> bought by step 51b, a throwaway matplotlib PNG, instead of nine steps of Vega and A2UI work.

> [!IMPORTANT]
> **Consolidation decision, 2026-09-18 — 27 steps become 17 files.**
>
> A review of Phases 2–5 found the one-file-per-step rule applied by habit rather than by
> judgement. **The test is now:** *split where each half has a contract worth asserting on and
> can fail independently; merge where splitting forces a lossy interface.*
>
> | Merge | Files | Why |
> |---|---|---|
> | **48 + 49** | `extract/trace.py` | **A design error, not a preference.** At a crossing, choosing between candidate pixels *is* the continuity prior. A tracer returning one value per row has already discarded what the prior needs, so continuity could never have been a pass over its output. |
> | 42 + 43 | `calibrate/value_axis.py` | Linear and log are two branches of one function, dispatched on a `ScaleType` we already have |
> | 35 + 37 | `detect/regions.py` | One geometric decomposition of the page |
> | 39 + 40 | `header/parse_header.py` | Both parse one header string |
> | 46 + 47 | `extract/separate.py` | One question — which pixels belong to which curve — two routes |
> | 50 + 51 | `extract/clean.py` | Both pure 1-D operations on a finished trace |
> | 31 → 33 | `detect/gridlines.py` | Binarisation is an input stage of Hough, not a pipeline step |
> | 57 → 59 | `qc/confidence.py` | `coverage_fraction` already exists on `CurveTrace` |
> | 13 + 27 | `gcs/list_objects.py` | The two listings differ only by prefix and extension |
>
> **Deliberately NOT merged:** 32 `remove_grid` and 44b `remove_annotations` — same contract,
> but different algorithms and different inputs, and when a curve goes missing you need to know
> which of the two ate it. 41 `depth_axis` stays out of 42+43 — a least-squares fit over measured
> ticks is not an interpolation between two declared bounds.
>
> **Also fixed:** step 32 was ordered *before* the step 33 gridline detection it consumes.
> **Also deferred:** steps 29, 30 and 34 — see the warning in Phase 2.

**Who** column: **You** = run it in your terminal / browser. **Me** = I do it, you review.

---

## Phase D — Decisions (blocks everything)

- [x] **D1** · *You* · Project directory location — `Log Digitisation/log-digitiser/`
- [x] **D2** · *You* · Delete the unused prototype `Oil & Gas Agent Portfolio/log-digitiser-agent/`? (Deleted)
- [ ] **D3** · *You* · Commit `Well_log_schlum.jpg` + `Log Digitisation/` to the slidedeck repo? (y/n)

---

## Phase P — Prerequisites

*Environment only. No application code.*

- [x] **P1** · *You* · Authenticate — both user creds and ADC
  ```bash
  gcloud auth login
  gcloud auth application-default login
  ```
  ✅ `gcloud auth list` shows an active account (`admin@amandeepsinghs.altostrat.com`)

- [x] **P2** · *Me* · Set the project
  ```bash
  gcloud config set project og-agentic-ecosystem
  ```
  ✅ `gcloud config get-value project` returns `og-agentic-ecosystem`

- [x] **P3** · *Me* · Verify bucket access
  ```bash
  gcloud storage ls gs://og-agentic-petrophysics-data/
  ```
  ✅ Bucket lists without error

- [x] **P4** · *Me* · **Copy the scanned log into the bucket** — source of truth moves to GCS
  ```bash
  gcloud storage cp \
    "/usr/local/google/home/amandeepsinghs/O&G_slidedeck_agentic_transformation/Oil & Gas Agent Portfolio/agent_ideas/P04_Petrophysicist/Log Digitisation/Well_log_schlum.jpg" \
    "gs://og-agentic-petrophysics-data/Scanned Well Logs/"
  ```
  ✅ Upload reports success
  *From this point the local copy is a backup only. The agent reads **only** from GCS — it never
  touches the local filesystem.*

- [x] **P5** · *Me* · Verify the object landed
  ```bash
  gcloud storage ls -l "gs://og-agentic-petrophysics-data/Scanned Well Logs/"
  ```
  ✅ `Well_log_schlum.jpg` listed with 52,597 bytes

- [x] **P6** · *Me* · Create the agent's own service account
  ```bash
  gcloud iam service-accounts create log-digitiser-agent \
    --display-name="Log Digitisation Agent"
  ```
  ✅ SA exists: `log-digitiser-agent@og-agentic-ecosystem.iam.gserviceaccount.com`

- [x] **P7** · *Me* · Grant bucket-level object read/write to that SA
  ```bash
  gcloud storage buckets add-iam-policy-binding gs://og-agentic-petrophysics-data \
    --member=serviceAccount:log-digitiser-agent@og-agentic-ecosystem.iam.gserviceaccount.com \
    --role=roles/storage.objectUser
  ```
  ✅ `gcloud storage buckets get-iam-policy` shows `roles/storage.objectUser` bound to SA

- [x] **P8** · *Me* · Enable GCS Data Access audit logs (`DATA_READ`, `DATA_WRITE`, `ADMIN_READ`)
  ✅ `gcloud projects get-iam-policy og-agentic-ecosystem` shows `auditConfigs` for
  `storage.googleapis.com` with DATA_READ, DATA_WRITE, ADMIN_READ

> [!IMPORTANT]
> **Gate P** — authenticated · bucket readable · **source image uploaded and verified** · SA created ·
> audit logging on.
>
> Audit logs are the *only* control that detects a rogue agent under the shared-bucket decision.
> Do not skip P8.

- [x] **GATE P PASSED**

---

## Architecture & Integration Layers Map

*Listed in **execution order**, which since 2026-09-18 is no longer the same as phase number.*

| Order | Phase | Purpose | Deliverable Files | What It Does & Why We Split It |
|---|---|---|---|---|
| 1 ✅ | **Phase 0** | **Protocol Handshake Spike** | • `contracts.py` (2)<br>• `render/a2ui_envelope.py` (4)<br>• `render/a2ui_lifecycle.py` (5)<br>• `integration/agent_card.py` (6)<br>• `integration/executor.py` (7)<br>• Minimal `integration/agent.py` (9) | **The Container Handshake**: advertises to Gemini Enterprise *"I support A2UI v0.9."* Proved the chat interface won't strip UI components before any product code was written. |
| 2 ✅ | **Phase 1** | **GCS + LAS Domain Layer** | • `gcs/` paths, client, read, list (10–13)<br>• `las/mnemonics.py` (15)<br>• `las/parse_las.py` (16)<br>• `las/validate_las.py` (17) | **The Data Foundation**: reads the bucket under the agent's own SA, and parses and structurally validates CWLS LAS 2.0. The digitiser writes *into* this layer. |
| **3 ◀ NOW** | **Phases 2–4** | **Digitisation Engine (the product)** | • `gcs/list_objects.py` (27)<br>• `ingest/load_image.py` (28)<br>• `detect/gridlines.py` (31+33)<br>• `preprocess/remove_grid.py` (32)<br>• `detect/regions.py` (35+37)<br>• `detect/depth_ticks.py` (36)<br>• `header/ocr_header.py` (38)<br>• `header/parse_header.py` (39+40)<br>• `calibrate/depth_axis.py` (41)<br>• `calibrate/value_axis.py` (42+43)<br>• `calibrate/validate_calibration.py` (44)<br>• `preprocess/remove_annotations.py` (44b)<br>• `extract/separate.py` (46+47)<br>• `extract/trace.py` (48+49)<br>• `extract/clean.py` (50+51)<br>• `scripts/qc_plot.py` (51b)<br>• `las/write_las.py` (52)<br>• `gcs/write_bytes.py` (53) | **Curve Extraction — scan to LAS.** Finds the grid and tracks, OCRs the scale headers, applies linear and logarithmic calibration, traces coloured and dashed curve paths through their crossings, and writes CWLS LAS 2.0 to GCS. **Ends at Gate C, the core offering.** |
| 4 | **Phase 1b** | **Display & Rendering** | • `render/track_layout.py` (19)<br>• `render/vega_track.py` (20)<br>• `render/vega_spec.py` (21)<br>• `render/a2ui_emit.py` (23)<br>• `integration/pipeline_render.py` (24) | **The Display Engine**: assigns curves to SPWLA tracks, compiles the Vega spec and emits the interactive chart into chat — rendering the **digitised** LAS, so it also proves the round trip. |
| 5 | **Phases 5–6** | **QC, Audit & Interaction** | • `qc/` (57–60)<br>• `bq/index_row.py` (61)<br>• `render/a2ui_controls.py` (63) | **Trust & Control**: confidence and coverage per curve, an audit row in BigQuery, and a depth-window control that re-renders at higher resolution. |

---

## Phase 0 — Wire-format spike

*Settles the three A2UI unknowns before a line of pipeline code exists.*

- [x] **1** · `[env]` · Scaffold the project with `agents-cli` — never hand-write the A2A surface
- [x] **2** · `[mod]` · `contracts.py` — dataclasses only, imports nothing from this project
- [x] **3** · `[test]` · **Determine catalog version: v0.8 or v0.9** — recorded v0.9 in `contracts.py`
- [x] **4** · `[mod]` · `render/a2ui_envelope.py` — one message → one Part, `text/plain`, `<a2a_datapart_json>` markers, `{"kind":"data","data":…}`
- [x] **5** · `[mod]` · `render/a2ui_lifecycle.py` — all four message builders for the resolved version
- [x] **6** · `[mod]` · `integration/agent_card.py` — capabilities declaring the A2UI extension
  ✅ Advertises ADK executor and `https://a2ui.org/a2a-extension/a2ui/v0.9` on `/.well-known/agent-card.json`
- [x] **7** · `[mod]` · `integration/executor.py` — **runtime** extension negotiation
  ✅ `A2uiNegotiatingExecutor(A2aAgentExecutor)` inspects incoming `context.requested_extensions`, activates `v0.9`, and stores `active_a2ui_version` in `call_context.state`. Verified across 4 test cases (v0.9 requested, v0.8/v0.9 dual requested, no extension text fallback, executor init).
- [x] **8** · `[mod]` · `scripts/emit_fixture.py` — dumps emitted bytes, asserts the wire shape
  ✅ Emits 3 independent lifecycle parts (`createSurface`, `updateComponents`, `updateDataModel`). Byte inspection verifies `text/plain`, `<a2a_datapart_json>` delimiters, and `{"kind":"data","data":...}` envelope shape.
- [x] **9** · `[int]` · Minimal `integration/agent.py` emitting one `Text` component; deploy under the SA
  ✅ Deployed to Vertex AI Agent Runtime in **`asia-south1` (Mumbai, India)**:
  - **Reasoning Engine ID**: `projects/349946979746/locations/asia-south1/reasoningEngines/6106115834621460480`
  - **Agent Card URL**: `https://asia-south1-aiplatform.googleapis.com/reasoningEngines/v1/projects/349946979746/locations/asia-south1/reasoningEngines/6106115834621460480/api/a2a/app/.well-known/agent-card.json`
  - **Service Account**: `log-digitiser-agent@og-agentic-ecosystem.iam.gserviceaccount.com`
  - **Registered Agent ID**: `projects/349946979746/locations/global/collections/default_collection/engines/oil-and-gas-agentic-transf_1788683272432/assistants/default_assistant/agents/10364749965176844252`
  - **Live Verification**: `agents-cli run --url https://asia-south1-aiplatform.googleapis.com/v1/projects/349946979746/locations/asia-south1/reasoningEngines/6106115834621460480 --mode adk` successfully queried, returns multi-turn petrophysical response with A2UI v0.9 lifecycle envelope.

- [x] **9b** · `[mod]` · `render/inventory_card.py` — *added, not in the original plan.* Renders the
  live bucket inventory as a `Card → Column → Text[]` tree, so the Gate 0 surface proves the
  **data path** as well as the wire format. Degrades to a visible error card if the scan fails,
  rather than looking like an empty bucket.
- [x] **9c** · `[test]` · `tests/unit/test_a2ui_catalog_validation.py` — *added, not in the original
  plan.* Validates every emitted component against the **real** downloaded GE catalog
  (`tests/fixtures/a2ui/`, pinned) plus the `root` protocol invariant. Turns a ~4-minute
  deploy-to-find-one-error loop into a 2.5-second local check. 21 tests.

> [!IMPORTANT]
> **Gate 0** — the word "hello" renders as an A2UI component in Gemini Enterprise chat.
> Nothing beyond this is worth building until a trivial surface renders.

- [x] **GATE 0 PASSED**
  ✅ **2026-09-17 · confirmed visually by the user in Gemini Enterprise chat.** Typing
  `hello` renders a bordered Card titled *Petrophysics Data Inventory* containing the
  Column, Divider and all Text nodes, populated with live Cloud Storage contents
  (`Well_log_schlum.jpg`, 51.4 KiB). Not a hardcoded string — the bucket was read at
  request time by the agent's own service account.

> [!NOTE]
> **Post-mortem: this gate was ticked prematurely once, and is retained here as a lesson.**
> It was first marked passed on the strength of a successful deploy and a valid payload
> observed on the reasoning-engine `:streamQuery` endpoint. Neither of those is the gate.
> The gate is *"renders in Gemini Enterprise chat"*. Three distinct faults were hiding
> behind that premature tick, each only becoming visible after the previous was fixed:
>
> | # | Fault | Symptom in GE chat | Status |
> |---|---|---|---|
> | 1 | `inline_data` became a `FilePart` | `application/json+a2ui: Unsupported attachment` | fixed — `<a2a_datapart_json>` framing |
> | 2 | `Card` used `children`/`appearance`; `Text` used `usageHint` | red *"Validation failed for component 'Card'"* | fixed — schema corrected, step 9c now guards it |
> | 3 | Root component id was `inventory-card`, not `root` | **nothing at all** — no card, no error, no log | fixed — confirmed rendering |
>
> Fault 3 is the reason this matters. It is a **prose-only MUST** in the A2UI spec
> (`a2ui_protocol.md:182`) that the JSON Schema does not encode, so the payload validated
> cleanly and the renderer silently drew nothing. There was no error to find in any log.
> Log inspection alone could never have found it; it took reading the spec.
>
> **Applies to every remaining gate:** a green deploy, a valid payload and passing tests
> are all necessary, and none of them are sufficient. A gate closes when a human observes
> the stated outcome. Standing rule: *never pass a gate on a partial result.*

---

## Phase 1 — GCS access and the LAS domain layer ✅ COMPLETE

*No image processing. Everything needed to read from the bucket and to read, understand and
check a LAS file. The digitiser in Phases 2–4 writes **into** this layer, and step 52's writer
must produce output that step 17's validator accepts.*

> [!NOTE]
> **Steps 10–13 were originally built out of order**, before Gate 0 opened, as a
> by-product of the inventory card (step 9b). That debt has now been repaid: each is a
> single-purpose module with its own tests, as this phase requires.

- [x] **10** · `[mod]` · `gcs/paths.py` — the **only** file allowed to construct an object key
  ✅ `GCS_BUCKET`, `GCS_REGION`, `SCANNED_LOGS_PREFIX`, `LAS_OUTPUT_PREFIX`,
  `RASTER_EXTENSIONS`, `LAS_EXTENSION`, `gcs_uri()`. Output prefix corrected to
  `Digitised Well Logs/` to match this checklist (was wrongly `Digitised LAS/`).
- [x] **11** · `[mod]` · `gcs/client.py` — returns an authenticated client, nothing else
  ✅ Extracted from `inventory.py` into its own module. `build_storage_client()` clears the
  credential quota project so no `x-goog-user-project` header is sent — without this the
  agent SA gets a 403 demanding `serviceusage.services.use`, which it deliberately does
  not hold. 3 tests in `tests/unit/test_gcs_client.py`.
- [x] **12** · `[mod]` · `gcs/read_bytes.py` — key → bytes
  ✅ `read_bytes(object_name)`. Uses `blob.download_as_bytes()`; never touches bucket
  metadata (the SA lacks `storage.buckets.get`). Errors **propagate** here, unlike the
  inventory scan — a caller asking for a named file must know it did not arrive.
- [x] **13** · `[mod]` · `gcs/list_las.py` — lists LAS under `Digitised Well Logs/`
  ✅ `list_las()` returns sorted keys scoped to `LAS_OUTPUT_PREFIX`, skipping folder
  placeholders. Verified live: `['Digitised Well Logs/SCAFFOLD-TEST-0001.las']`.
- [x] **14** · `[env]` · Hand-author a ~30-line valid SPWLA LAS 2.0 file and upload it *(throwaway — deleted at step 56)*
  ✅ `tests/fixtures/las/SCAFFOLD-TEST-0001.las`.
  20 depth steps, 7,000–7,009.5 ft at 0.5 ft. Six curves — `DEPT`, `GR`, `SP`, `ILD`,
  `NPHI`, `RHOB` — chosen so **all three SPWLA tracks** carry visible character, including
  `ILD` for the logarithmic track. Models shale / hydrocarbon-sand / shale.
  Contains **one deliberate `NULL` (-999.25)** in `SP` at 7,004.0 ft to verify that gaps
  render as gaps and are never interpolated. `~OTHER` marks it as synthetic scaffolding.
  Validated by `lasio` (which correctly read the NULL as NaN) and round-tripped through
  `list_las()` → `read_bytes()` → `lasio.read()`.
  ✅ Re-confirmed live 2026-09-18 after the bucket revert:
  `gs://og-agentic-petrophysics-data/Digitised Well Logs/SCAFFOLD-TEST-0001.las`. This is the
  file Gate 1 renders. (It was first uploaded to the short-lived Mumbai bucket.)
- [x] **15** · `[mod]` · `las/mnemonics.py` — SPWLA mnemonic → name, unit, scale type *(ours: domain data, not an algorithm)*
  ✅ `MnemonicSpec` for 10 curves across the three SPWLA tracks, plus 30 vendor `ALIASES`
  (`RT`/`LLD`/`AT90` → `ILD`, and so on) and `DEPTH_MNEMONICS`. The **only** place that
  decides scale type and track number: `ILD`/`ILM`/`RXO` are logarithmic, everything else
  linear. `NPHI` deliberately carries `display_min > display_max` so the neutron/density
  crossover reads as hydrocarbon. 27 tests in `tests/unit/test_mnemonics.py`.
- [x] **16** · `[mod]` · `las/parse_las.py` — **thin `lasio` adapter** → `LasDocument`, pure. No hand-written section parsing
  ✅ `parse_las()` reads in-memory via `io.StringIO`, never touching the filesystem.
  lasio's NaN is converted back to `-999.25` with `confidence=0.0`, so a gap stays a gap.
  Mnemonics are carried through **verbatim** — interpretation is `track_layout`'s job, and
  rewriting `RT` to `ILD` at read time would make the LAS we later write disagree with its
  source. `~OTHER` provenance survives the read. 16 tests in `tests/unit/test_parse_las.py`.
- [x] **17** · `[mod]` · `las/validate_las.py` — CWLS 2.0 conformance via `lasio` + our SPWLA mnemonic check
  ✅ `validate_las()` returns `list[QcFinding]` and **never raises**: version, the five
  required `~WELL` items, the `NULL` convention, FT-vs-M index agreement, depth-frame
  monotonicity, `STRT`/`STOP`/`STEP` against the actual data, duplicate mnemonics, and
  unrecognised mnemonics or units. Structure only — whether the *numbers* are sensible is
  `qc/range_check.py` (step 58). 37 tests in `tests/unit/test_validate_las.py`; the
  step-14 fixture validates with zero findings. **102 unit tests pass overall.**

> [!NOTE]
> **Step 17 was interrupted mid-write when the previous session ended** (2026-09-17 14:44).
> The module existed; its tests did not. Writing them exposed three defects, all fixed:
>
> | # | Defect | Why it mattered |
> |---|---|---|
> | 1 | `import io` missing | **Every** call returned `"File is not readable as LAS: NameError"`. The validator was 100% broken and looked like a data problem. |
> | 2 | `lasio`'s `SectionItems.get()` misread as `dict.get` | It fabricates an empty item for a missing mnemonic instead of returning `None`, so the guard never fired and the next line raised `KeyError`. The validator **crashed on exactly the malformed headers it exists to catch**. Same pattern found and fixed in `parse_las.py`, where it happened to be harmless. |
> | 3 | Non-numeric `~ASCII` raised `ValueError` | Same class of failure: the tool you reach for when a file is suspect died on the suspect file. Now an ERROR finding. |
>
> A fourth issue was the code contradicting itself: the `STEP` tolerance comment claimed
> vendor rounding of `0.1524 m` → `0.15` was accepted, but at 1% it was not (the error is
> 1.6%). Tolerance widened to 2%, which still catches a step in the wrong unit.
>
> None of these were visible from a green deploy. Defect 2 in particular only appears on
> input the happy path never produces — which is the argument for the one-step-one-test rule.
> [!IMPORTANT]
> **Gate 1a — the LAS domain layer is complete.** GCS read/list works under the agent's own
> service account, and a LAS file can be parsed into `LasDocument` and structurally validated.
> **102 unit tests pass.** This is everything the digitiser in Phases 2–4 needs from this side.

- [x] **GATE 1a PASSED** — 2026-09-18

> [!NOTE]
> **Steps 18–26 have moved.** They were the display half of this phase — Vega spec
> compilation and A2UI emission — and now live in **Phase 1b**, after Gate C. See the
> reordering note at the top of this file.

---

## Phase 2 — Image access and calibration

**This phase is now the front of the queue.** It holds every genuinely uncertain part of the
project, which is the reason for the reorder.

> [!NOTE]
> **What the source scan actually contains — read before writing step 35 or 46.**
> `Well_log_schlum.jpg` is ~916×775 px, giving **≈0.54 ft per pixel** vertically over
> 7,000–7,300 ft. A 0.5 ft LAS step is therefore honest; anything finer would be invented.
>
> | Observation | Consequence |
> |---|---|
> | **The depth column sits in the MIDDLE**, between Track 1 and Track 2 | `detect/track_bounds.py` (35) must **not** assume the depth axis is leftmost. This is the single most likely wrong assumption in the phase. |
> | Curves are distinguished by **both colour and dash** — GR green / SP black; deep solid, medium dashed, shallow dotted; NPHI black / RHOB brown | Exactly as steps 46 and 47 anticipated. |
> | Large **coloured area fills** — yellow sand, grey hydrocarbon, orange gas, green oil, blue brine | Not curves. A fill's edge coincides with a curve in places and not in others. New step 44b. |
> | **Annotation text and leader lines sit inside the tracks** — "Shale", "Sand", "Hydrocarbon", "Gas", "Oil", "Brine" | Will be traced as curve pixels unless removed. New step 44b. |
> | Headers are cleanly structured: `0 gAPI 150`, `−80 mV 20`, `0.2 ohm.m 20`, `45 % −15`, `1.90 g/cm³ 2.90` | Good conditions for the OCR step (38). `NPHI 45 → −15` is reversed and already matches `mnemonics.py`. |
> | Only **four** depth ticks are labelled (7,000 / 7,100 / 7,200 / 7,300) | Step 41's least-squares depth fit has four points. Enough, but every tick counts — a mis-detected one moves every depth. |

> [!NOTE]
> **Consolidated 2026-09-18: 20 steps → 13.** Merges, three deferrals, and one ordering fix
> (`remove_grid` was listed *before* the gridline detection it consumes). Step numbers are kept
> as work increments. See the consolidation note at the top of this file.

- [x] **27** · `[mod]` · `gcs/list_objects.py` — **generalises the former `gcs/list_las.py`.**
  Listing scans and listing LAS files differ only by prefix and extension, so one private
  `_list(prefix, extensions)` with two thin public functions `list_las()` and `list_images()`
  replaced two near-identical files. *9 tests.*
- [x] **28** · `[mod]` · `ingest/load_image.py` — bytes → `RasterImage`. The only module that
  decodes, and the only one that knows the buffer layout. Converts OpenCV's BGR to RGB, pinned by
  a test against the scan's orange gas fill. *13 tests.*
- [x] **33** · `[mod]` · `detect/gridlines.py` — **absorbed step 31 (`binarise.py`).** Morphological
  opening with a long 1-px kernel, the standard OpenCV table-rule approach, in **two threshold
  passes**: heavy ink below 128 for the frame and separators, light ink below 245 for the pale
  reading grid, which an Otsu threshold tuned for black would erase. Regular rows are found by
  **longest-chain search**, not by anchoring on the first row — anchoring dragged the header box
  rules at 13, 72 and 133 into the depth grid. *12 tests.*
- [x] **32** · `[mod]` · `preprocess/remove_grid.py` — subtracts the detected grid. *9 tests.*
- [x] **35+37** · `[mod]` · `detect/regions.py` — track bounds **and** header regions, one
  geometric decomposition. The depth column is found **by width, never by position**; on this sheet
  it is 2nd of 4. The data area is bracketed by the heavy rules that **enclose the depth grid**,
  which is the only thing distinguishing them from the identical-looking header rules. The header
  band runs from the topmost printed row to the data area, because Track 2 stacks three curve
  headers and starts 59 px above Tracks 1 and 3. *16 tests, including a synthetic leftmost depth
  column and an equal-width sheet that is refused.*
- [x] **36** · `[mod]` · `detect/depth_ticks.py` — pairs each printed label with its grid line.
  `DepthTick` keeps the two apart deliberately: the 7,000 label is printed 14 px **below** its own
  line and 7,300 11 px **above** its own, both to fit inside the frame, so calibrating on label
  centres would bend the depth scale at both ends. *13 tests.*
- [x] **38** · `[mod]` · `header/ocr_header.py` — **the pipeline's only language-model call.**
  Sends one request holding a crop per track header, the depth column header, and each depth
  label, and returns the text **verbatim** under a pinned JSON schema at temperature 0. Rejects a
  reply whose label count differs from the tick count — labels pair with pixel rows by position,
  so that mismatch would file every value at the wrong depth. *9 tests with the call replaced.*
- [x] **39+40** · `[mod]` · `header/parse_header.py` — `parse_scales()`, `parse_depth_unit()` and
  `parse_depth_label()`. Fully deterministic. Converts printed units to LAS units — **`45 %` becomes
  `0.45 V/V`**, and stored as 45 it would be a hundredfold error — and distinguishes a thousands
  separator from a European decimal comma, without which `2,000 ohm.m` reads as 2.0. Scale type is
  derived **from the printed numbers** (a range spanning a decade or more and never reaching zero
  is logarithmic) and cross-checked against `las/mnemonics.py`; disagreement is refused.
  `lookup_by_title()` was added to `mnemonics.py` so a printed "Resistivity, Deep" resolves to ILD.
  *31 tests.*
- [x] **41** · `[mod]` · `calibrate/depth_axis.py` — least-squares fit over **every** labelled tick,
  reporting the residual RMSE that step 44 judges the scan on. A test fails if the implementation
  ever degenerates into two-point interpolation. *10 tests.*
- [x] **42+43** · `[mod]` · `calibrate/value_axis.py` — `pixel_to_value()` with linear and
  logarithmic as two branches of one function, plus the inverse `value_to_pixel()` the QC overlay
  needs. Pinned by the test that halfway across a 0.2–20 ohm.m track reads **2, not 10.1**.
  *15 tests.*
- [x] **44** · `[mod]` · `calibrate/validate_calibration.py` — judges the depth fit **in pixels**,
  not depth units, so a metric log is held to the same standard as an imperial one. Carries the
  skew/perspective guard that replaces deferred steps 29 and 34: a grid pitch that **drifts**
  steadily down the page is a photograph taken at an angle and is refused, while the ±1 px rounding
  jitter a real sheet shows is not. The reference scan passes with no warnings. *15 tests.*
- [x] **44b** · `[mod]` · `preprocess/remove_annotations.py` — **added 2026-09-18 after examining
  the scan.** Hollows out the colour fills **by erosion, never opening**: opening restores a
  region's outline and would take the bounding curve with it. Curve ink is then excluded from the
  erasure — without that, the SP curve running *through* the yellow sand was eaten, because curve
  plus surrounding fill is a thick printed region. Labels are found by clustering character-shaped
  blobs that share a baseline, which is what stops the medium resistivity curve's 8-on-4-off dashes
  from being read as a word. *14 tests.*
  ⚠️ **Must run BEFORE `remove_grid`**: whitening the grid rows first slices a fill into bands and
  leaves every band's rim behind.
- [x] **45** · `[int]` · `integration/tools.py` — `inspect_scanned_log`. *11 tests.*

> [!WARNING]
> **Deferred to Phase 7 — steps 29 `deskew`, 30 `denoise`, 34 `rectify`.**
> These correct rotation, scan noise and perspective distortion. `Well_log_schlum.jpg` is a clean,
> axis-aligned digital figure and **has none of them**. Building them now would mean writing three
> modules that cannot be meaningfully tested, because we have no distorted image to test against —
> guideline 3, *no scaffolding for a job it might do later*.
>
> Note also that 34 (homography) subsumes 29 (rotation), so the two were partly redundant by
> construction. When a genuine photographed scan arrives, `rectify` alone is the right answer.
>
> The risk of deferring is a distorted image being digitised into plausible-looking wrong depths.
> **Step 44's guard closes that** by detecting the condition and refusing.

> [!IMPORTANT]
> **Gate 2** — from the image in GCS alone the agent reports 3 tracks, 7 curves, depth range
> 7,000–7,300 ft, and identifies Track 2 as logarithmic. **Nothing hardcoded.**

> [!NOTE]
> **Gate 2 PASSED, 2026-09-18 — verified with nothing replaced.**
> `inspect_scanned_log("Scanned Well Logs/Well_log_schlum.jpg")` was run end to end: the image was
> read from Cloud Storage under the agent's own service account and the header transcribed by a
> live Gemini call. No mocks, no fixtures, no hardcoded layout.
>
> ```
> {"ok": true, "digitisable": true, "track_count": 3, "curve_count": 7,
>  "depth_range": [6999.8, 7300.4], "depth_units": "FT",
>  "depth_resolution": 0.52, "depth_fit_error": 0.142}
> ```
>
> | Criterion | Result | Derived from |
> |---|---|---|
> | 3 tracks | 3 | heavy separators at 8 / 285 / 357 / 635 / 912, minus the narrowest column |
> | 7 curves | 7 | header transcription |
> | 7,000–7,300 ft | 6,999.8 – 7,300.4 | least-squares fit over 4 ticks, RMSE **0.142 ft** (0.27 px) |
> | Track 2 logarithmic | LOGARITHMIC | printed scale `0.2 ohm.m 20` spans two decades |
>
> The only QC finding was INFO: *"One pixel is 0.520 FT, so the scan supports a sample step no
> finer than that."* No warnings, no errors.
>
> **The live transcription was character-perfect** and matched the mocked reply in
> `tests/unit/test_tools.py` exactly — `0 gAPI 150`, `-80 mV 20`, `0.2 ohm.m 20` ×3, `45 % -15`,
> `1.90 g/cm³ 2.90`, `Depth, ft`, and `7,000 / 7,100 / 7,200 / 7,300`. That equality is what makes
> the mocked test a fair stand-in for the live call from here on, so Phase 3 does not need to spend
> a request per test run.

- [x] **GATE 2 PASSED** — 2026-09-18

---

## Phase 3 — Curve extraction

> [!NOTE]
> **Six steps, three files — consolidated 2026-09-18.** The step numbers are kept as work
> increments; the file boundaries moved. See the consolidation note at the top of this file.

- [x] **46+47** · `[mod]` · `extract/separate.py` — `by_colour()` and `by_dash()`
  Both answer one question — *which pixels belong to which curve* — by two different routes,
  dispatched per track. Tracks 1 and 3 separate by **colour** (GR green vs SP black; RHOB brown
  vs NPHI black). Track 2 cannot: all three resistivities are printed black and differ only by
  **dash pattern** — deep solid, medium dashed, shallow dotted.
  **Done — 24 tests.** Returns **affinity maps, not masks**: where two curves coincide both
  score and `trace.py` arbitrates, because a hard decision here is unrecoverable.
  Three things the real scan taught us, all of which broke a first attempt:
  - **Printed GR is olive (hue 53–76°), not the table's `#4C7C2F` (hue 97°)** — which is nearer
    the table's RHOB brown. *Matching on absolute hue swaps the Track 3 curves.* The robust
    discriminator is **channel order** (GR is G>R>B, RHOB R>G>B); order survives a change of
    press, hue does not.
  - **HSV saturation is meaningless near black** — pixel (11,10,7) computes to saturation 93/255.
    Use absolute channel spread.
  - The ordering test must be a **floor (0.15), not a veto**: brown density ink blended into the
    green oil fill leans the wrong way *and is still the curve*. As a veto it punched 18-row
    holes in RHOB.
- [x] **48+49** · `[mod]` · `extract/trace.py` — **tracing WITH the continuity prior. One algorithm.**
  *These were two steps and cannot be. At a curve crossing there are two candidate x values for a
  depth row, and choosing between them **is** the continuity prior — a log curve cannot jump
  without a rock transition, so the continuous path wins. A tracer that returns one value per row
  has already destroyed the candidate set the prior needs, so continuity can never be a
  post-processing pass over its output.*
  Implemented as a lowest-cost path down the mask: ink presence is reward, horizontal jump is
  penalty.
  **Done — 23 tests**, organised around the four hard cases: crossing, occlusion, bed boundary,
  leader line. Cost is `(1 − affinity)` per pixel plus `0.10` per pixel of lateral movement.
  **Guideline 4 exception, recorded here and in the module docstring:** the min-plus step is
  hand-rolled rather than `scipy.sparse.csgraph.dijkstra`. The graph is layered by depth row, so
  depth *is* the topological order and Dijkstra's queue collapses to a single sweep; the linear
  penalty then makes the per-row window minimum an exact two-pass cumulative-minimum identity —
  the L1 distance-transform trick generalised from a binary mask to a cost field. Dijkstra would
  require materialising a 157k-node / 3M-edge graph seven times per sheet. **Do not "fix" this.**
  Confidence is the affinity at the chosen pixel; **zero means carried across a gap and becomes
  `NULL`, never an interpolated value.**
- [x] **50+51** · `[mod]` · `extract/clean.py` — `despike()` and `resample()`
  Both are pure 1-D operations on a finished trace: remove outliers, then put the trace on a
  uniform depth step with decimation for transport. **Resampling must not interpolate across a
  gap** — an untraced interval stays `NULL`.
  **Done — 24 tests.** The load-bearing decision is that **despiking happens in pixel space and
  resampling in depth space**, on either side of calibration. Despiking in value space was built
  first and was wrong: the threshold came out at 0.00125 V/V for NPHI — *half a pixel* — and
  0.017 OHMM for ILD on a 0.2–20 log scale, removing 5–7% of every curve. A spike is a tracing
  error, so it must be judged in the units the tracer works in, where one threshold means the
  same thing everywhere on the page. Removals fell to 0–2 rows per curve.
- [x] **51b** · `[test]` · `scripts/qc_plot.py` — **throwaway matplotlib PNG.** *Added in the
  2026-09-18 reordering.* Plots the traced curves on three panels beside the source scan so
  extraction quality can be judged by eye. Deliberately **not** the product renderer: no
  Vega, no A2UI, no deploy, no GE. Extraction accuracy cannot be judged from numbers alone,
  and this restores that check without the nine steps of display work it used to require.
  *Delete or leave as a dev tool once Phase 1b renders; it is never called by the agent.*
  **Done.** Runs the whole chain live, including a real Gemini header read. `matplotlib` added
  as a **dev** dependency only. **It earned its place twice over:** every one of the four defects
  above was found by looking at its output, and none by a failing test.
  **→ Step 54 must absorb `_extract()` and `_to_curve()` from this script** — they are the full
  extraction wiring (separate → trace → despike → calibrate → resample) and belong in
  `pipeline_digitise.py`, with the script calling it.

> [!IMPORTANT]
> **Gate 3** — traced curves match the source in character: gas crossover in Track 3 and
> resistivity separation in Track 2 both reproduce, judged on the step-51b plot.

- [x] **GATE 3 PASSED** — 2026-09-18. **350 unit tests pass.** Judged on the rendered plot, not
  on numbers:
  - **Track 2 resistivity separation reproduces** — all three curves separate correctly on the
    log scale and keep their line styles; dotted RXO lowest, dashed ILM, solid ILD highest
    through the hydrocarbon zone, as printed.
  - **Track 3 gas crossover reproduces** at ~7,090–7,130 ft.
  - **Track 1 sand body reproduces**, and the depth range is 7,000–7,300 ft as printed.
  - Coverage: GR 89.3% · SP 98.2% · RXO 100% · ILM 98.2% · ILD 100% · NPHI 94.8% · RHOB 100%.
    GR's shortfall is **occlusion, not a separation defect** — 126 empty rows in 53 short runs,
    longest 16, and on each the ink present belongs to the other curve.
  - **Accepted limitation:** the two near-straight runs in GR at the top and base of the sand are
    gaps bridged by `resample()` where the "Shale" callouts and their leader lines sit on the
    curve. A callout whose leader touches its curve is one connected component with it and is
    left alone by `remove_annotations` by design. Widening the despike window to 7 was tried
    against this and correctly did nothing — there is no sample there to reject — and was
    reverted. Cosmetic; does not affect Gate 3.

---

## Phase 4 — LAS write: the core offering delivered

- [x] **52** · `[mod]` · `las/write_las.py` — **`lasio` writer.** CWLS LAS 2.0, SPWLA mnemonics, untraced intervals as `NULL -999.25` *(never interpolated)*, provenance in `~Other`
  *Its output must pass `validate_las()` (step 17) with zero ERROR findings. That is the
  contract between the two halves of this project, and it is already written and tested.*
  **Done — 24 tests.** lasio does the section formatting; this file owns the two things it
  cannot. First, **laying curves of differing extent onto one shared depth index** — curves do
  not all start at the top of the sheet (GR spans 599 samples, ILM 595, RHOB 596), and LAS has
  only one index column, so the file spans the union and a curve absent at a depth is NULL
  there. Second, **refusing to write a curve whose samples miss that index.** That guard is the
  most valuable thing in the file: an unresampled curve raises no error anywhere else in the
  pipeline and would produce a LAS that is structurally valid, opens cleanly, validates, and is
  entirely empty.
  A one-row file is also refused, because `parse_las` and `validate_las` both reject one and
  this pipeline must never emit a file its own reader will not take back.
- [x] **53** · `[mod]` · `gcs/write_bytes.py`
  **Done — 15 tests** (shared with the naming helpers). The **prefix guard is load-bearing, not
  defensive**: this bucket is shared with the other petrophysics agents and the service account
  holds `roles/storage.objectUser` across all of it, so it *could* overwrite their curves. Audit
  logging would record that afterwards; this refuses it beforehand, and it is enforceable
  because this is the only file in the project that writes to GCS.
  `content_type` is required rather than defaulted — a LAS stored as `application/octet-stream`
  downloads instead of previewing in the console.
- [x] **54** · `[int]` · `integration/pipeline_digitise.py` — image → LAS → GCS
  **Done.** Absorbed `_extract()` and `_to_curve()` out of `scripts/qc_plot.py` as promised
  under step 51b; the script now calls `extract_curves()`, so **the QC plot is evidence about
  the product rather than about a parallel copy of the wiring.** Verified: the plot's numbers
  are byte-identical before and after the move.
  Two decisions worth recording:
  - **Validation happens before the upload, not after.** The output prefix is read by other
    agents, so a LAS that lands there is a published artefact; withdrawing one is far harder
    than never writing it. This immediately earned its keep — the first live run failed *after*
    validation and *before* the upload, and published nothing.
  - The choice of depth step and bridgeable gap moved into `extract/clean.py::sampling_grid()`,
    beside `resample()` which they parameterise, so this file stays call-and-pass. That move
    also **fixed a latent defect**: the old expression gave 0.55 ft where the correct answer is
    0.5 ft, the standard wireline interval.
- [x] **55** · `[int]` · `integration/tools.py` — add `digitise_scanned_log`
  **Done.** Mirrors `inspect_scanned_log`: never raises, returns `{'ok': False, 'error': ...}`
  instead, because an ADK tool that raises loses the agent its turn. Reports per-curve
  `observed_fraction` so the agent can tell a user *which* curves to trust rather than offering
  one opaque score.
  *Not yet registered on `root_agent` — `app/integration/agent.py` still has no `tools=[...]`.
  That is Phase 6/R work and is not required by Gate C.*

> [!IMPORTANT]
> **GATE C — THE CORE OFFERING.** `Well_log_schlum.jpg` in, a real LAS out, in GCS.
> Three conditions, all required:
>
> 1. `Digitised Well Logs/<WELL>.las` exists in the bucket, written by the agent's own SA.
> 2. `validate_las()` returns **zero ERROR findings** on it.
> 3. The step-51b QC plot, laid beside the source scan, reproduces its **character**: the
>    sand body in Track 1, the resistivity separation in Track 2, the gas crossover in
>    Track 3, and the depth range 7,000–7,300 ft.
>
> Condition 3 is a human judgement and cannot be automated. Nothing here requires Vega,
> A2UI or Gemini Enterprise — this gate is deliberately reachable from a terminal.
>
> **If this gate does not close, the project has no product.** Everything below it is
> delivery of a thing that already works.

- [x] **GATE C PASSED — 2026-09-18. THE CORE OFFERING IS DELIVERED.** 397 unit tests pass. The
  pipeline ran end to end **as the agent's own service account** and published
  `gs://og-agentic-petrophysics-data/Digitised Well Logs/WELL_LOG_SCHLUM.las` — 55,336 bytes,
  7 curves, 7,000.5–7,299.5 ft at a 0.5 ft step, `text/plain`, written at 07:24:30 UTC.
  - **Condition 1 — MET.** Run under `log-digitiser-agent@og-agentic-ecosystem.iam.gserviceaccount.com`
    via in-process impersonation (`google.auth.default` patched before any client was built, so
    no credential file was ever written to disk). **This proved more than the object write:**
    the same identity also performed the Gemini header read, so the service account's
    permissions are confirmed sufficient for the *whole* pipeline, which is exactly what the
    deployed Reasoning Engine agent will need.
    *Getting here needed `roles/iam.serviceAccountTokenCreator` on that SA for the operator;
    the binding took ~60 s to propagate before impersonation succeeded.*
  - **Condition 2 — MET, and better than asked.** `validate_las()` on the bytes downloaded back
    out of the bucket returns **zero findings of any severity**, not merely zero ERRORs.
  - **Condition 3 — MET, with stronger evidence than the gate requires.** Rather than plotting
    the in-memory curves, the published LAS was **downloaded from the bucket, parsed, and drawn
    from its own numbers** with no involvement from the extraction code. It reproduces the scan:
    the sand body in Track 1, the three separated resistivities with their printed line styles
    in Track 2, the gas crossover in Track 3, and the depth range 7,000–7,300 ft.
  - Coverage as published: GR 89.3% · SP 98.2% · RXO 100% · ILM 98.2% · ILD 100% · NPHI 94.8% ·
    RHOB 100%. Overall confidence 0.69. One INFO finding, on depth resolution.

> [!NOTE]
> **What Gate C does NOT cover, so it is not mistaken for more than it is.** The tool is not yet
> registered on `root_agent` (no `tools=[...]` in `app/integration/agent.py`), nothing is
> deployed, and there is no rendering. The product is reachable from a terminal only. Those are
> Phases 1b, 6 and R.

---

## Phase 1b — Display: render the digitised LAS

*Deferred from its original position ahead of Phase 2 — see the reordering note at the top.
It now renders the **real** digitised LAS, not the hand-authored step-14 fixture, which makes
the old Gate 4 round-trip test redundant: the round trip is the only path there is.*

- [x] **18** · `[mod]` · `scripts/compile_check.py` — compile + headless render, then repeat with GE's sizing rewrite
  *Language decided: **`vl-convert-python`** as a dev dependency, not Node. It bundles the same
  Vega-Lite compiler the browser runs, keeps the project to one language and one dependency
  manager, and satisfies CODING_GUIDELINES rule 4's demand for "the real Vega-Lite compiler".
  32 MB, dev group only, so it does not ship to the runtime.*
  **Evidence:** compiles to Vega, renders to PNG, counts leaf marks at any nesting depth so a
  spec that compiles to an empty chart fails here rather than in chat, and reports the payload
  against the 100 KiB inline limit.
- [x] **19** · `[mod]` · `render/track_layout.py` — curve → track assignment
  **Evidence:** GR+SP → track 1, RXO+ILM+ILD → track 2, NPHI+RHOB → track 3, all from
  `mnemonics.py`. Unknown mnemonics are dropped, not guessed onto a track. Rejects a document
  whose curves do not share one depth index — a silent misalignment would still draw a
  convincing plot. NULL → `None` so the line breaks instead of diving to −999.25.
- [x] **20** · `[mod]` · `render/vega_track.py` — one track → one layered view; **exactly one layer owns the depth axis**
  **Evidence:** one layer per curve; x scales resolved independently so each curve keeps its
  printed scale; curves sharing a scale share one header, named for all of them
  (`RXO · ILM · ILD (OHMM)`).
- [x] **21** · `[mod]` · `render/vega_spec.py` — `hconcat`, numeric width per child, `resolve.scale.y="shared"`, zoom param on **one layer only**
  **Evidence:** 3 children × 200 px, one shared depth scale, one `depthZoom` param bound to the
  y encoding only. Data inline as one record per depth: **70.2 KiB, 29.8 KiB under the limit.**
- [x] **22** · `[test]` · Run `compile_check` — renders as-authored **and** size-rewritten
  **Both pass.** 7 marks, 4 signals, identical under GE's `width/height: 'container'` rewrite —
  the fixed child widths leave it nothing to act on.

  > **Two defects the compile check caught, both of which render without error:**
  >
  > 1. **Every curve drawn as a solid black blob.** Vega-Lite sorts a line by its **x** channel
  >    by default. On a well log x is the *reading*, so all seven curves were drawn in order of
  >    increasing value instead of increasing depth — a dense zigzag between the extremes of
  >    each track. Compiles, renders, and is completely wrong. Fixed with an explicit
  >    `order` encoding on DEPTH.
  > 2. **The depth axis vanished entirely.** One layer declared the axis and the rest declared
  >    `axis: null`, which is what BUILD.md Part 4 prescribes — but Vega-Lite *merges* shared
  >    layer axis definitions, and a real axis merged with null yields null. No warning. Fixed
  >    with `resolve.axis.y = "independent"`, which lets the one declaration stand.
  >
  > Neither would have been visible in a schema check or a unit test written from the spec.
  > Both are now pinned by tests.
- [x] **23** · `[mod]` · `render/a2ui_emit.py` — the callback that attaches the Parts
  **Evidence:** three Parts — `createSurface`, `updateDataModel`, `updateComponents` — data model
  **before** the components that read it.

  > **The spec cannot be embedded in the chart component.** `VegaChart.spec` is typed
  > `DynamicValue`, whose `oneOf` admits string, number, boolean, array, DataBinding or
  > FunctionCall — and **not object**. Writing the spec inline, which is what every Vega-Lite
  > example shows, fails catalog validation outright. It travels in `updateDataModel` and the
  > component points at `/spec`. Pinned by
  > `test_vega_chart_spec_cannot_hold_an_inline_object`, so if a future catalog adds an object
  > variant the indirection gets reconsidered rather than carried on as folklore.
- [x] **24** · `[int]` · `integration/pipeline_render.py` — composes GCS → LAS → spec, calls and passes only
  **Evidence:** `render(object_name) -> LogPlot`. Accepts a bare well name or a full object key,
  since the model will relay whichever form the user typed.
- [x] **25** · `[int]` · `integration/tools.py` — add `render_well_log`
  **Evidence:** builds the plot so a bad well name fails while the model can still say so, then
  leaves the resolved key in session state for the callback. A tool cannot attach a surface to
  its own response; only a callback runs late enough.
- [x] **25a** · `[int]` · `integration/tools.py` — add `list_well_logs`
  **Evidence:** wraps `scan_bucket_inventory()` and returns counts plus, per scan, its format,
  size, whether it is already digitised, and the LAS key it would be written to.
  > **Why a tool at all, when the inventory card already shows this.** The card is attached by
  > the after-agent callback, which by construction runs *after* the model has finished
  > writing. The model never sees it, and is instructed never to invent counts — so without
  > this tool the only honest answer to "how many are still only images?" is "see the card
  > below". Run live against the bucket: 1 scan, 1 digitised, 0 awaiting, correctly paired.
  > Pairing uses `las_object_name()`, the same rule the writer names its output by, so the
  > two cannot disagree.
- [x] **25b** · `[mod]` · `ingest/load_pdf.py` — accept a scanned log filed as a PDF
  **Evidence:** `rasterise_first_page()` via `pypdfium2`; `load_image` sniffs `%PDF-` and
  delegates, staying the single decode point. `DIGITISABLE_EXTENSIONS` makes PDFs visible to
  the inventory and the lister. **Proven end to end against a real object in the bucket:**
  `Scanned Well Logs/Well_log_schlum_scan.pdf` inspects to the same 3 tracks, 7 curves,
  0.520 ft/px and 0.142 RMSE as the JPEG.
  > **A fixed render width was tried first and it broke the sheet.** Rendering to 1000 px
  > upscaled the 919 px reference by 1.088×, which moved the depth grid and put a header text
  > block 45 px from the nearest rule on a 42 px pitch. `depth_ticks.py` correctly refused to
  > attribute it and the whole inspection failed. **The page is now rendered at the resolution
  > of the raster embedded in it** — no resampling, identical numbers to the JPEG path. Do not
  > reinstate a fixed target width.
  >
  > Incidentally this is the first time `_MAX_LABEL_OFFSET_FRACTION = 0.5` has ever fired. The
  > earlier note that it "can never fire within the grid's span" was right — it fired on a
  > block *outside* the span, which is exactly what it is for.
  >
  > **Never upscales; downscales only above 2000 px.** A 300 dpi letter scan is ~2550 px, well
  > outside the ~919 px regime the thresholds were tuned in.
  >
  > **Multi-page PDFs are refused, not silently truncated.** Publishing page 1 of five as "the
  > well log" would drop four fifths of the well into a file that looks complete.
  >
  > **Vector PDF extraction is explicitly NOT built.** A born-digital PDF holds its curves as
  > path geometry and would digitise near-perfectly — the SP-over-GR occlusion could not
  > happen. That is a second extraction backend (~2–3 days) replacing `separate.py` and
  > `trace.py`, and it pays off only on files we do not have. Deferred, not overlooked.
- [x] **25c** · `[int]` · Chain digitisation to the chart
  **Evidence:** `digitise_scanned_log` now takes a `ToolContext` and sets `PENDING_CHART_KEY`
  on success, so the plot is drawn in the same turn the LAS is published. Pinned both ways:
  `test_digitising_queues_the_chart_for_the_same_turn` and
  `test_a_failed_digitisation_queues_nothing` — a chart of a file that was never written
  would be a fabrication.
- [x] **25d** · `[int]` · Confirmation before anything is written
  **Evidence:** the agent instruction requires it to state the source, the destination object
  key and the runtime, then stop and wait, before calling `digitise_scanned_log`.
  > Enforced in the instruction rather than by gating the tool, deliberately. A hard gate
  > would make the agent ask twice when the user says "digitise Well_log_schlum.jpg, go ahead"
  > in one breath. If the model is ever observed writing unbidden, promote this to an ADK tool
  > confirmation — the instruction is the cheap version, not the only one.
- [x] **26** · `[int]` · Wire the callback into `integration/agent.py`; deploy
  **Evidence:** `after_agent_callback` dispatches — a pending chart replaces the inventory
  card, and is cleared as it is read so it cannot reattach to later replies. **All four tools
  are registered on `root_agent`** — `list_well_logs`, `inspect_scanned_log`,
  `digitise_scanned_log`, `render_well_log` — which closes the gap recorded under Gate C.
  Declarations verified: `list_well_logs` takes no arguments, the other three expose
  `object_name` only, with `tool_context` correctly excluded. Verified offline against a stub
  GCS: 3 Parts, 71.4 KiB on the wire, state cleared, follow-up turn falls back to the
  inventory card.
  **Deployed 2026-09-18 09:03 UTC** (Phase 1b baseline), **09:16 UTC** (Deploy 2, native PDF resolution), and **10:22 UTC** (Deploy 3, carrying steps 26a–26e: chart `value` fix, base64 scan display, model scrubber) to reasoning engine `6106115834621460480` (asia-south1).
  Container logs confirm a clean boot: `Application startup complete`, Uvicorn serving on 8080, no
  import errors.
  > **The Agent Card URL printed by `agents-cli` 1.4.2 returns 404.** Not a deployment
  > failure — the runtime logs show the app serving, and Gemini Enterprise reaches the agent
  > through its own registered connection rather than that URL. Don't chase it; check the
  > reasoning-engine logs instead.
- [x] **26a** · `[mod]` · `render/a2ui_lifecycle.py` — `updateDataModel` must carry **`value`**, not `data`
  **Evidence:** the live GE run of 2026-09-18 replaced the chart with
  `This content could not be displayed. Expected undefined, received undefined /updateDataModel`.
  We were sending `{"data": …}`. The spec's own example stream sends `{"path": …, "value": …}`,
  and Google's builder agrees — `MessageBuilder.kt:137` emits `add("value", contents)`, naming
  the Kotlin parameter `contents` while writing the key `value`. Fixed, and measured end to end
  against the real LAS: `updateDataModel keys: ['surfaceId', 'value']`, 72,127 bytes.
  > **Why every existing test missed it.** `tests/fixtures/a2ui/` pins the component catalog and
  > the common types, and the catalog says nothing whatever about the messages that carry the
  > components. The lifecycle envelope had no schema and therefore no test. `updateComponents`
  > happened to be right; `updateDataModel` was never exercised until Phase 1b, and shipped
  > wrong. Two existing tests were actively pinning the bug and had to be corrected.
- [x] **26b** · `[test]` · `tests/unit/test_a2ui_lifecycle.py` — pin the envelope against the spec
  **Evidence:** 9 tests. Every expected shape is taken from the published specification's own
  example stream, which is quoted verbatim in the file and itself guarded by
  `test_the_spec_example_stream_names_the_four_lifecycle_messages` — so the quotation cannot be
  quietly edited to match the code. This is the guard whose absence let 26a through.
- [x] **26c** · `[int]` · `strip_fabricated_a2ui` — stop the model echoing A2UI into its prose
  **Evidence:** the live run emitted **three** `<a2a_datapart_json>` blobs as literal text, two
  copied verbatim from the previous turn **including its surface id**, and a third inventing a
  `WellLogChart` component that exists in no catalog. Cause: A2UI travels as `text/plain`, so
  every surface we attach re-enters the conversation as text the model reads next turn, and it
  imitates what it reads. Now stripped deterministically in an `after_model_callback`, with an
  instruction forbidding it as well.
  > **Both, not either.** An instruction alone will not hold against the model copying what is
  > right there in its context. The scrubber touches only text parts; our own surfaces are
  > `inline_data` blobs attached later by the after-agent callback and never pass through it.
  > The copies were the worse half: a repeated surface id makes the renderer drop the real
  > surface too.
- [x] **26d** · `[mod]` · `render/scan_image.py` + `show_scanned_log` — show the sheet itself
  **Evidence:** asked *"can I see the image?"*, the live agent described the scan instead of
  showing it, because there was no way to show it. Now there is: the scan is decoded, re-encoded
  as JPEG and carried as a base64 `data:` URI in an `Image` component. Measured against the real
  bucket — JPEG **159,471 bytes (30.4% of cap)**, PDF **159,925 bytes (30.5%)**. Validated
  against the live catalog by `test_scan_components_validate`.
  > **A data URI, not a signed URL.** The service account reads the bucket either way; the
  > question is whether the reader's BROWSER also needs access. With a data URI it does not, and
  > the agent needs no `serviceAccountTokenCreator` on itself. The remaining risk is that GE's
  > CSP blocks `data:` images, which only a live run can settle.
  >
  > **Everything is re-encoded, whatever it arrived as.** That is not wasted work: a scanned PDF
  > then displays without asking the browser to render a PDF inside an `Image`, and the output
  > size is under our control rather than the uploader's.
  >
  > **It refuses to guess between two files.** `_match_scans` returns *every* candidate, and the
  > tool shows nothing when there is more than one — two scans of a well are usually different
  > runs. Verified live: `'Well_log_schlum'` → the JPEG, `'Well_log_schlum_scan.pdf'` → the PDF.
- [x] **26e** · `[mod]` · Replace the guessed payload limit with the measured one
  **Evidence:** `_PAYLOAD_LIMIT_BYTES` was a guessed 100 KiB. The real cap is
  `kMaxA2uiPayloadBytes = 512 * 1024`, enforced server-side at
  `cloud/ai/agentis/gateway/agentspace/converters/a2ui_converters.{h:21,cc:139}`. Now 512 KiB
  less a 64 KiB envelope margin. The gateway **rejects** an oversized message rather than
  truncating it, so the docstring that described silent truncation was also wrong and is fixed.
  > This is what makes the base64 image affordable. At the guessed limit it would have looked
  > impossible; at the real one it uses 30%.
- [ ] **56** · `[env]` · Delete the hand-authored LAS from step 14 *(only once the digitised LAS renders — it is the fallback until then)*

> [!IMPORTANT]
> **Gate 1 — now also the old Gate 4.** Judged in the Gemini Enterprise chat surface, by you.
> It is the whole conversation, not one render:
>
> 1. *"How many well logs are only images or PDFs?"* → a **number**, not "see the card", and
>    the inventory card lists the files beneath it.
> 2. *"Can I see the scan?"* → because there are **two**, it must **list them and ask which**,
>    not pick one.
> 3. *"The JPEG"* → the scanned sheet **appears as a picture**, not a description of a picture.
> 4. *"Can you digitise the PDF?"* → the agent names the scan, names the **destination object
>    key** (`Digitised Well Logs/WELL_LOG_SCHLUM_SCAN.las`), says it takes a minute or two, and
>    **stops**. It must not have written anything yet.
> 5. *"Yes"* → the LAS is written **and the three-track chart appears in the same reply**.
> 6. The chart **zooms across all three tracks together** and shows **tooltips on hover**.
>
> Throughout: **no raw JSON anywhere in the agent's text.** A single `<a2a_datapart_json>` blob
> visible as prose fails the gate even if everything else renders.
>
> Because the file being rendered is the one Phase 4 wrote, this single gate proves both the
> display path and that the two halves of the architecture meet at the file.
>
> **Do not tick this on a green deploy.** A deploy proves the code shipped, not that the
> surface renders — the Gate 0 post-mortem lesson.
>
> **Turn 3 is the one genuine unknown.** Everything else has been measured offline. Whether
> Gemini Enterprise's content security policy permits a `data:` image can only be found out in
> the browser. If it is blocked, the fallback is a signed URL, which needs
> `roles/iam.serviceAccountTokenCreator` on the agent service account for itself.

- [ ] **GATE 1 PASSED** *(supersedes Gate 4)*

---

## Phase 5 — QC and audit

- [ ] **58** · `[mod]` · `qc/range_check.py` — are the values physically plausible?
  *The per-mnemonic plausible range is domain data and belongs beside the existing scale data in
  `las/mnemonics.py`, not in a second table that can disagree with it.*
- [ ] **59** · `[mod]` · `qc/confidence.py` — **absorbs step 57 (`coverage.py`).**
  `CurveTrace.coverage_fraction` is already computed in `contracts.py`, so a separate coverage
  module would have been a wrapper over an existing property — guideline 2. Confidence combines
  that coverage with the range findings and the tracing ambiguity recorded per sample.
- [ ] **60** · `[mod]` · `qc/report.py`
- [ ] **61** · `[mod]` · `bq/index_row.py` — schema shaped to also receive mudlog hazard events later
- [ ] **62** · `[int]` · Write `<WELL>.qc.json` alongside the LAS

> [!IMPORTANT]
> **Gate 5** — every curve carries a confidence score and coverage fraction; low-confidence curves
> are **flagged in chat, not hidden**; an audit row lands in BigQuery.

- [ ] **GATE 5 PASSED**

---

## Phase 6 — Interaction loop

- [ ] **63** · `[mod]` · `render/a2ui_controls.py` — depth-window input, re-render button, HITL approval
- [ ] **64** · `[int]` · Handle the returning `userAction` turn in `integration/agent.py`

> [!IMPORTANT]
> **Gate 6** — the user narrows the depth window in the UI and the chart re-renders at higher
> resolution from the same LAS.

- [ ] **GATE 6 PASSED**

---

## Phase R — Release: deployed and running

*The finish line. Everything before this was a dev-loop deploy; this is the one that stays up.*

- [ ] **65** · `[test]` · Full local regression — `adk web`: digitise → LAS in GCS → render → zoom → re-render
- [ ] **66** · `[env]` · Pin runtime config — model, region `asia-south1`, service account, env vars — no secrets in code
- [ ] **67** · `[env]` · Deploy to Agent Runtime **running as** `log-digitiser-agent@…`
  ✅ Deployment reports healthy; resource name captured
- [ ] **68** · `[test]` · Verify the deployed agent card advertises the A2UI extension *and* negotiates it at runtime
- [ ] **69** · `[env]` · Publish to Gemini Enterprise / Agent Registry
- [ ] **70** · `[test]` · **Production smoke test in GE chat** — three prompts, from a clean session:
  1. "List the scanned logs available" → returns `Well_log_schlum.jpg`
  2. "Digitise it" → LAS + `.qc.json` appear in `Digitised Well Logs/`, confidence reported
  3. "Show me the log" → interactive three-track chart renders, zoom and tooltips work
- [ ] **71** · `[test]` · Confirm the run appears in GCS Data Access audit logs **attributed to the agent's own SA**
- [ ] **72** · `[env]` · Record in `EXECUTION.md`: resource name, GE agent ID, SA, catalog version, date
- [ ] **73** · `[env]` · Commit and push *(ask first)*

> [!IMPORTANT]
> **Gate R — DONE.** A colleague with GE access, given no instructions, can ask the agent to
> digitise the scan and gets back an interactive log plot.

- [ ] **AGENT LIVE** 🎉

---

## Deferred — Phase 7 hardening (post-launch, not blocking)

**Geometry correction for real scans — deferred from Phase 2 on 2026-09-18.** None of these can
be tested until we have a genuinely distorted image; until then step 44's guard refuses such input
rather than silently mis-calibrating it.

- [ ] **29** · `preprocess/deskew.py` — rotation *(subsumed by 34; build only if 34 is overkill)*
- [ ] **30** · `preprocess/denoise.py` — scan noise and JPEG artefacts
- [ ] **34** · `preprocess/rectify.py` — perspective correction by homography from gridline intersections
- [ ] **A distorted test image** — photograph a printed log at an angle. *Do this first: without it
  the three steps above cannot be verified, only written.*

**Original hardening list**

- [ ] Multi-page PDFs
- [ ] Metric wells
- [ ] Non-standard track counts
- [ ] `deleteSurface` lifecycle cleanup
- [ ] Batch digitisation of a whole folder

> [!NOTE]
> **"Curve-crossing recovery" has been removed from this list.** It is not hardening — it is the
> continuity prior inside step 48+49, and it is the main accuracy lever in the project. Leaving it
> deferred would have meant shipping a tracer that guesses at every crossing.

---

## Standing rules — apply at every step

| Rule | Why |
|---|---|
| **[CODING_GUIDELINES.md](./CODING_GUIDELINES.md) applies to every file** | Commented for readability · only code that does something · to the point and operational |
| **One step = one increment of work, not necessarily one file** | Split where each half has a contract worth asserting on and can fail independently. Merge where splitting forces a lossy interface — see the 2026-09-18 consolidation note. Every module still carries its own tests |
| `[mod]` files are pure and standalone; `[int]` files only call and pass | Logic never hides in the wiring |
| **Never pass a gate on a partial result** | A gate exists to stop bad foundations |
| **No hardcoded values presented as computed** | The predecessor agent failed exactly this way |
| **Report gaps, never interpolate across them** | Digitisation is inference, not measurement |
| **No commit or push without asking** | Your standing instruction |
