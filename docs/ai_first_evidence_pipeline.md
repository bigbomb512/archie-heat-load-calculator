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

### Ground-contact envelope method

Ground-contact floors use a separate `ground_contact_fixed_v1` method gate. The
method is steady-state only: an eligible surface needs an approved named
engineer gate, a reviewed construction/U-value, a cited owning room and area,
and an explicit cited ground temperature or complete 24-hour temperature
profile. No soil dynamics, groundwater response, or outdoor-temperature
fallback is permitted. The gate and temperature evidence are included in
calculator-input and report fingerprints; changing either makes dependent
snapshots stale without rewriting historical reports.
