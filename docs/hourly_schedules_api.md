# Hourly Schedules and Peak Timing API

This is the backend contract for the authoritative hourly cooling design-day workflow. The older flat cooling report is read-only legacy history. All endpoints require `project_id`; all saved artifacts live in that project's review folder.

## Evidence and readiness

Every input status is one of `missing`, `provisional`, `confirmed`, or `not_applicable`. Values are never defaulted, interpolated, or copied from site conditions. A response returns the artifact, `readiness`, an artifact URL, and an artifact status. A report is `blocked`, `draft`, or `review_ready`; `validated` is reserved for the later authorised benchmark gate.

`confirmed` schedules and scenarios require sources. A review-ready calculation requires confirmed complete-scope inputs and no unresolved drawing-coverage exceptions. Saving any artifact does not update `design_requirements.json`, legacy cooling reports, or ventilation reports.

## Evidence-to-calculator draft bridge

`GET /api/calculator-draft?project_id=...` retrieves the project-local
`calculator_draft.json`. `POST /api/calculator-draft` accepts these actions:

- `build` rebuilds source-backed candidates from thermal, building-evidence and
  drawing-coverage artifacts.
- `save_review` persists `accept`, `edit`, `reject`, or `needs_evidence`
  decisions with reviewer attribution and citations. It does not change calculator artifacts.
- `preview_apply` returns additive records, fields to populate, already-present
  values, authored conflicts, missing dependencies and unresolved evidence.
- `apply` requires the expected draft revision and preview token, then writes only
  valid accepted records. Existing populated fields and active envelope surfaces
  are never overwritten by the bridge.

Draft schema 2 stores source-content and candidate fingerprints, evidence IDs,
source page/excerpt, confidence, decision history and application receipts.
Changing source evidence or a target artifact invalidates a stale browser review;
the API returns a structured `409` conflict. A repeated no-op apply does not
rewrite target artifacts or their timestamps. Accepted records retain bridge
provenance when edited later in the hourly or envelope editors.

## Schedules

`GET /api/schedules?project_id=...` retrieves `schedule_library.json`.

`POST /api/schedules` saves a library. A schedule has a stable lowercase `schedule_id`, a title, evidence, and all three explicit day profiles: `weekday`, `saturday`, and `sunday_holiday`. Each supplied profile has exactly 24 values indexed in order from hour 0 through 23, constrained to `0.0`–`1.0`. There is no fallback between day types.

```json
{
  "project_id": "example",
  "schedule_library": {
    "schedules": [{
      "schedule_id": "retail_people",
      "title": "Retail occupancy",
      "status": "confirmed",
      "source": "Engineer design brief, rev C",
      "citations": [{"reference": "Design brief", "location": "p. 3"}],
      "day_profiles": {
        "weekday": {"status": "confirmed", "source": "Design brief, p. 3", "citations": [], "values": [0,0,0,0,0,0,0,0,0.5,1,1,1,1,1,1,1,1,1,0.5,0,0,0,0,0]},
        "saturday": {"status": "missing", "source": "", "citations": [], "values": []},
        "sunday_holiday": {"status": "missing", "source": "", "citations": [], "values": []}
      }
    }]
  }
}
```

The schedule semantic is a generic load fraction. Assign it explicitly to people, lighting, each heat source, outside air, and each solar-bearing surface as applicable.

## Design-day scenarios

`GET`/`POST /api/design-day-scenarios` manages `design_day_scenarios.json`. Each scenario has a stable ID, title, mode (`cooling` or `heating`), representative month, day type, evidence status, source, citations, and pressure field. Cooling and heating scenarios have 24 distinct rows `0`–`23`; every row carries cited outdoor DB and WB fields, with `WB <= DB`. Heating scenarios are calculated through the separate `/api/hourly-heating-load-report` path and never alter cooling reports.

## Hourly room model

## Calculator input assembly and scoped defaults

`GET /api/calculator-inputs?project_id=...` returns the latest immutable input
snapshot when one exists, plus the current project context, cited overrides,
room coverage, grouped exceptions and the current assembly fingerprint. If
source inputs changed after the snapshot was created, the response marks it
`stale` and provides the current assembly status; calculation requires a new
explicit assembly. It never writes calculator artifacts.

