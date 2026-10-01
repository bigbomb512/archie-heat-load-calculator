# Cooling workflow runbook

This runbook exercises the private evidence-to-calculator workflow. It does
not create a CAMEL+/DA09 benchmark and it does not approve engineering inputs.

## 1. Clean checkout

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
PYTHONPATH=. .venv/bin/python -m backend.web_app --port 8000
```

The browser application is served by the Python backend. A static copy of
`frontend/` is not a working calculator because it needs the project APIs.

## 2. Prepare a private evidence case

Use an existing local reviewed evidence packet. The source PDF is referenced
by path and must remain outside GitHub. Prepare a manifest with a real reviewer:

```json
{
  "project_id": "private-reviewed-cooling-case",
  "reviewer": "ENGINEER-ID",
  "reviewed_at": "2026-09-07",
  "approved_scope": "Existing supported hourly cooling only",
  "source_pdf": "/private/path/drawing-set.pdf",
  "unresolved_inputs": [],
  "exclusions": ["infiltration", "heating", "AHU", "plant", "annual analysis"]
}
```

Then run:

```bash
PYTHONPATH=. python3 tools/create_reviewed_cooling_case.py \
  --source-dir /private/path/evidence-packet \
  --output-dir output/web_review/private-reviewed-cooling-case \
  --review-manifest /private/path/review_manifest.json
```

If the packet already contains `drawing_coverage.json`, `building_evidence.json`,
`architect_evidence_fusion.json`, and `calculator_draft.json`, bootstrap the
calculator-side artifacts without rewriting any authored file:

```bash
PYTHONPATH=. python3 tools/create_reviewed_cooling_case.py \
  --source-dir /private/path/evidence-packet \
  --output-dir output/web_review/private-reviewed-cooling-case \
  --bootstrap
