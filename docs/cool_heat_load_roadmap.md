# Cool / Heat Load Calculation Roadmap

## Purpose

This is the delivery roadmap for the **Cool / Heat Load Calculation** capability within Archie. It combines the implementation checklist in [`TODO.md`](../TODO.md) with the CAMEL+ product evidence recorded in [`camel_screenshot_feature_report.md`](camel_screenshot_feature_report.md). Its milestones describe capability areas and validation gates; they are not a promise that completing one drawing or project completes the product.

It is deliberately separate from the wider Archie evidence-ingestion, CAD, platform and commercial work, while sharing a common product goal: handle varied project evidence and produce traceable, appropriately validated HVAC design outputs. CAMEL+ screenshots are product-reference evidence only: they identify information, calculation stages and review behaviour worth supporting; they do not authorise copying proprietary tables, default values, calculation methods or numerical results. The Butcher Buffet Melrose Park / Drawing 6 case is one project example and regression target, not the product's finish line.

## Product boundary and engineering rule

The target capability is an engineer-reviewable, evidence-first load-calculation workflow that can generalize across projects. It must:

- retain drawing/source evidence and engineer-entered numerical assumptions;
- distinguish `missing`, `provisional`, `confirmed` and `not_applicable` inputs;
- block a final result when required inputs or drawing-coverage decisions are unresolved;
- show the scenario, hour, components, safety allowance and exclusions behind every result; and
- never claim CAMEL+/DA09 parity until authorised reference cases and an approved tolerance policy demonstrate it.

The current backend is the handoff boundary for a later frontend. No CAMEL-like frontend reconstruction is included in the completed milestones.

These milestones measure progress in the heat-load capability, not completion of all of Archie. A project-specific case can test a capability, but cannot establish coverage for other drawing sets, building uses, climates or HVAC systems. Track generalization through a varied, permissioned evaluation set and report its coverage and limits.

## Executive status

The current checkout contains a **gated calculation chain** from hourly room cooling loads through supported cooling AHUs to chilled-water chillers, plus a separate room-heating runner and annual room analysis. These are calculation implementations, not an engineer-approved or benchmark-validated design tool. No real authorised DA09/CAMEL+ comparison case or approved engineering method gate is present in the repository.

### Completed foundations

- Evidence/coverage artifacts distinguish direct, inferred and missing drawing facts.
- A dedicated, cited site design-conditions packet stores engineer-entered summer/winter conditions without automatic weather lookup or transfer into calculation inputs.
- A reusable 24-hour schedule library supports weekday, Saturday and Sunday/holiday profiles with no fallback day type or default operating hours.
- A cited hourly cooling design-day scenario stores 24 dry-bulb/wet-bulb points and pressure, including physical validation that wet bulb cannot exceed dry bulb.
- A reviewed room-within-zone overlay is separate from `design_requirements.json`; it can seed draft rooms from current zones but requires engineer review.
- The hourly cooling runner calculates people, lighting, individual equipment/refrigeration, steady-state envelope conduction, manually supplied surface solar and psychrometric outside-air loads.
- A direct-to-room moisture-gain path calculates latent load from cited water-vapour generation rates or directly supplied latent watts, applies a dedicated hourly schedule, and remains draft until its project method gate is approved. Steam is accepted only as a directly supplied latent-watt value; steam source-state energy is not inferred.
- A room-to-room transfer-air path calculates signed sensible and latent exchange from cited flow, source-room and receiving-room psychrometric states, and a dedicated hourly schedule. It remains draft pending project-specific HVAC engineer review; this V1 does not solve network pressure/flow balance.
- First-order RC thermal mass and two-room partition coupling have isolated implementations behind separate method gates; they do not activate from the presence of input records alone.
- The cooling AHU adapter calculates supported air-path, mixed-air and coil states plus cited fan/duct/recovery/preconditioning effects from explicit reviewed inputs. The cooling plant adapter aggregates coincident AHU load to reviewed chiller/chilled-water circuits with explicit pump and pipe allowances. Both remain draft until their own engineer gate and complete reviewed inputs are supplied.
- The site-location and design-weather resolvers can prepare sourced proposals; calculation inputs still require explicit review and transfer.
- Reports preserve hourly room/zone/floor components, sensible/latent values, subtotal, safety allowance, design total, included-scope peak, blocked rooms, readiness state and input timestamps. A project peak is shown only for review-ready complete scope.
- The API persists isolated per-project artifacts and identifies stale models/reports after their source requirements change.
- The parity adapter can expose complete-scope hourly components while deliberately retaining `final_parity_allowed: false`; it does not treat a draft subtotal as a project duty.
- The evidence-to-calculator bridge creates source-backed, fingerprinted proposals and applies only explicit engineer decisions through a preview/conflict-controlled workflow. It does not approve evidence or fill missing calculation inputs.

