# Archie Heat-Load Calculator TODO

This file tracks actionable gaps for the heat-load capability across Archie.
The product end goal is documented in [`docs/PLAN.md`](docs/PLAN.md), and the
full calculation roadmap is in [`docs/cool_heat_load_roadmap.md`](docs/cool_heat_load_roadmap.md).

## How to choose and track work

Prioritize the most consequential supported gap in calculation correctness,
evidence traceability, scope completeness or user workflow. Prefer improvements
that apply across projects. A task on one project or drawing set is a means to
exercise a capability, not the product's completion criterion. Preserve
project-specific evidence and mark gaps unresolved where the documents do not
support a conclusion.

Maintain a varied project evaluation set, including different building uses,
drawing conventions, scales, layouts, document quality and HVAC systems. Record
which capabilities each case exercises, what evidence is permitted, and what
remains unsupported. Drawing 6 is one such case; its results must not be
generalized to untested projects.

Use the read-only portfolio scorecard in evaluations/README.md and
tools/evaluate_portfolio.py to track stage coverage, blockers and change across
permitted project cases. Coverage is not an accuracy or engineering-validation
claim.

## Open implementation and validation work

The existing Drawing 6 evidence-fusion and review work is a project-specific
test of the reusable evidence-to-model workflow:

- [x] Align room-use, ceiling-volume, internal-gains and airflow editors with
  the preliminary pipeline's shared room proposal and resolver-specific source
  fingerprints. Old artifacts may show stale once; users refresh them through
  the corresponding editor. Overrides on disappeared ceiling, internal-gains,
  and airflow room/paths remain explicitly stale for review; airflow overrides
  are per path, queued source research survives a re-resolve, and stale source
  inputs block airflow edits. Individual stale airflow rows cannot be edited
  even when artifact fingerprints are current; clearing one removes the value
  without clearing stale status. Stale retained values stay out of calculation
  mappings.
- [ ] Audit truthful staleness checks for safety-factor, site-location and
  site-design-weather editors; those services have separate inputs and remain
  outside the room-input resolver change.

- [x] Extract cited calculation inputs from architect PDF plans, service/RCP
  pages, openings, equipment schedules, notes and sections into a normalized
  evidence register before adding another calculation method.
- [x] Bind image, OCR, table, vector and manual-vision observations to stable
  room/opening identities; keep ambiguous matches as conflicts and 3D as
  cross-check-only evidence.
- [x] Classify building-level, finish/tag, detail-label and body-text level
  evidence separately; only a reviewed page-triage label or agreeing
  title-block/address/page-title building-level evidence may select a floor.
  Version the classifier and mark dependent artifacts stale after the upgrade.
- [x] Use the existing evidence-fusion output in the frontend to review page
  groups, room witnesses, floor identity, geometry status and area evidence.
- [x] Apply reviewed floor → zone → room topology and current calibrated traced
  areas through the calculator draft bridge without overwriting authored
  records. The room registry supplies otherwise missing room candidates;
  engineer acceptance records the trace proof and calibration in provenance.
  Remaining room cooling inputs still require their own evidence and review.
- [ ] Resolve floor identity for single-storey tenancies when drawings provide
  no explicit title-block/page-title building level; do not infer a floor from
  finish codes or create a floor until the contractor/reviewer maps it.
- [ ] Record the case's traceable result and unresolved evidence when available;
  treat it as one project test, not a prerequisite for all other product work.

The temporary calculation harness below is internal development support. It is
not a contractor workflow and does not block the geometry-review product work.

- [x] Add temporary internal calculation sanity tests; do not expose equations
  or QA workflow to contractors. Useful checks may later be retained as
  ordinary regression tests, but this temporary harness is not a product
  feature.
- [x] Add independent numeric regression cases for psychrometrics, moist-air
  enthalpy, specific volume, outside-air load, infiltration, conduction, solar,
  safety factors, and coincident room/zone/floor peaks.
- [ ] Resolve and document the sign policy for negative conduction and negative
  sensible/latent air loads.
- [ ] Confirm safety-factor placement and ensure it is applied exactly once. Legacy and approved project-policy invariants now have calculation-path checks; strict behavior after a preliminary fallback artifact remains unresolved, and annual/project-policy scope still needs owner review.
- [ ] Confirm that outside air and infiltration cannot represent the same air
  path twice. Cooling, hourly heating, and annual room calculations now share
  the room-level checks for duplicate infiltration paths, assessment state,
  dedicated schedules, and ACH volume inputs; room-versus-central-AHU outside-
  air ownership remains open and has not been settled by these checks.
- [ ] Decide whether heating infiltration needs an approval scope separate from
  the cooling-scoped `infiltration_method_gate.json`; current heating behavior
  checks that gate but does not establish that its approval covers heating.
- [ ] Run the supported equations through the private Drawing 6 workflow when
  its inputs are ready, and retain it as a regression case alongside other
  permitted projects.
- [ ] Record the remaining limitations; do not claim CAMEL+ validation.

## Deferred — annual and monthly energy analysis

Annual analysis is intentionally deferred until the prerequisite peak-load and
building/system methods are validated. It must not be presented as complete
merely because an annual sum can be calculated.

- [ ] Add a versioned weather/calendar input set covering all 8,760 hours.
- [ ] Validate hourly dry-bulb, humidity, pressure, solar radiation, and
  daylight/solar-position data for the project location.
- [ ] Complete detailed glazing, solar transmission, orientation, and shading
  calculations.
- [ ] Approve and implement dynamic envelope thermal mass/conduction rather
  than relying only on steady-state `U × A × ΔT`.
- [ ] Complete occupancy, lighting, equipment, ventilation, infiltration, and
  holiday/weekend schedules for the full year.
- [ ] Implement heating, AHU, coil, fan, heat-recovery, plant, and control
  behaviour required for annual energy modelling.
- [ ] Define the hourly energy equations and whether outputs represent room
  load, coil load, plant load, or electrical energy.
- [ ] Add annual integration with explicit time-step and unit handling:
  `E = Σ(Q_h × Δt)`.
- [ ] Add annual regression cases for leap years, missing weather, schedule
  gaps, daylight-saving/calendar handling, and equipment shutdown periods.
- [ ] Compare annual/monthly outputs against an authorised reference case
  before exposing annual results as validated.

## Capability areas to advance

- [ ] Complete and validate automatic room-boundary detection and vision-based
  room proposals. Engineer acceptance of a current traced area is a workflow
  control, not independent evidence of area accuracy; compare real areas against
  a separately recorded source before making accuracy claims.

Select order from current impact, evidence availability, dependencies and
verification readiness; this is a capability map, not a sequence gated on one
project reaching completion.

- Close method-specific validation gaps for supported cooling, heating,
  infiltration, glazing/solar, psychrometrics and safety-factor behavior.
- Complete evidence-backed room and air-path modeling while keeping comfort
  ventilation, process exhaust, make-up air and infiltration distinct.
- Expand validated heating, AHU/air-side and plant/circuit scopes when their
  methods, sources and reference cases are ready.
- Improve the review and reporting workflow so users can resolve missing or
  conflicting evidence without silently accepting assumptions.
- Build a permissioned and diverse project evaluation set; report coverage,
  component deviations and unsupported cases rather than a single aggregate
  accuracy claim.
- Defer annual/monthly analysis until its weather, calendar, building/system
  methods and reference validation are adequate for the intended output.
