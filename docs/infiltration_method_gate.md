# Infiltration Sensible/Latent Cooling Method Gate — V1

Status: **placeholder by default — disabled until a named HVAC engineer approves the project-local gate**.

The authoritative project record is `infiltration_method_gate.json`. This document fixes the V1 policy; a project gate records the engineer, credential, approval date, citation, scope, and approval status. A placeholder may be saved and tested but cannot add infiltration to cooling totals.

## Fixed V1 method

- Accepted inputs are positive `ACH`, `L/s`, `m3/s`, and `m3/h` values only.
- All resolved airflow is treated as outdoor-design-condition volume flow.
- Direct-flow conversions are `m3/s × 1000` and `m3/h ÷ 3.6` to produce L/s.
- ACH conversion is `ACH × room volume (m³) ÷ 3.6`. Room volume is reviewed room area times room ceiling height; a cited zone height is the only fallback. No project-wide height or ACH default is permitted.
- A non-zero calculated infiltration input requires its own complete 24-hour schedule for the selected scenario day type. It must not reuse occupancy or outside-air schedules implicitly.
- The existing room safety factor applies once after all hourly components, including infiltration. No separate infiltration allowance is applied.
- The input must declare `uncontrolled_infiltration`; it cannot duplicate outside-air ventilation, transfer air, extract, spill, make-up air, or a future AHU path.
- The engine uses the selected scenario outdoor DB/WB/pressure and room DB/WB in the existing moist-air enthalpy method. It records signed sensible and latent diagnostics, but only each positive cooling component contributes to the cooling duty. Negative air effects never reduce peak cooling duty.

## Eligibility

Each calculated room input requires a stable component ID, room owner, value, unit, source, citation, confirmation status, the fixed method ID `infiltration_psychrometric_v1`, air-path declaration, and flow reference.

- `not_present_confirmed` remains a cited confirmed absence and adds no load.
- `calculated` is allowed only for infiltration after the project gate is approved. Confirmed inputs can support `review_ready`; provisional ones are calculated only in `draft` scope.
- `stored_not_calculated` and `not_assessed` remain excluded. For infiltration they also exclude that room from complete cooling scope.

## Required approval record

An approved project gate needs: engineer name, credential, approval date, method citation, stated cooling-only scope, and at least one citation. Any policy change requires a new method/version and renewed engineering approval.

## Required verification cases

1. Confirmed not-present input changes no cooling result.
2. ACH and equivalent direct flow agree.
3. Changing reviewed volume changes ACH flow deterministically.
4. Schedule-off hours contribute zero.
5. Signed sensible/latent diagnostics are retained without reducing cooling peak duty.
6. Missing gate, source, citation, schedule, volume, invalid unit, and duplicate air-path declaration block the affected room.
7. Provisional input produces only `draft`; confirmed complete scope may be `review_ready`.

This is cooling infiltration only. It does not approve heating, AHU, transfer air, extract/make-up air, glazing, shading, annual analysis, or benchmark validation.

## Isolated preparation review (14 September 2026)

The [isolated method package](infiltration_method_preparation.md) characterizes
this existing policy and its validation gaps. It does not approve the method,
change runtime physics, or release Drawing 6 inputs. Independent arithmetic
vectors and dedicated tests are development evidence only. No engineer approval
record is created by that package.
