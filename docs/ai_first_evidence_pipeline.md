# AI-first evidence-to-load pipeline

The evidence pipeline now has a deterministic boundary between extraction and
calculation:

```text
architect PDF + saved vision response
  -> architect_evidence_fusion.json (pages, entities, normalized facts)
  -> validation and safe fact activation
  -> calculator draft / review queue
  -> existing hourly cooling engine
```

`architect_evidence_fusion.json` is schema 2. It retains the original page and
entity graph and adds a normalized `facts` registry. Every fact carries a
stable ID, source page or reference, extraction confidence, validation status,
activation status, dependencies, and a candidate fingerprint.

Only exact, conflict-free metadata and directly explicit low-risk records may
be activated automatically. Geometry, room areas, thermal properties,
occupancy, schedules, and load bases remain proposals unless their evidence
meets the current validation rules. The pipeline never converts a detected
piece of equipment into a heat load.

The evidence API supports these actions:

- `build` or `build_ai_extraction`: rebuild the fusion artifact from current
  local evidence artifacts;
- `save_extraction`: persist validated structured facts from the saved vision
  response;
- `validate_facts`: return validation results without changing artifacts;
- `apply_safe_facts`: activate only safe facts in the fusion registry;
- `save_review`: persist review decisions with optimistic revision checking.

External facts are represented by `research_cache.json`. It is a project-local
cache of cited, reviewed records; it contains no network client and calculations
never perform live web research. Expired, rejected, out-of-scope, or merely
proposed records are not eligible inputs.

The calculator input assembler now produces immutable, content-addressed
cooling snapshots in `calculator_input_sets/`. Each snapshot resolves one
field at a time using cited project overrides, explicit PDF/fusion evidence,
supported derivations, then approved Australia-first source-pack defaults.
It preserves every source, citation, formula and exclusion and does not modify
the editable project artifacts. The existing hourly engine remains the only
load calculator; it materializes a selected snapshot in memory.

Assembly is an explicit step before calculation. Reassembling unchanged inputs
reuses the same fingerprint; changed evidence, schedules, scenarios, context,
overrides, research or gate inputs produce a new snapshot. The snapshot also
contains room coverage (`included`, `excluded`, `blocked`, `draft-only`) and a
complete-scope flag so a partial result cannot be presented as a project duty.

Defaults are limited to weather, indoor conditions, safety allowance,
occupancy/people assumptions, lighting assumptions, schedules and outside-air
basis. Geometry, boundaries, U-values, glazing, construction assemblies and
equipment heat remain non-defaultable. Unsupported airflow and moisture
components are never inferred as zero: confirmed absence is explicit, while
known or unassessed loads keep the relevant result at `draft`.

A complete AI-assembled/default-backed supported cooling scope can be
`review_ready`, but it remains visibly labelled as such and is never
`validated`. Validation is reserved for the authorised benchmark gate.

## Australia-first default pack

`config/au_cooling_default_pack.json` contains the first cited candidate pack:
the NCC 2022 Class 6 shop and Class 6 restaurant/cafe daily occupancy,
lighting, and equipment profiles. `tools/seed_au_default_pack.py` can copy
those records into a private project's `research_cache.json` without copying
any PDF. The tool is idempotent and records the pack version and source
fingerprint.

The records are deliberately seeded as candidates. That means they are visible
to the resolver and review UI but cannot affect a calculation yet. A qualified
HVAC engineer must release the exact record IDs and content hashes in
`config/research_source_pack_releases.json`. Each release records the
engineer name and credential, approval date, scope, expiry, and source-pack
version. The normal project UI has no action that can release a default.

At assembly time, Archie checks the record citation, allowlisted domain,
expiry, source-pack version, release-manifest hash, and project scope. It uses
the normal precedence order: cited project override, direct PDF evidence,
validated derivation, engineer-released default, then blocked/excluded. In
particular, the current Drawing 6 room uses (`cool room` and `freezer room`)
do not silently match a generic Class 6 shop or restaurant profile. A future
explicit room-use/profile mapping or project-specific schedule is required.
The mapping is stored separately from `use`, so a room can remain named “Cool
Room” while the project explicitly selects a cited schedule profile for its
supported assumptions.

