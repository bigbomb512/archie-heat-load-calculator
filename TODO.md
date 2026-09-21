# Archie Heat-Load Calculator TODO

This file tracks the practical next actions for the heat-load calculator. The
full milestone roadmap is in [`docs/cool_heat_load_roadmap.md`](docs/cool_heat_load_roadmap.md).

## Current priority — Drawing 6 geometry review workspace

- [x] Extract cited calculation inputs from architect PDF plans, service/RCP
  pages, openings, equipment schedules, notes and sections into a normalized
  evidence register before adding another calculation method.
- [x] Bind image, OCR, table, vector and manual-vision observations to stable
  room/opening identities; keep ambiguous matches as conflicts and 3D as
  cross-check-only evidence.
- [x] Use the existing evidence-fusion output in the frontend to review page
  groups, room witnesses, floor identity, geometry status and area evidence.
- [ ] Apply only reviewed floor → zone → room topology without overwriting
  authored records, then complete the supported room cooling inputs.
- [ ] Reach one traceable `review_ready` Drawing 6 cooling case before adding
  another calculation method.

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
- [ ] Confirm safety-factor placement and ensure it is applied exactly once.
- [ ] Confirm that outside air and infiltration cannot represent the same air
  path twice.
- [ ] Run the validated equations through the private Drawing 6 workflow.
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

## Roadmap after current validation

1. Complete the supported Drawing 6 cooling case.
2. Finish the approved infiltration slice.
3. Implement detailed glazing and controlled solar/shading.
4. Expand partitions and adjacent-boundary methods.
5. Run the authorised cooling benchmark gate.
6. Build heating separately.
7. Build AHU and air-side calculations.
8. Build plant and circuit aggregation.
9. Implement annual/monthly analysis last.