`POST /api/calculator-inputs` supports these actions:

- `save_context` writes the minimum project decision record: Australia-first
  locality/use context and one cited conditioned-room scope declaration.
- `save_override` writes one cited, reviewer-attributed project override. It
  requires a stable target path, value, unit, source and citation.
- `assemble` resolves the current inputs and creates (or reuses) an immutable,
  content-addressed snapshot in `calculator_input_sets/<fingerprint>.json`.
  The pointer file `calculator_input_set.json` only identifies the latest
  snapshot; it does not rewrite historical snapshots.
- `save_research_record` retains a cited project-local research candidate for
  compatibility. This route always stores it as `proposed`; only a released,
  approved source-pack record can become eligible automatically.
- `refresh_research` may request an allowlisted collection worker when one is
  configured. This local application deliberately has no live research worker;
  calculation always reads the existing local cache.

For every target calculator field the resolver uses this fixed precedence:

1. cited project override;
2. explicit, valid project evidence;
3. supported derivation from resolved evidence;
4. approved, current, scope-matched Australia-first source-pack default;
5. blocked or excluded.

The snapshot retains each value, unit, target path, source IDs, citations,
scope, resolution status and, for derived values, formula, operands, rounding
policy and competing candidates. It never defaults room geometry, boundaries,
U-values, glazing performance, construction assemblies or equipment heat.
Source-pack updates and overrides do not alter historical reports. A deliberate
reassembly creates a new snapshot and the changed dependency makes any report
that used the prior snapshot stale.

`GET /api/hourly-load-model?project_id=...` retrieves `hourly_load_model.json`.

`POST /api/hourly-load-model` supports two actions:

- `{"project_id":"...","action":"build"}` seeds one **inferred**, provisional room per existing design zone. It also creates one provisional `unassigned` floor and maps each migrated zone to it; it never guesses a real level.
- `{"project_id":"...","action":"save","hourly_load_model":{...}}` saves the engineer-reviewed model.

Schema-v4 stores a topology before the existing room load inputs: each floor has a stable ID, name, optional elevation, review status, source and citations; each zone has a stable ID, name, optional cited ceiling height and valid `floor_id`; each room has a stable ID, name, optional cited ceiling height and valid `zone_id`. A room therefore belongs to a floor only through its zone. Schema-v1 through schema-v3 models are normalised in memory on `GET`; only an explicit save writes schema v4.

Rooms retain source labels and mapping evidence, their own area/occupancy/setpoint/static cooling inputs/conditions, heat sources with stable source IDs, surfaces with existing surface IDs, and `schedule_assignments`:

```json
{
  "people": "retail_people",
  "lighting": "retail_lights",
  "outside_air": "retail_ventilation",
  "infiltration": "retail_infiltration",
  "equipment": {"room-a-source-1": "refrigeration"},
  "solar": {"surface-north": "north_solar"}
}
```

Non-zero timed drivers need a valid profile for the scenario day type. A zero or explicitly not-applicable driver needs none. The model records the saved `design_requirements.updated_at`; it is stale when that revision changes.

Each room also has `unapproved_components`. The calculator creates one record for every currently unsupported input family: infiltration, minimum supply, extract, spill, transfer and make-up air; vapour, steam and process latent loads. A record has a stable `component_id`, `component_type`, raw `value` and `unit`, evidence `source` and `citations`, `verification_status`, and one `calculation_status`:

- `not_present_confirmed`: a cited, engineer-confirmed declaration that the component is absent. It has no value or unit.
- `stored_not_calculated`: a positive, cited raw value in an accepted capture unit. It is deliberately excluded from the cooling total until its method is approved.
- `not_assessed`: neither absence nor a source-backed value is known yet.
- `calculated`: available only for `infiltration` after the project-local
  approved method gate is active. It requires a source, citation, approved
  method ID, uncontrolled-air-path declaration and outdoor-condition flow
  reference.