The official source is NCC 2022 Specification 35, Tables S35C2e and S35C2f.
The profiles express percentages of maximum occupancy, lighting power density,
and internal heat gain; they do not supply room areas, equipment heat-to-space,
thermal boundaries, U-values, or design-day weather. Those fields remain
blocked until project evidence or a separately released, scope-matched source
is available.

## Curating Australia-first default candidates

`tools/seed_au_default_pack.py --check` validates the checked-in candidate
pack without touching a project. It reports candidate counts by category and
calculator target, the required Cooling V1 coverage that still lacks a cited
candidate, and records that cannot be seeded. The same tool seeds only valid
candidates into a private project cache; it always writes them as `proposed`
and unreleased.

Each candidate may carry its own official-source metadata. A candidate needs
an allowlisted Australian source, citation, retrieval time, content hash,
expiry, AU scope, and only permitted low-risk bindings. Complete 24-hour
profiles are required for each declared day type. Weather candidates also need
a locality/state/climate-zone and scenario scope. Geometry, thermal
boundaries, constructions, U-values, glazing, solar/shading, and equipment
heat-to-space targets are rejected by the curation tool before a cache write.

The candidate report intentionally shows missing coverage rather than filling
it with guessed values. It is developer-only: calculations continue to use
only engineer-released, scope-matched records.

Drawing 6 remains private. The reviewed-case tool writes derived evidence to a
local output directory and never copies the source PDF into the repository.

## PDF calculation-input evidence

The Evidence-to-Calculator panel can build `calculation_input_evidence.json`.
This derived register reads page-specific evidence from dimensioned plans,
ceiling/service sheets, openings, equipment schedules, notes and sections. It
stores candidate values with page, drawing number, excerpt, unit, extraction
method, confidence and unresolved fields. Exact room-targeted values may be
active; ambiguous geometry, incomplete schedules and equipment without a
heat-to-space basis remain proposed, blocked or evidence-only.

The endpoint is:

```text
GET  /api/calculation-input-evidence?project_id=...
POST /api/calculation-input-evidence
     {"project_id":"...", "action":"build"}
```

The artifact is never an editable hourly model. The calculator-input assembler
consumes only active candidates after exact room-ID mapping, while the existing
override and draft-review paths handle conflicts. Rebuilding is content
addressed and source-fingerprinted; it does not alter schedules, envelopes,
room inputs or reports.

### Evidence binding

The calculation-input register also contains a `binding` section. It preserves
raw OCR words, table cells, image witnesses, and extracted candidates as stable
observations, then records cross-page relationships such as:

```text
room label ↔ area/ceiling/lighting value
opening tag ↔ plan opening ↔ elevation or schedule row
PDF value ↔ manual vision value
3D/render observation ↔ plan or elevation (cross-check only)
```

Bindings require an exact label/tag, compatible target, or explicit witness.
They do not use visual similarity alone. A unique plan/elevation opening match
is recorded as a sourced geometry relationship; multiple possible targets and
vision/PDF disagreements become blocking conflicts. Three-dimensional images
and render observations remain cross-check evidence and cannot provide primary
dimensions or activate a load input. The binding fingerprint is included in
the derived artifact fingerprint, so changing source evidence makes the
register stale without rewriting authored calculator artifacts.

### Ranked page discovery

`drawing_coverage.json` is the page-discovery register. It scans every
architect page, but does not treat every page as equally useful. Each page has
title/drawing-number candidates, identity status, a multi-capability map,
category-specific relevance scores, related-page links, and a selection state:
`primary_context`, `supporting_context`, `cross_check_context`,
`ranked_exception`, or `reference_only`.

Title-block OCR and explicit sheet text take precedence over flattened legacy
metadata. Dates are rejected as drawing numbers, while conflicting identities
remain ambiguous and block automatic cross-page matching. The manual and
optional provider vision handoffs consume the same ranked groups, so service,
ceiling, elevation, schedule, detail, and 3D evidence cannot disappear merely
because an older role name differed. A 3D page can strengthen or challenge a
relationship, but never supplies primary dimensions or thermal properties.

Floor identity uses a separate source-aware classifier (drawing-coverage
version 5, level-classification method 2). It preserves each match's raw text,
normalized label/key, source, nearby context, kind and reason. Finish/material
codes and “Level n Detail/Section/Elevation” labels remain evidence, while body
text levels are non-authoritative. A page receives a level automatically only
when title-block/page-title `building_level` matches agree; a reviewed page
triage label takes precedence. Conflicting title sources are ambiguous, and
missing evidence stays unassigned. Building evidence and calculator floor
candidates use only those selected levels. The source fingerprint includes the
classifier version, so saved coverage and dependent artifacts from the previous
method must be rebuilt once after upgrade; a stale coverage artifact cannot be
used to rebuild the calculator draft. This fail-closed rule can require a
reviewer to map a single-storey tenancy when its drawings do not state a level.