```

Bootstrap creates only missing `project_context.json`,
`calculator_input_overrides.json`, and `hourly_load_model.json`. Topology copied
from the draft is provisional and carries candidate provenance; it does not
confirm a floor, room geometry, area, envelope, or conditioned scope. Run it
repeatedly as needed: existing files are preserved byte-for-byte. The API then
requires the one explicit **Assemble cooling inputs** action before calculation.

The normal prepare command fails if the packet has no source PDF or the
manifest has no named reviewer. It creates a proposal-only
`calculator_draft.json`; it never fills missing rooms, schedules, loads, or
envelope values. Bootstrap also requires a source PDF, but can reuse the
reviewer recorded in an existing private manifest.

The tool verifies the derived coverage artifact against the `ai_input.json`
source fingerprint and page count. Empty or stale coverage is rebuilt in
memory and marked with a rebuild reason. Each source page receives a
proposal-only role (`supporting_geometry_plan`, `reflected_ceiling_plan`,
`services_or_lighting_plan`, `reference`, and so on) with page citation,
confidence, and authority status. A role is never treated as engineer-approved
just because the classifier selected it.

For pages with readable spatial OCR, room-label candidates are retained as
medium-confidence evidence. A label does not create an area, ceiling height,
occupancy, schedule, equipment duty, U-value, or cooling load. Missing floor
identity, geometry, and supported inputs remain explicit review items and keep
the case blocked or draft.

## 3. Review and apply

In the browser:

1. Build the Evidence-to-Calculator Draft.
2. Start in the **Geometry review workspace**. Page groups and room cards show
   the linked plan, finish, ceiling/service, elevation and 3D cross-check
   evidence. 3D pages are visual witnesses only and cannot supply dimensions.
3. Resolve only the displayed exceptions: floor identity, room boundary,
   area evidence and conflicting witnesses. A current calibrated reviewer
   trace is linked to the room and shown with its proof ID and area.
4. Review the floor, then the zone's floor mapping, then the room. For a room
   with a current calibrated trace, choose **Accept traced geometry**; do not
   type a geometry status or reference. Accept the linked area candidate only
   after checking its page citation, reviewer and calibration details.
5. Save the review.
6. Preview changes and resolve conflicts or missing dependencies.
7. Apply reviewed changes. Topology is applied in floor → zone → room order;
   authored records are never overwritten.
8. Complete supported room cooling inputs in the hourly editor.

Room and trace source fingerprints are part of draft freshness. If the source
PDF, vector page, room registry or trace changes after review, rebuild and
review the draft again. The accepted trace adds only its room area; it does not
complete the room's occupancy, schedules, gains, ventilation or envelope data.

Accepted evidence is not automatically a complete cooling input. Occupancy,
schedules, setpoints, internal gains, envelope properties, and source status
must still pass hourly readiness validation.

The envelope editor exposes separate method gates for infiltration, reviewed
glazing/manual solar, geometric shading, and ground-contact floors. A gate is
an engineering-method approval record, not a substitute for room-specific
geometry, construction, boundary, schedule, or source evidence. Unapproved or
incomplete records remain visible but do not enter a complete project duty.

## 4. Calculate and interpret readiness

Use `POST /api/hourly-load-report` with selected scenario IDs. Results mean:

- `blocked`: required inputs, topology, evidence, or supported scope are missing;
- `draft`: useful included-scope numbers exist, but inputs or room scope remain provisional/incomplete;
- `review_ready`: every active room has complete confirmed supported inputs;
- `validated`: unavailable until authorised benchmark evidence exists.

Only `review_ready` results expose a complete project peak. Draft results use
included-scope subtotals and list omitted rooms/components.

### Safety-factor placement

- **Legacy room calculation:** each cited room factor is applied once to that
  room's hourly subtotal before same-hour zone/floor/project aggregation.
- **Approved project policy:** room factors are neutralized; one cited,
  engineer-approved policy is applied to the selected coincident project peak.
  Cited room factors above 1.0 remain in the report as evidence and do not
  block this mode. The calculation blocks only if a factor above 1.0 actually
  survives into calculated room hours, which would compound with the project
  policy.
- **AI preliminary:** the named provisional 1.10 fallback is applied once and
  the result remains draft-only. It is not an approved project policy.
- **AHU and plant:** AHU room reconciliation uses unfactored room subtotals;
  coil duty comes from the air-state calculation. Plant aggregates AHU coil
  duties and does not apply another safety factor.
- **Annual:** annual room-hour cooling and heating currently use legacy room
  factors. The result reports the factor and its room-hour basis; a missing
  annual heating factor blocks that heating hour.

The preliminary workflow persists its fallback artifact. Whether a later
strict report should ignore that fallback and use legacy room factors or
remain blocked is unresolved; do not treat the preliminary factor as approved.

### Trace a room boundary for evidence

In Geometry & Evidence Review, select a known room and a rendered plan page.
Choose **Trace boundary**, click the room's corners in order, and close the
polygon. Snapping to vector endpoints/intersections is optional. Choose **Set
printed dimension**, click both ends of a printed dimension on that sheet, and
enter the printed value in millimetres. Archie compares that measured scale
with the plan's declared scale; a mismatch or missing declared scale leaves the
area unresolved. A second printed dimension can replace a rejected declared
scale only when both reviewer measurements agree within 2%.

Save with reviewer initials/name. The result records its source page and
fingerprints and remains `geometry_proposed`; it is visible as a proposed area
candidate but is not an active calculation input. Re-trace or delete a saved
record from the same workspace. A changed source PDF or vector page makes the
trace stale, so reload and review it before use. The traced number is not an
independent answer key: verify real-drawing accuracy against an architect's
schedule or an independent CAD measurement.

## 5. Staleness and recovery

Changing requirements, schedules, design-day scenarios, topology, envelope
artifacts, or source evidence makes the report stale. Recalculate after reviewing
the changed input. Historical report files are preserved. A failed bridge apply
uses its transaction journal to restore all target artifacts before returning an
error.

## 6. Verification commands

```bash
for test in tests/test_*.py; do
  [ "$test" = "tests/test_frontend_contract.py" ] || PYTHONPATH=. python3 "$test" || exit 1
done
PYTHONPATH=. python3 tests/test_frontend_contract.py
```

The frontend browser checks are run from `frontend/` with the repository's
Playwright installation. No new calculation method is considered complete until
its method, units, citations, directionality tests, and readiness effects are
documented separately.
