# Cool / Heat Load Calculation Roadmap

## Purpose

This is the delivery roadmap for the **Cool / Heat Load Calculation** workstream. It combines the implementation checklist in [`TODO.md`](../TODO.md) with the CAMEL+ product evidence recorded in [`camel_screenshot_feature_report.md`](camel_screenshot_feature_report.md).

It is deliberately separate from the wider Archie drawing-geometry, CAD, platform and commercial work. CAMEL+ screenshots are product-reference evidence only: they identify information, calculation stages and review behaviour worth supporting; they do not authorise copying proprietary tables, default values, calculation methods or numerical results.

## Product boundary and engineering rule

The target product is an engineer-owned, evidence-first load-calculation workflow. It must:

- retain drawing/source evidence and engineer-entered numerical assumptions;
- distinguish `missing`, `provisional`, `confirmed` and `not_applicable` inputs;
- block a final result when required inputs or drawing-coverage decisions are unresolved;
- show the scenario, hour, components, safety allowance and exclusions behind every result; and
- never claim CAMEL+/DA09 parity until authorised reference cases and an approved tolerance policy demonstrate it.

The current backend is the handoff boundary for a later frontend. No CAMEL-like frontend reconstruction is included in the completed milestones.

## Executive status

The project now has a solid **V1 hourly cooling foundation**, but it is not yet a complete cooling design tool and it does not calculate heating, AHU coil load or plant duty.

### Completed foundations

- Evidence/coverage artifacts distinguish direct, inferred and missing drawing facts.
- A dedicated, cited site design-conditions packet stores engineer-entered summer/winter conditions without automatic weather lookup or transfer into calculation inputs.
- A reusable 24-hour schedule library supports weekday, Saturday and Sunday/holiday profiles with no fallback day type or default operating hours.
- A cited hourly cooling design-day scenario stores 24 dry-bulb/wet-bulb points and pressure, including physical validation that wet bulb cannot exceed dry bulb.
- A reviewed room-within-zone overlay is separate from `design_requirements.json`; it can seed draft rooms from current zones but requires engineer review.
- The hourly cooling runner calculates people, lighting, individual equipment/refrigeration, steady-state envelope conduction, manually supplied surface solar and psychrometric outside-air loads.
- Reports preserve hourly room/zone/project components, sensible/latent values, subtotal, safety allowance, design total, coincident project peak, tied peak hours, provisional/blocked state and input timestamps.
- The API persists isolated per-project artifacts and identifies stale models/reports after their source requirements change.
- The parity adapter exposes a current hourly project peak/components while deliberately retaining `final_parity_allowed: false`.

### Not yet a supported calculation result

- Heating load, heating peak timing or heating design output.
- Dynamic thermal mass, detailed glazing, geometric shading, partitions/adjacent-space heat transfer, infiltration and vapour-gain modelling.
- AHU airflow allocation, coils, fans, ducts, heat recovery, preconditioning or psychrometric state paths.
- Chiller, boiler, circuits, pumps, pipe effects, unitary-equipment inclusion or primary-plant aggregation.
- Annual/month-by-month weather simulation, CAMEL-compatible graphs/tables, printable issue reports or frontend workflows.

## Completion view by checklist area

