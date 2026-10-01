# Airflow volumetric reference basis

Volumetric airflow is not a mass-flow rate. Converting it to dry-air mass flow
requires the specific volume at the state where the volume was measured, or a
stated standard-air density. Archie uses `m_dot_da = V_dot / v` for its room
outside-air and infiltration psychrometric loads; those paths currently
interpret L/s at the outdoor design state. The output now records that choice
as `flow_reference_basis: outdoor_design_condition` and marks it
`calculation_assumption_unverified`. Heating outside-air and infiltration
reports carry the same provenance. This is separate from whether the airflow
value itself was confirmed by its source.

The result UI phrases this for users as “outdoor design-air state (assumption;
source airflow basis not verified)” rather than presenting the internal status
code.

ASHRAE Handbook—Fundamentals (2025), Chapter 1 defines moist-air specific
volume per unit dry-air mass. ASHRAE Handbook—Fundamentals (2025), Chapter 18,
Section 3.1, “Standard Air Volumes,” says calculations are more accurate on a
mass basis and describes both standard-air volume (including 1.2 kg dry
air/m³) and volume measured at a particular point such as a coil inlet or
outlet. Its infiltration section likewise relates volumetric rate to a known
dry-/wet-bulb condition. These are psychrometric calculation references, not
Australian ventilation rules or project-specific design instructions.

For room outside-air rates, the editor now lets the user declare either the
outdoor design-air state or ASHRAE's 1.2 kg dry-air/m³ standard-air basis. The
selected basis is used for both cooling and heating mass-flow conversion and is
reported as declared but not independently verified. “Legacy / not stated” is
still the default for existing projects and preserves the previous outdoor-
state calculation, marked as an unverified assumption. These selections do not
establish that the source rate was actually specified on that basis.

Cooling and heating infiltration remain on the outdoor-state basis because the
approved V1 infiltration-method policy fixes that reference. This work does
not widen the approved method gate. An arbitrary measurement point also
remains unsupported. AHU supply airflow has a separate explicit mixed-air
coil-inlet assumption and remains draft/review gated.

Before an airflow result is treated as design-validated, the input workflow
must capture and retain evidence of the volume reference basis from its source.
The room outside-air editor now captures a basis declaration, but does not
require a citation or verify that the declaration matches the source. Infiltration
basis changes require a separately versioned and approved method; the V1 gate
fixes it to outdoor design conditions. AHU path-state selection requires project
source data. Do not reinterpret existing project rates until their source basis
is known.

The draft airflow resolver treats repeated records with the same room, path
type, and physical-path key as one physical path. If those records disagree on
flow value, unit, direction, or ownership, each record is blocked and retained
with conflict IDs and a reason. The AI preliminary model does not select one of
those conflicting candidates; it may still use its separately labeled
controlled fallback, which remains provisional. This conflict guard does not
resolve unkeyed multiple paths, determine whether separate paths should be
summed, or validate the fallback rate.

References:

- [ASHRAE Handbook—Fundamentals (2025), Chapter 1: Psychrometrics](https://handbook.ashrae.org/Handbooks/F25/SI/F25_Ch01/F25_Ch01_si.aspx)
- [ASHRAE Handbook—Fundamentals (2025), Chapter 18: Nonresidential Cooling and Heating Load Calculations](https://handbook.ashrae.org/Handbooks/F25/SI/F25_Ch18/f25_ch18_si.aspx)
