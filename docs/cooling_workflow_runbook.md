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
   geometry status, area evidence and conflicting witnesses.
4. Choose `accept`, `edit`, `reject`, or `needs_evidence` on the linked
   proposal below the room card.
5. Save the review.
6. Preview changes and resolve conflicts or missing dependencies.
7. Apply reviewed changes. Topology is applied in floor → zone → room order;
   authored records are never overwritten.
8. Complete supported room cooling inputs in the hourly editor.

Accepted evidence is not automatically a complete cooling input. Occupancy,
schedules, setpoints, internal gains, envelope properties, and source status
must still pass hourly readiness validation.

## 4. Calculate and interpret readiness

Use `POST /api/hourly-load-report` with selected scenario IDs. Results mean:

- `blocked`: required inputs, topology, evidence, or supported scope are missing;
- `draft`: useful included-scope numbers exist, but inputs or room scope remain provisional/incomplete;
- `review_ready`: every active room has complete confirmed supported inputs;
- `validated`: unavailable until authorised benchmark evidence exists.

Only `review_ready` results expose a complete project peak. Draft results use
included-scope subtotals and list omitted rooms/components.

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