| Area | Current state | What exists now | Next material gap |
| --- | --- | --- | --- |
| Project/site/design conditions | Partial | Cited site packet, status, summer/winter fields and API | Reviewed station/climate workflow; no default climate data |
| Schedules/peak timing | Partial | 24-hour reusable schedules; coincident room/zone/project cooling peak | AHU and plant coincidence |
| Opaque envelope/storage mass | Partial | Cited evidence and steady-state entered envelope load | Reviewed constructions, dynamic mass/conduction |
| Windows/glazing/internal shading | Partial | Opening evidence; manual solar basis per surface | Glazing/window library and detailed solar/transmission |
| External shading | Partial | Drawing evidence and manual solar schedule assignment | Geometry, solar-position and obstruction engine |
| AHU/zone/room hierarchy | Partial | Reviewed zones and room overlay linked by `zone_id` | Editable AHU → zone → room system model |
| Room physical data/airflow | Partial | Area, height, occupancy and preliminary air-balance inputs | Infiltration, vapour, minimum supply, transfer/extract/duct model |
| Internal gains | Partial | Scheduled people, lighting and equipment/refrigeration | Steam, return-air allocation, verified equipment libraries |
| Partitions/adjacent conditions | Partial | Evidence and exception flags | Boundary-condition heat-transfer model |
| HVAC system type/mapping | Not started | No authoritative system model | Engineer-selected systems and constraints |
| AHU outside air/heat recovery/preconditioning | Partial | Preliminary zone outside-air/air balance | System aggregation and air-treatment models |
| Coils/psychrometrics/fans/ducts | Partial | Outdoor-air psychrometric load and cooling safety factor | Coil, ADP/bypass, fans, ducts, heating safety |
| Chiller/boiler/circuits/plant | Not started | None | System-to-plant aggregation and auxiliaries |
| Validation/evidence/reporting | Partial | Evidence artifacts, staleness, tests and disabled parity gate | Authorised benchmarks, tolerance policy and engineer-facing issue pack |

Of the 14 calculation areas in the checklist, 12 have a deliberately limited foundation and 2 (system type/mapping and primary plant) have not started. “Partial” means the prerequisite data/evidence or a limited calculation is available; it does **not** mean the CAMEL+ capability is reproduced or ready for equipment selection.

## Current implementation inventory

### Engineer-owned calculation artifacts

Each project review folder can hold these separate files:

- `site_design_conditions.json` — cited site identity and summer/winter design basis.
- `schedule_library.json` — cited reusable hourly schedule profiles.
- `design_day_scenarios.json` — cited hourly cooling or future-heating weather scenarios.
- `hourly_load_model.json` — reviewed room overlay and schedule assignments.
- `hourly_load_report.json` — current or stale hourly cooling results.

They intentionally do not overwrite `design_requirements.json`. Saving a site condition, schedule or hourly report does not silently change the existing preliminary cooling or ventilation reports.

### Backend/API handoff

- Site design conditions: `GET`/`POST /api/site-design-conditions`
- Schedules: `GET`/`POST /api/schedules`
- Design-day scenarios: `GET`/`POST /api/design-day-scenarios`
- Hourly room model: `GET`/`POST /api/hourly-load-model`
- Hourly report: `GET`/`POST /api/hourly-load-report`

The contracts are documented in [`site_design_conditions_api.md`](site_design_conditions_api.md) and [`hourly_schedules_api.md`](hourly_schedules_api.md). The project analysis response exposes discovery URLs/statuses for these artifacts. This is the contract for the teammate’s eventual UI; the backend should remain stable while that UI is built separately.

### Key implementation locations

- Hourly calculation: [`ai/hourly_loads.py`](../ai/hourly_loads.py)
- Site-design-condition validation: [`ai/site_design_conditions.py`](../ai/site_design_conditions.py)
- HTTP/API integration: [`backend/web_app.py`](../backend/web_app.py)
- Hourly parity adapter: [`ai/parity_harness.py`](../ai/parity_harness.py)
- Targeted regression tests: [`tests/test_hourly_loads.py`](../tests/test_hourly_loads.py) and [`tests/test_site_design_conditions.py`](../tests/test_site_design_conditions.py)

## Roadmap

### Milestone 0 — Stabilise the evidence-first cooling baseline

**Goal:** Make the delivered V1 cooling capability easy to exercise, review and regression-test before extending the physics.

**Work:**

- [x] Create a clearly labelled local synthetic cooling fixture with cited synthetic inputs, schedules, a design-day scenario and a room overlay. See [Synthetic Hourly Cooling Baseline](synthetic_hourly_cooling_baseline.md). It is deliberately blocked from final calculation and must not be used for design or benchmark parity.
- [ ] Create one engineer-reviewed sample project with cited schedules, design-day scenario and room overlay; keep it clearly labelled as a test/reference case, not a benchmark.
- Add API-level validation/error examples for every blocked state so the future frontend can render actionable remediation.
- Add regression coverage for artifact migration/versioning, stale-report causes and component reconciliation at room/zone/project level.
- Produce a concise backend runbook covering artifact lifecycle: build model → review/save → calculate provisional → confirm/final request.