### Reusable geometry binding

`geometry_resolution` is a derived, project-independent evidence graph. It
retains every indexed page, identity candidate, capability, geometry entity,
witness, and cross-page relationship. Room matching is level-aware, so equal
room names on different levels remain distinct; competing same-level records
become conflicts. A uniquely room-labelled printed area may be used as an
evidence value, while polygon-derived area requires a closed calibrated boundary
and independent supporting evidence. Missing scale blocks derived geometry.

Raw vector walls and unbound dimension text remain in the geometry graph but are
not calculator fields. Plan/elevation, ceiling/service, opening/schedule, and
3D relationships retain their matching basis and citations. 3D observations
are cross-check-only. Geometry resolution writes derived evidence and draft
candidates; it does not modify the hourly model, envelope model, or reports.

### Shared room-input resolver sources

Room-use, ceiling-volume, internal-gains and airflow resolvers consume the same
`_proposal_for_resolution` output used by the preliminary pipeline. It selects
local room inference before the manual-proposal fallback and applies eligible
room-identity skill findings. Each resolver records only its own relevant
inputs and assumption-pack fingerprint; the broad preliminary source set is
reserved for model-input dependencies and is not reused as a resolver's
fingerprint set. Editor status and the portfolio scorecard use those same
fingerprint builders.

Existing room-input artifacts made with the previous broad/self-referential
fingerprint sets may show stale after this update. Resolve the affected editor
once to refresh its derived artifact. Archie does not rewrite project folders
automatically. A re-resolve drops rooms only when the current proposal no
longer includes them; ceiling-height, internal-gains, and airflow records with
engineer overrides are retained as stale and flagged for review. Airflow
overrides are keyed by room and path type, and queued research jobs survive a
re-resolve. Airflow edits are blocked while their source fingerprints are
stale, and an individual stale airflow room/path cannot be edited even when
the artifact-level fingerprints are current. Clearing an override on a stale
row removes its value but preserves its stale status. Retained stale airflow
or ceiling records remain visible in the model input review queue but are
excluded from preliminary calculation mappings.
This source alignment makes editor and pipeline state consistent; it does not
validate the underlying room data or calculation assumptions.

### Reviewer-traced room boundaries

The Geometry & Evidence Review workspace can save a room boundary traced on a
rendered plan. The editor uses the full-resolution page screenshot and enables
tracing only when its dimensions match the vector coordinate system. Zoom and
pan preserve that image-pixel coordinate system; a missing or mismatched
render disables tracing rather than stretching a thumbnail. Each vertex
optionally snaps to the current page's vector-line endpoints/intersections,
and the server rechecks referenced line IDs and the fixed pixel tolerance.
The reviewer marks a printed dimension on the same rendered page and enters
its stated millimetres. The resulting mm/px is
compared with the drawing's declared scale using the page pixel-to-point ratio;
the values must agree within 2%. If they do not, a second independently read
printed dimension may replace the declared scale only when the two reviewer
measurements agree within 2%. Missing or inconsistent calibration keeps the
trace stored but leaves area unresolved.

Traces are versioned in `reviewer_room_geometry.json` and bind the ordered,
closed polygon, per-vertex snap references, calibration, reviewer, note, page,
source-PDF fingerprint, and vector-page fingerprint. A changed PDF or vector
page makes the trace stale and excludes it from derived geometry. A valid trace
creates a `room_geometry_proof` with method `reviewer_traced_boundary`; its
area is emitted as a proposed calculation-input candidate only. The calculator
draft now links current calibrated proofs to room-use/inference room candidates
and their area candidates. A named engineer can accept the room candidate in
the draft bridge; that records the proof ID, calibration and source fingerprints
in the applied room provenance. The area enters the hourly model only through
the separate preview-and-apply step. Missing or stale proofs cannot be accepted,
and other missing room inputs still keep readiness blocked or draft. Automatic
boundary detection and vision-generated room proposals remain open work; this
editor does not change their filters.

