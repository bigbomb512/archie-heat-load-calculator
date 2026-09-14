# Infiltration cooling: isolated engineering-method preparation

Prepared 14 September 2026 against `Max` commit `d045420` (physics unchanged from `7c8a212`).
Status: **unapproved proposal and characterization of existing code**.

This package contains documentation, an independent arithmetic worksheet and
regression tests. It adds no production method or input integration. The existing
identifier `infiltration_psychrometric_v1` is quoted from `ai/infiltration_gate.py`;
it is not a newly assigned or approved method ID. Engineer name, credential,
approval date and approval citation remain absent. Passing tests is not approval.

## 1. Drawing 6 evidence and limits

Folder `6` contains two PDFs, rather than a single sixth file. Pending confirmation,
the architectural construction set is the primary reference and the mechanical
as-built is supporting context. PDFs remain outside the repository.

| Source | Inspected evidence | Consequence for the proposed contract |
| --- | --- | --- |
| `20260226 Butcher Buffet @ Melrose Park CONSTRUCTION REV B(2).pdf`, PDF page 19, sheet 201, proposed floor layout | Dining/buffet, kitchen, coolroom and freezer are shown | Establish reviewed room ownership and conditioned scope before allocating leakage; do not treat refrigeration rooms as comfort-HVAC infiltration by default |
| Same PDF, page 22, sheet 204, reflective ceiling plan, revision B | Visible CH labels 2600, 2900 and 3150 mm; underside of slab 3750 mm; service zone 2950–3150 mm; underside of beam 2700 mm | CH, slab, beam and service dimensions have different meanings. A reviewer must bind the appropriate volume boundary to each room; no universal 3000 mm default |
| `Butcher Buffet (BB) - Air Conditioning Design Drawings Full Set_As-Built.pdf`, PDF page 5, sheet BB-M120 | Duct/grille layout, O/A annotations and a base-building outside-air recommissioning note | Mechanical outdoor-air and supply flows are not evidence of uncontrolled infiltration |

Both PDFs were text-inspected; the three pages above were rendered and visually
reviewed. No leakage rate, infiltration operating profile or approval was
established from those reviewed pages. This is not a claim that every page has
been exhaustively checked for those facts. Drawing notes are source evidence,
not instructions authorizing code changes or engineering approval.

SHA-256 source fingerprints:

- Architectural: `62be9de1a34b6522173031500102c6499c9a7818199818030b059a10008f338b`
- Mechanical: `65a4b8cd3a72b85c621c71b874d275035e330391b80dcde46cadfaff17e4db72`

The supplied project baseline is a fresh immutable input snapshot with 22 blocked
records, no complete design-day scenario/schedules and no active envelope model.
This package neither regenerates nor independently certifies that snapshot.
Synthetic test areas, airflows and weather are not Drawing 6 inputs. The height
sensitivity test uses the observed CH numbers only as arithmetic examples, with
a synthetic 20 m² area and 0.36 ACH; it does not calculate a real room volume/load.

## 2. Proposed interface and accepted units

Preserve the current pure calculation signature for characterization:

```python
infiltration_load(value, unit, indoor_db_c, indoor_wb_c,
                  outdoor_db_c, outdoor_wb_c, pressure_kpa,
                  *, room_volume_m3=None, schedule_factor=1.0,
                  method_id="", gate_version="")
```

This function is an arithmetic primitive, not an approval boundary. It can be
called without a gate and must not be exposed as a standalone project calculator.
Production entry points must validate the complete contract before calling it.
The default schedule factor of 1 is a primitive default, not permission to omit
an hourly schedule. Blank method/version arguments are not approval.

| Quantity | Exact existing representation | Proposed eligibility |
| --- | --- | --- |
| Infiltration value | Positive `ACH`, `L/s`, `m3/s`, `m3/h` (case-sensitive) | Finite positive number, declared uncontrolled air path, cited source and review status |
| Resolved airflow | L/s at outdoor design conditions | Never silently interpret standard/indoor volume flow as outdoor volume flow |
| Room area / height | m² / `ceiling_height_mm` | Reviewed area and room height, or explicit reviewed zone-height fallback |
| Room volume | m³ | Required only for ACH; user confirmed this contract choice on 14 September 2026 |
| DB / WB | °C | Indoor room conditions and outdoor scenario-hour conditions; WB ≤ DB |
| Pressure | kPa absolute | Positive, physically compatible with vapour pressure; no atmospheric default |
| Schedule | 24 factors per selected day type | Each 0–1, explicit infiltration assignment, source and review status |
| Safety | Dimensionless room multiplier | Applied once to the room subtotal, not to the primitive |