Transfer-air records may reference an existing `source_room_id`; no other component type may do so. Stored input units are captured but never converted in this release: airflow accepts `L/s`, `m3/s`, or `m3/h` (with `ACH` for infiltration); moisture/process input accepts `kg/h`, `g/h`, or `W`.

## Hourly report

`GET /api/hourly-load-report?project_id=...` returns the saved report and whether all input artifacts are current.

`POST /api/hourly-load-report` runs it:

```json
{
  "project_id": "example",
  "selected_scenario_ids": ["summer_design_day"]
}
```

To calculate from an assembled snapshot, add its immutable fingerprint:

```json
{
  "project_id": "example",
  "input_set_fingerprint": "8af..."
}
```

The endpoint materializes that snapshot in memory and never writes its resolved
values back to editable room, schedule, requirements or envelope artifacts.

`GET /api/infiltration-method-gate?project_id=...` retrieves the project-local method gate. `POST /api/infiltration-method-gate` saves the fixed V1 policy plus its approval record. A placeholder gate is visible but cannot contribute to cooling totals; an approved gate requires a named HVAC engineer, credential, date, citation and stated scope.

`GET /api/ground-contact-method-gate?project_id=...` and `POST
/api/ground-contact-method-gate` manage the separate ground-contact floor method
gate. A ground-contact surface remains excluded unless the gate is approved and
the surface has a cited scalar ground temperature or a cited 24-hour boundary
temperature profile. The gate does not infer soil properties, groundwater
response, or outdoor temperature, and it is included in calculator/report
freshness fingerprints.

For every hour, the engine schedules people, lights, heat sources, solar, outside air and eligible infiltration; calculates envelope conduction at hourly outdoor DB; calculates psychrometric outside-air and infiltration sensible/latent load at hourly DB/WB/pressure; then applies the existing explicit safety factor once. ACH infiltration uses reviewed room volume or a cited zone-height fallback. The report retains signed infiltration diagnostics while applying only positive sensible and latent cooling components. Floors aggregate zones and zones aggregate rooms at the **same hour**, never independent room peaks.

The report exposes `known_exclusions` for stored uncalculated room inputs and `unresolved_room_inputs` for unassessed categories, separately from the calculated hourly components. A known excluded or unassessed room component makes the result `draft` and removes the project peak, while retaining an included-scope subtotal for engineering review.

V1 supports reviewed fixed-temperature partitions, manually-sourced glazing inputs, and geometric-shading records only when their separate method gates and evidence requirements are complete. Dynamic thermal mass and cited surface-irradiance radiation are also available behind their separate Stage 6 gates and explicit surface/source records; they remain excluded while those gates or records are incomplete. Room-to-room dynamic coupling, AHU coil and fan/duct effects, heat recovery, and plant loads remain excluded. Infiltration remains excluded until its project gate and room input are eligible. The analysis response exposes each artifact URL/status for frontend discovery. A draft may show only an included-scope subtotal; a project peak is available only for review-ready complete scope. The parity adapter remains disabled until an authorised CAMEL+/DA09 reconciliation is completed.
## Calculation-input evidence

Before assembling an immutable calculator snapshot, the frontend may build the
derived PDF evidence register:

```text
GET  /api/calculation-input-evidence?project_id=...
POST /api/calculation-input-evidence
     {"project_id":"...", "action":"build"}
```

The register extracts cited numerical candidates from architect plans,
reflected-ceiling/service pages, openings/elevations, equipment schedules,
general notes and sections. It does not apply defaults or change authored
calculator artifacts. Equipment presence/nameplate power is retained as
evidence unless a heat-to-space basis is explicit. Active candidates are
consumed by the existing calculator-input precedence and remain traceable to
their source page and excerpt.

Each response also includes `calculation_input_evidence.binding`, containing
stable observations and relationships between OCR/table/image witnesses,
plan/elevation opening tags, and manual vision records. `binding.conflicts`
lists ambiguous matches and source disagreements. A 3D/render relationship is
marked `cross_check_only` and cannot activate geometry or a cooling input.