### Not yet a supported calculation result

- Engineer-approved method gates and real reference validation for cooling, heating, AHUs, plant and annual calculations.
- Moisture diffusion through the building fabric; steam-source sensible energy; and zone moisture sources exhausted before mixing. Exhausted-source replacement air belongs in the air-side calculation.
- Room-level thermal effects for minimum supply, extract, spill and make-up air; these remain outside the room load until explicit system boundaries avoid overlap with direct outside air and AHU calculations.
- Heating AHU/coil and boiler-system/plant calculations, including approved warm-up and hydraulic methods. Current primary-plant calculations cover cooling chillers and chilled-water circuits only.
- Additional AHU system types, complete refrigerant/heat-pump and package-unit aggregation, and annual AHU/plant state calculations.
- Any result requiring an engineer gate remains draft until a qualified reviewer enters a real approval; do not populate gates with invented reviewer details.

## Completion view by checklist area

| Area | Current state | What exists now | Next material gap |
| --- | --- | --- | --- |
| Project/site/design conditions | Partial | Cited site packet and site/weather proposal resolvers | Engineer-reviewed transfer of location-specific design conditions |
| Schedules/peak timing | Partial | 24-hour reusable schedules; coincident room/zone/project cooling peak | AHU and plant coincidence |
| Opaque envelope/storage mass | Partial | Steady-state conduction; first-order RC method behind its own gate | Engineer-reviewed constructions and RC validation |
| Windows/glazing/internal shading | Implemented behind review gate | Reviewed opening/window records; manual hourly solar; separate conduction and solar audit | Complete project evidence and benchmark validation |
| External shading | Controlled V1 behind review gate | Cited overhang/fin/reveal/obstruction geometry and cited hourly sun vectors replace the manual external factor | Solar-position sourcing, diffuse/dynamic shading and annual analysis |
| AHU/zone/room hierarchy | Partial | Reviewed zones/rooms and an explicit cooling AHU → zone → room adapter | Complete topology review and additional system types |
| Room physical data/airflow | Partial | Outside air, gated infiltration and draft room-to-room transfer psychrometrics; evidence/balance inputs retained | Engineer validation plus exhaust, spill, minimum supply and make-up paths |
| Internal gains | Partial | Scheduled people, lights, equipment/refrigeration; gated moisture latent gain | Source-state steam/process sensible effects and validated equipment library |
| Partitions/adjacent conditions | Implemented behind review gate | Fixed boundaries, ground contact and two-room dynamic coupling | Advanced adjacent profiles and benchmark reconciliation |
| HVAC system type/mapping | Partial | Explicit reviewed single-zone CV and VAV cooling systems | Additional types, heating systems and approved system constraints |
| AHU outside air/heat recovery/preconditioning | Implemented behind review gate for supported cooling systems | Explicit air paths, mixed-air state, fixed recovery and preconditioning | Heating path, additional methods and benchmark reconciliation |
| Coils/psychrometrics/fans/ducts | Implemented behind review gate for supported cooling systems | Cooling coil states, sensible/latent/total duty, fan/duct effects | Heating coils, broader coil methods and benchmark reconciliation |
| Chiller/boiler/circuits/plant | Partial | Cooling chillers and chilled-water circuits with reviewed coincident aggregation | Boiler/heating and refrigerant circuits, package units and validated auxiliaries |
| Validation/evidence/reporting | Partial | Evidence artifacts, staleness checks, synthetic tests and disabled final-parity gate | Real authorised benchmarks, tolerances and HVAC engineer review |

The bridge is complete for model entry: source evidence can be rebuilt, reviewed,
saved, previewed and applied additively into the hourly topology, schedule and
inactive envelope artifacts. Calculation methods remain gated per method and
project. Cooling AHU/plant and annual modules exist for bounded scopes, but are
not an approved or benchmark-validated engineering release. Heating AHU/boiler,
moisture diffusion through the fabric and remaining room air-path effects are
still excluded.

“Partial” means only a bounded calculation or data contract exists. A gate, source field or passing synthetic test does **not** mean the method is engineer-approved, benchmark-validated, equivalent to CAMEL+, or ready for equipment selection.

## Current implementation inventory

### Engineer-owned calculation artifacts

Each project review folder can hold these separate files:

- `site_design_conditions.json` — cited site identity and summer/winter design basis.
- `schedule_library.json` — cited reusable hourly schedule profiles.
- `design_day_scenarios.json` — cited hourly cooling or future-heating weather scenarios.
- `hourly_load_model.json` — reviewed room overlay and schedule assignments.
- `hourly_load_report.json` — current or stale authoritative hourly cooling results, with `blocked`, `draft`, or `review_ready` status.

The legacy `heat_load_report.json` remains readable only. New calculations are written exclusively to `hourly_load_report.json`.

They intentionally do not overwrite `design_requirements.json`. Saving a site condition, schedule or hourly report does not silently change the legacy cooling report or the separate ventilation report.

### Backend/API handoff

- Site design conditions: `GET`/`POST /api/site-design-conditions`
- Schedules: `GET`/`POST /api/schedules`
- Design-day scenarios: `GET`/`POST /api/design-day-scenarios`
- Hourly room model: `GET`/`POST /api/hourly-load-model`
- Immutable cooling-input assembly: `GET`/`POST /api/calculator-inputs`
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
- [ ] Build a permissioned evaluation set spanning multiple projects, uses and drawing conventions, with cited schedules, design-day scenarios and room overlays; distinguish ordinary project regression cases from independent analytical or authorised comparison benchmarks.
- Add API-level validation/error examples for every blocked state so the future frontend can render actionable remediation.
- Add regression coverage for artifact migration/versioning, stale-report causes and component reconciliation at room/zone/project level.
- [x] Produce a concise backend runbook covering artifact lifecycle: build model → review/save → calculate draft → complete scope → review-ready report. See [Cooling workflow runbook](cooling_workflow_runbook.md).

**Exit criteria:** repeatable project cases can generate current draft or review-ready cooling reports, explain every result line and become stale predictably when dependencies change; the evaluation record states which project types and capabilities remain untested.

### Milestone 1 — Complete reviewed room cooling inputs

**Goal:** Close the highest-value room-data gaps without adding unapproved automatic assumptions.

**Work:**

- [x] Expand the room overlay to carry separately cited infiltration, vapour gain, minimum supply air, extract/spill/transfer/make-up air, source-room mapping and airflow constraints with explicit calculation status.
- [x] Add separately cited moisture/process component records and schedule mappings; direct-to-room latent calculation is implemented, while source-state steam sensible effects remain excluded.
- Add reviewed construction and opening references rather than free-text-only envelope fields. Preserve source/version and allow engineer overrides.
- [x] Define explicit readiness rules for calculated room moisture and transfer-air inputs, requiring their schedules and keeping unapproved methods draft-only.

The current schema stores airflow and moisture/process components with room
ownership, units, source, citations and explicit calculation status. Direct
moisture latent and transfer-air exchange have draft calculation paths; other
room air paths remain excluded until their boundaries can be represented
without overlap. Construction/opening references still need stronger
component-level provenance.

**Decisions required before design use:** project-specific engineer approval of the implemented infiltration, direct moisture and transfer-air methods, plus defined methods for the remaining components. No code rates or CAMEL defaults are embedded without a separately approved source/basis.

The infiltration decision record is documented in
[`infiltration_method_gate.md`](infiltration_method_gate.md). The fixed V1
engine is implemented, but each project remains disabled until its named
HVAC-engineer gate is approved.

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

**Dependencies:** reviewed envelope/adjacent-condition scope and approved heating method. The separate heating runner now supports draft and review-ready room/zone/floor/project results when its cited inputs and gate are complete.

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

The private drawing-case preparation tool and runbook are available. The current
Butcher Buffet Melrose Park / Drawing 6 candidate is one review-required project
case: its evidence packet has no resolved floor/room topology or complete
supported internal-gain inputs. It must not be promoted to a review-ready result
until a named reviewer resolves those gaps. This status does not define the
readiness of other project cases or block independent capability work.

## Choosing the next work

Choose the next task from the largest consequential gap in the capability, based on current code, evidence, tests and user friction. Prefer work that improves correctness, traceability, completeness or usability across more than one project. When a project-specific task is the best way to address that gap, use the project as a test case and preserve unresolved evidence rather than assuming values.

Drawing 6 may be used to exercise page grouping, floor/room evidence, reviewed topology and a traceable cooling workflow. Its remaining work belongs in the broader evidence-to-calculation backlog; finishing it does not block unrelated validated capabilities and does not prove that those capabilities generalize. Keep the internal calculation sanity harness as development support, separate from contractor workflow and from engineering validation.

Do not defer a method solely because Drawing 6 is incomplete. Start or extend a method when its scope, source evidence, independent checks, gates and user value are sufficiently clear; keep it blocked or draft where required evidence or review is absent. Conversely, do not release a method merely because it works on Drawing 6 or passes synthetic tests.

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