Conversions:

```text
V_room = area_m2 × ceiling_height_mm / 1000
L/s = ACH × V_room / 3.6
L/s = (m3/s) × 1000
L/s = (m3/h) / 3.6
applied_L/s(hour) = resolved_L/s × infiltration_schedule(hour)
```

The factor 3.6 is 3600 seconds/hour divided by 1000 litres/m³. Thus synthetic
0.36 ACH × 60 m³ = 21.6 m³/h = 0.006 m³/s = 6 L/s.
At fixed area, ACH and conditions, doubling height doubles airflow and unrounded
load. At fixed direct airflow, changing height does not change load.

A confirmed absence uses `not_present_confirmed`, with no value/unit; zero ACH
is not a substitute for an absence review. Zero schedule is allowed for an
otherwise valid positive input. An off hour still validates psychrometric inputs.

## 3. Exact existing psychrometric arithmetic

The following equations describe `ai/heat_loads.py`; they are not asserted to be
an independently approved engineering standard. For each indoor/outdoor state,
T is DB, Tw is WB, P is absolute pressure (°C and kPa):

```text
p_ws(Tw) = 0.61094 × exp(17.625 × Tw / (Tw + 243.04))       [kPa]
a(Tw) = 0.00066 × (1 + 0.00115 × Tw)
p_v = p_ws(Tw) − a(Tw) × P × (T − Tw)                     [kPa]
w = 0.621945 × p_v / (P − p_v)                           [kg water/kg dry air]
h = 1.006 × T + w × (2501 + 1.86 × T)                    [kJ/kg dry air]
v = 0.287055 × (T + 273.15) × (1 + 1.607 × w) / P        [m³/kg dry air]
m_da = (applied_L/s / 1000) / v_outdoor                   [kg dry air/s]
Qs_signed = m_da × 1.006 × (T_outdoor − T_indoor)          [kW]
Qt_signed = m_da × (h_outdoor − h_indoor)                 [kW]
Ql_signed = Qt_signed − Qs_signed                        [kW]
```

The mass-flow field is named `mass_flow_kg_s`; its dry-air basis follows from
specific volume. Existing sensible arithmetic uses 1.006 only, while the residual
latent term includes the moist-air sensible contribution implicit in enthalpy.
An engineer must decide whether this sensible/latent split is the intended method.

The code rejects WB above DB, nonpositive pressure and vapour pressure outside
(0, P). These checks alone do not establish an approved temperature/pressure
range or cover every low-level non-finite input.