Geometry review also includes per-page diagnostics for vector-line inclusion
reasons, loop and rejected-component counts, label filtering, dimension links,
and per-room reasons that an automatic proof was not produced. These
diagnostics explain pipeline behavior; they are not evidence that the source
geometry or traced area is correct.

### AI attempt archives

Every skill-provider attempt is retained under the local, ignored project
`output/skill_workflow_runs/<run_id>/attempts/<subskill_id>/<attempt_number>/`
folder. The archive contains the exact prompt, image paths and page numbers,
raw provider reply, CLI status and bounded stdout/stderr tails when applicable,
and an outcome record with timing, input fingerprint, failure phase, and the
specific validation check, field path, and safe diagnostic detail. A proposal
and the run manifest link to the archive with `attempt_ref`; rejected replies
remain available for diagnosis and are not accepted as calculation evidence.
The validator and prompts are unchanged by this archive mechanism. Raw replies
can contain drawing content, so these artifacts remain local and must not be
copied into version control or shared without project authorization.

Manual vision-response pastes use a separate `output/chatgpt_runs/` history.
Each attempt preserves the raw reply before parsing, records packet pages
separately from the optional pages actually attached, and remains visible in
the history whether accepted or rejected. Acceptance is recorded only after
the full evidence-chain rebuild succeeds; a failed rebuild restores the prior
accepted response. These archives improve diagnosis and traceability, not
extraction accuracy or engineering validation.

Snap-to-vector tolerance is 8 image pixels and is only a placement aid; it does
not classify a vector line as a wall or verify that a snapped corner is the
correct room boundary. PDF fingerprinting, page-size lookup, and candidate
intersection geometry are cached against source-file and vector-page
fingerprints to avoid repeating expensive work while those inputs are unchanged.

### Scoped AI task prompts

Each skill task receives a scoped prompt rather than the whole project
evidence package. This is a product decision, not only a test convenience:
provider cost, input limits and reviewability all scale with prompt size.

- Generic domain tasks no longer receive unvalidated vector `wall` and
  `dimension` candidates from `geometry_resolution.json`; only confirmed or
  AI-estimated geometry of those kinds is passed. Glazing and construction
  tasks receive only value-resolution records whose target matches their
  domain.
- Before sending, resolver bookkeeping (hashes, fingerprints, witness lists,
  timestamps, attempt and output summaries) is removed and text longer than
  600 characters is truncated with an explicit `…[truncated N chars]` marker.
  Prerequisite tasks are passed as their validated proposal content only.
- The room-boundary task keeps the complete primary-plan vector line index so
  it can cite real line IDs, encoded as `[candidate_id, x1, y1, x2, y2, role]`
  in whole image pixels, with dimension and OCR label candidates in the same
  compact form.
- Every task has a character budget: 80,000 by default and 120,000 for
  `room_boundaries_areas`. `ARCHIE_SKILL_PROMPT_MAX_CHARS` overrides it for all
  tasks. A task whose scoped prompt is still over budget is **blocked before
  any provider is created or called**, with the prompt size and its largest
  sections in the remediation. Evidence is never silently dropped to fit.
- Each attempt archive and run manifest row records the prompt size, budget,
  status, and largest sections (`prompt_budget`).
- The skill workflow accepts `scope`: `all` (default) or `rooms_only`. The
  rooms-only scope runs page identity, revision scope, page relationships,
  room identity and use, room boundaries and areas, and ceiling height and
  volume; other tasks are recorded as `not_in_scope`. Test mode exposes it as
  the `codex_rooms_only` scenario.

On the archived Butcher Buffet Codex run, these rules reduced the 24 prompts
from 5,565,061 to 893,718 characters; the largest task fell from 3,984,817 to
about 40,000. The six rooms-only tasks total about 257,000 characters. These
are measured prompt sizes from that archive; page images are sent as before,
and the effect on extraction quality has not yet been compared on a new run.

### Ground-contact envelope method

Ground-contact floors use a separate `ground_contact_fixed_v1` method gate. The
method is steady-state only: an eligible surface needs an approved named
engineer gate, a reviewed construction/U-value, a cited owning room and area,
and an explicit cited ground temperature or complete 24-hour temperature
profile. No soil dynamics, groundwater response, or outdoor-temperature
fallback is permitted. The gate and temperature evidence are included in
calculator-input and report fingerprints; changing either makes dependent
snapshots stale without rewriting historical reports.