**Exit criteria:** a repeatable test project can generate a current provisional cooling report, explain every result line and become stale predictably when a dependency changes.

### Milestone 1 — Complete reviewed room cooling inputs

**Goal:** Close the highest-value room-data gaps without adding unapproved automatic assumptions.

**Work:**

- Expand the room overlay to carry separately cited infiltration, vapour gain, minimum supply air, extract/spill/transfer/make-up air, source-room mapping and airflow constraints.
- Add a source-specific internal-gain schema for latent and steam/process loads; retain people, lighting, equipment and refrigeration as independent entities.
- Add reviewed construction and opening references rather than free-text-only envelope fields. Preserve source/version and allow engineer overrides.
- Define explicit readiness rules for each new non-zero component and block only the affected room/scenario with actionable reasons.

**Decisions required before implementation:** approved calculation methods and units for infiltration, vapour/steam, transfer-air treatment and any diversity rules. No code rates or CAMEL defaults should be embedded without a separately approved source/basis.

**Exit criteria:** every supported non-zero room load/airflow component has source, status, units, validation and report visibility; unsupported components remain explicit exclusions.

### Milestone 2 — Reviewed envelope, glazing and shading method

**Goal:** Replace manual-only envelope/solar simplifications with controlled, auditable methods.

**Work:**

- Create versioned construction, window and shading master-data artifacts; distinguish library reference data from project-selected instances.
- Add external-surface and opening instances with orientation, geometry, construction/window reference, source drawing and review status.
- Define a reviewed glazing method covering U-value, solar basis, frame corrections, internal shading and source conditions.
- Decide whether shading remains engineer-entered hourly solar fractions or gains an approved geometric method. If geometric, define coordinates, north convention, solar-position algorithm, overhang/reveal/fins/adjacent obstruction schema and verification cases first.
- Add storage-mass only after the dynamic calculation method, accepted materials/parameters and benchmark strategy are agreed.

**Dependencies:** reliable drawing geometry/orientation evidence and an engineering-approved method. This is where the separate Archie geometry work can supply citations, but it must not supply guessed thermal performance.

**Exit criteria:** every calculated envelope/glazing/solar contribution has a traceable surface/opening/method; test cases cover invalid geometry, orientation, missing construction and expected heat-gain directionality.

### Milestone 3 — Partitions and boundary conditions

**Goal:** Model non-external thermal boundaries without hiding adjacent-space assumptions.

**Work:**

- Introduce partitions, floors and ceilings as distinct boundary surfaces.
- Use named boundary methods rather than opaque flags: outdoor offset, constant adjacent temperature, proportional ambient difference or a future named method.
- Reference adjacent rooms/zones where known; otherwise require a cited engineered adjacent condition.
- Keep cooling/heating applicability and values separately reviewed.

**Exit criteria:** reports distinguish external envelope, glazing, partitions, ground/other boundaries and their method/value/source. No adjacent condition is inferred from a title alone.

### Milestone 4 — Cooling method validation and authorised parity gate

**Goal:** Establish that the supported cooling method is reliable for its declared scope.

**Work:**

- Obtain authorised DA09/CAMEL+ or other agreed benchmark cases, with permission to use them as regression evidence.
- Reconcile the 14 input families: site/weather, schedules, occupancy/internal gains, envelope, glazing, shading, airflow, system assumptions and result scope.
- Compare room, zone, project and hourly components—not just grand totals.
- Agree tolerance rules, rounding convention, allowed exclusions and error-investigation workflow with the engineering owner.
- Enable final-parity approval only after documented success; retain it as false otherwise.

**Exit criteria:** published reconciliation cases, approved tolerance policy and repeatable component-level comparisons. This is the engineering release gate for the cooling scope, not a frontend milestone.

### Milestone 5 — Separate heating design-day engine

**Goal:** Deliver heating as a separately validated calculation, not a mirrored cooling report.

**Work:**