For external comparison, [PsychroLib's published implementation](https://psychrometrics.github.io/psychrolib/_modules/psychrolib.html#GetHumRatioFromTWetBulb)
uses a different wet-bulb humidity-ratio relation, with separate above/below-freezing
branches. Its SI pressure is Pa and enthalpy is J/kg. It is a useful candidate
independent method comparison, not the numerical oracle for characterizing
Archie's current equations. No claim of ASHRAE, DA09 or CAMEL+ parity follows.

## 4. Signs, schedules, report fields and safety

Existing V1 applies `max(Qs_signed, 0)` and `max(Ql_signed, 0)` **separately**.
It does not clamp net enthalpy load as a single number. Consequently hot/dry or
cool/humid air can have an applied total different from the signed enthalpy total.
Both signed components remain visible as diagnostics. This is an unapproved
policy under review, not a newly chosen convention.

Primitive output contains `name="infiltration"`, `sensible_kw`, `latent_kw`,
`total_kw`, `formula` and `inputs`. Inputs retain original value/unit, base/applied
flow, room volume, schedule factor, DB/WB/pressure, mass flow, method ID,
gate version, flow reference and `raw_signed_sensible_kw`/`raw_signed_latent_kw`.
There is no separate signed-total field; sum signed diagnostics for display only.

Existing component sensible/latent numbers are rounded to 4 decimal kW places
before total summation; flow and mass diagnostics use 6 decimal places. Hourly
`combine_components` adds `input_rows` and schedule metadata. Safety is added by
`hour_total` after combining all room components. Aggregation sums already-applied
room allowances; it must not apply the room factor again at zone/floor/project.
Outside-air ventilation currently preserves signed terms without infiltration's
clamp. This asymmetry requires engineering review, not an incidental refactor.

## 5. Real gate and incomplete inputs

The project-local `infiltration_method_gate.json` remains authoritative. The
validator requires approval status, existing method ID, fixed policy, named
engineer, credential, approval date, method citation, scope and citation records
for an approved record. This preparation writes no such record and invokes no
approval API. The new tests do not invent an engineer or reuse the existing
`approved_infiltration_gate()` synthetic helper in `tests/test_hourly_loads.py`.

A calculated component must pass `validate_room_component`: stable ID, owner
through its room, positive accepted value/unit, source, citation, confirmed or
provisional status, existing method ID, `uncontrolled_infiltration` air path and
`outdoor_design_condition` flow reference. Report-level tests use an unapproved
gate and assert blocking, never simulate engineering release.

`room_static_missing` rejects unassessed/stored infiltration, a missing approved
gate and absent ACH height. `resolved_profiles` rejects absent assignments,
unknown schedules, unusable day types and incomplete 24-hour profiles. Provisional
components/schedules cannot establish review readiness. Incomplete project scope
must not publish a complete `project_peak`.

## 6. Unresolved decisions and observed enforcement gaps

1. Confirm outdoor-condition volume basis for ACH and measured direct flow. Room-
   condition or standard-volume measurements need an explicitly approved conversion.
2. **Resolved contract choice (user, 14 September 2026):** reviewed room volume
   is required only for ACH conversion, not for direct airflow. This matches the
   existing implementation; it is not a method approval or a release of any room.
   Geometry still needs review wherever used elsewhere in the cooling model.
3. Confirm component-wise positive-only cooling versus signed-net cooling, and the
   residual latent definition. Decide how this relates to outside-air sign policy.
4. Establish the valid psychrometric domain and acceptable reference discrepancy.
   Decimal worksheet agreement proves arithmetic, not suitability of the equations.
5. **Left unresolved for engineer review by the user:** decide whether “dedicated
   schedule” requires a unique schedule ID or only an
   explicit assignment with independent evidence. Current validation permits a
   shared ID; it does not implicitly fall back to ventilation/occupancy schedules.
6. Approve evidence of distinct physical air paths. The current string declaration
   rejects `outside_air` as infiltration, but cannot detect copied measurements
   relabelled as uncontrolled infiltration. Equal flow values alone are not proof
   of duplication. A future reviewed air-path register would need distinct physical
   ownership and treatment of coupled exhaust/make-up paths; none is added here.
7. Validate positive reviewed geometry before calculation. Area zero is accepted
   by the room schema and produces zero volume; the arithmetic primitive rejects
   it later. Height citation ownership is not independently enforced by
   `room_volume_m3`; it simply uses room height, then zone height.
8. The gate validator checks field presence/policy, not credential authenticity or
   date authenticity. `gate_is_approved` alone checks only status and method ID.
   Use the full validator and a real human approval process; do not treat that
   predicate or passing metadata checks as evidence of authorization.
9. Multiple infiltration entries with different component IDs currently pass the
   component-list validator, while the lookup selects the first entry. Decide
   whether to reject duplicate types or explicitly model multiple distinct paths.
   The package records this behavior without correcting it in production.
10. Confirm safety-factor scope and treatment of uncertainty. The code has one room
   multiplier; this package does not approve its magnitude for Drawing 6.

These are future release requirements. Characterization tests may explicitly
record a current gap; a passing gap test is not evidence that the gap is solved.

## 7. Expected-direction and independent numeric verification

Before coding tests, the intended cases are: equivalent units agree; higher ACH,
flow or height increases hot/humid load; direct-flow load is independent of height;
schedule 0 produces zero and 0.5 halves unrounded load; identical states yield
zero; hot/humid gives positive sensible/latent; hot/dry retains negative latent
but applies zero latent; cool/humid retains negative sensible but applies zero
sensible; cool/dry retains both negatives and applies zero total. Ventilation and
infiltration occupy separate component keys and each is summed once. Wrong path,
missing source/citation, missing gate, incomplete schedule and missing volume
must reject or remain blocked/draft. No genuine approved-project success case is
claimed without an actual approval.

The independent worksheet uses 50-digit Decimal arithmetic and no Archie imports.
It evaluates the documented equations, outputs intermediate properties and freezes
numeric expectations in JSON. Tests read those constants rather than generating
expected answers by calling the implementation under test. This is independent
arithmetic of the same proposed equations, not independent engineering validation.

Representative frozen results at synthetic 6 L/s, indoor 24/18 °C DB/WB and
101.325 kPa (kW; 4-decimal reporting precision):

| Outdoor DB/WB °C / factor | Signed sensible | Signed latent | Applied infiltration total |
| --- | ---: | ---: | ---: |
| 35/24 / 1 | 0.0744 | 0.0644 | 0.1388 |
| 35/18 / 1 | 0.0754 | -0.0812 | 0.0754 |
| 20/19 / 1 | -0.0285 | 0.0537 | 0.0537 |
| 20/15 / 1 | -0.0287 | -0.0335 | 0.0000 |
| 24/18 / 1 | 0.0000 | 0.0000 | 0.0000 |
| 35/24 / 0 | 0.0000 | 0.0000 | 0.0000 |
| 35/24 / 0.5 | 0.0372 | 0.0322 | 0.0694 |

The 100 L/s outside-air plus 6 L/s infiltration fixture has a 2.4513 kW room
subtotal and 2.6964 kW total with a synthetic 1.1 room multiplier. This verifies
separate, once-only arithmetic; the fixture establishes no actual site airflow.

## 8. Performance and test evidence

The focused suite covers 32 test methods, including 13 frozen numeric vectors
and parameterized invalid-input cases. Three tests deliberately characterize
known gaps (shared schedule IDs, zero area and duplicate component types).
Direct-flow volume handling is tested as the user-confirmed contract choice. No approved gate is fabricated, mocked or persisted. Direct
primitive/assembly tests exercise arithmetic below the eligibility boundary;
they do not represent authorized project calculations.

Verification completed in the isolated worktree: all 32 focused tests passed
(0.004 seconds reported by unittest), all 15 existing heat-load script checks
passed, and the 13 frozen vectors reproduced byte-for-byte. The final change-set
audit permits exactly the five files listed below; whitespace validation passed.

Run from the repository root:

```bash
python3 tests/test_infiltration_method_preparation.py
python3 tests/test_heat_loads.py
git diff --check
```

The worksheet may be regenerated manually to a temporary file and compared with
`tests/fixtures/infiltration_method_vectors.json`; it is never called by the test
suite to obtain expected values. It requires only Python's standard library.
The tests likewise add no dependency, network request, application server,
private-PDF read, project-cache mutation or report-file write.

Infiltration arithmetic is constant work per room/hour; the existing selected-day
workflow scales approximately with rooms × scenarios × 24 hours. Detailed retained
component rows consume storage at the same order. The Decimal worksheet is an
offline test tool, not a runtime optimization. No production performance changed,
and no building-scale performance benchmark was performed. Any later caching
would need to include weather, indoor conditions, pressure, flow, schedule and
method version to avoid stale results.

## 9. Changed files and integration boundary

Prepared in a temporary detached worktree based on `Max`, for delivery on `Max`
as requested. No additional named branch is required. The temporary worktree is
removed after delivery. `main` remains unchanged; putting documentation and tests
on `Max` does not connect new physics to production calculations.

| File | Purpose |
| --- | --- |
| `docs/infiltration_method_gate.md` | Link existing policy to this unapproved preparation record |
| `docs/infiltration_method_preparation.md` | Input/equation contract, Drawing 6 source observations, engineering decisions and handoff report |
| `tests/reference/infiltration_decimal.py` | Standalone high-precision arithmetic worksheet with explicitly synthetic inputs |
| `tests/fixtures/infiltration_method_vectors.json` | Frozen independent expectations and intermediate properties |
| `tests/test_infiltration_method_preparation.py` | Focused arithmetic, eligibility and known-gap characterization tests |

No changes are made to `ai/heat_loads.py`, `ai/hourly_loads.py`,
`ai/infiltration_gate.py` or any other production Python module. These are inspected
and imported by tests only. In particular, do not yet connect the worksheet or
fixtures to:

- `ai/calculator_inputs.py` or `ai/research_cache.py`;
- `backend/web_app.py` or `frontend/js/app.js`;
- `config/approved_research_source_packs.json`,
  `config/research_source_pack_releases.json` or `config/au_cooling_default_pack.json`;
- `tools/seed_au_default_pack.py`;
- any production report, snapshot, project cache, private PDF or Drawing 6 file
  under `output/`.

Next review is the documented contract and unresolved decisions, followed by a
real project-local engineering approval if appropriate. Any implementation of
those decisions is a separate future task. Input-resolution work and the 22
blocked Drawing 6 records remain outside this package's scope.