- Finalise winter scenarios, winter setpoints and heating-specific source/status requirements.
- Implement heating conduction, outside-air/infiltration, internal-gain credit policy, safety factors and peak timing under an approved method.
- Add tests for winter physical validity, heat-loss direction, competing heating peaks and output status.
- Keep cooling and heating reports, exclusions and readiness separate until a combined result has an approved definition.

**Dependencies:** reviewed envelope/adjacent-condition scope and approved heating method. Existing heating scenarios are storage-ready only; they are not calculated today.

**Exit criteria:** a cited, engineer-reviewed heating report with its own component trace and validated reference cases.

### Milestone 6 — AHU hierarchy and air-side system calculations

**Goal:** Turn room/zone results into a truthful system-level air-side calculation.

**Work:**

- Create an editable AHU → zone → room hierarchy with number-off, system types and evidence/approval state.
- Implement engineer-selected system constraints for the supported types; do not infer a system type from room names or screenshots.
- Aggregate room schedules/loads at the same clock hour through each AHU.
- Add outside-air aggregation, direct-to-room treatment, heat recovery, preconditioning, fan placement/heat, return/supply duct gains/leakage and coil psychrometrics only after their methods are approved.
- Calculate/report coil entering/leaving states, apparatus dew point/bypass factor where applicable, and system-specific exceptions.

**Dependencies:** validated cooling/heating room methods, airflow model and a formal system topology/schema.

**Exit criteria:** every AHU result identifies served rooms, governing hour, system/air path and all included/excluded components; its coil duty is never confused with the project room total.

### Milestone 7 — Primary plant, circuits and auxiliaries

**Goal:** Aggregate system results to chiller/boiler/circuit duties using an approved plant method.

**Work:**

- Model plant/circuit ownership, number-off, unitary-equipment inclusion/exclusion and coincident timing.
- Add approved pump heat, pipe gains/losses, diversity, water/refrigerant circuit assumptions and boiler warm-up terms.
- Produce separate chiller and boiler reports with source/status, governing time and reconciliation to included AHUs.

**Dependencies:** Milestone 6 and a formally approved plant aggregation method.

**Exit criteria:** each plant total can be traced to named systems/circuits and allowances; exclusions are explicit and tests prove that independent peaks are not incorrectly summed.

### Milestone 8 — Engineer-facing review, charts and issue package

**Goal:** Give the frontend teammate a stable, bounded presentation layer after the calculation scopes are proved.

**Work:**

- Build UI against the existing artifact APIs first: status/readiness, source citations, schedule/scenario editing, room review, stale warnings and hourly components.
- Add charts only for calculated current artifacts; label scenario, day type, status, scope and governing hour.
- Add psychrometric charts only once AHU state points are calculated by Milestone 6.
- Add PDF/print package generation from immutable/versioned result artifacts, preserving inputs, warnings, exclusions and source references.
- Consider annual/month-by-month tables, graphs and shadow animation only as later milestones with their required calendar/weather/solar methods.

**Exit criteria:** the UI cannot present stale/provisional/blocked data as final, and every issued document retains enough data to reproduce the result.

## Recommended next action

The next practical engineering task is **Milestone 0 followed by the bounded portions of Milestone 1**: exercise the existing cooling workflow with a reviewed example, then add only the missing room inputs whose calculation method has been approved. Do not start AHU, plant, annual graphs, geometric shading or a heating calculation until room cooling is validated against authorised reference cases.

## Definition of done by release level

| Release level | Meaning |
| --- | --- |
| Foundation | Data/API schema, validation and tests exist; output may be blocked or provisional. |
| Engineer-reviewable cooling | All supported room inputs are cited/reviewed; scenario/hour/component trace is available; authorised cooling benchmarks meet agreed tolerance. |
| Engineer-reviewable heating | Heating has its own approved method, citations, trace and benchmark results. |
| System-ready | AHU hierarchy, air path, coils/fans/ducts and system aggregation are validated. |
| Plant-ready | Chiller/boiler/circuit scope, coincidence and allowances reconcile to system results. |
| Issue-ready | Versioned calculation package/exports preserve status, inputs, sources, exclusions and results. |

No release level should be implied by the existence of a UI screen, a complete-looking table or a non-zero grand total.
