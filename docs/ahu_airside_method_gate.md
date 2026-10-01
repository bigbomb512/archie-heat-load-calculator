# AHU and air-side cooling method gate

Stage 9 adds a separate, explicit air-side calculation path. It consumes a
current room-level hourly cooling report and never changes the room formulas or
historical room reports.

The first release supports single-zone constant-volume and multi-zone VAV
systems. AHU topology, zone ownership, number-off, air paths, airflow,
return/outside-air states, fan heat, duct effects, leakage, heat recovery,
preconditioning, and coil leaving states must be reviewed and cited. Airflow
is not derived from room load, screenshots, room names, or system type. The
current schema does not record the reference condition for L/s airflow, so coil
mass flow still uses an explicitly documented mixed-air-state assumption and
is not fully validated.

Outside-air conditioning is centrally owned by the AHU adapter for AHU-served
zones. The adapter removes the room report's outside-air component only from
its AHU reconciliation view; the historical room report is not rewritten.
Infiltration remains room-level and separate.

Positive make-up, transfer, leakage, exhaust, or relief airflow currently
blocks the AHU thermal calculation. V1 includes these flows in the volume
balance but does not fully resolve their psychrometric state or whether and
where they pass through the modeled coil. In particular, leakage direction and
source/destination states are not applied; treating every leakage path as
outdoor air would be incorrect for other path directions. Exhaust and relief
take-off points also affect which air reaches the coil, while V1 derives coil
mass flow from supply volume. Silently omitting or misclassifying these paths
would make the mixed-air state and coil flow inconsistent. Zero scheduled
flow has no hourly thermal contribution. Modeling active paths requires
reviewed routing, state, and coil-ownership inputs.

Fan heat is added to the system result after coil duty. Positive return- or
mixed-air fan heat currently blocks calculation because V1 does not apply it
to the coil-inlet state; supply-fan heat remains downstream of the coil in this
model.

The air-side method gate is a project-local approval record. A placeholder gate
allows development/draft results, while an approved gate is required for a
`review_ready` AHU report. Approval requires a named engineer, credential,
date, method citation, scope, and supporting citations. The gate is not a
credential verifier and passing synthetic tests is not engineering approval.

The hourly report retains room-load reconciliation, mixed-air and coil state
points, sensible/latent/total coil duty, fan and duct effects, flow balance,
blocked AHUs, source citations, and dependency fingerprints. Invalid states,
missing return-air conditions, missing airflow, path imbalance, and incomplete
reviewed inputs fail closed. Plant, hydraulic, annual, and benchmark-validating
calculations remain outside this method.

V1 consumes at most one coil, heat-recovery, and preconditioning record per
AHU, and one return-air psychrometric state. Duplicate records for these
single-state inputs are rejected during validation; the previous first-record
selection could otherwise make the reported result depend on record order.
The modeled outside-air, return, and supply paths must connect outside-air to
mixed-air, room-return to return-air, and mixed-air to supply-air respectively.
These are the streams V1 uses to construct the mixed-air state and infer coil
flow; a path tagged with one type but connected to different nodes is rejected
instead of being treated as the tagged stream.
Explicitly requested empty scenario/AHU selections, or a scenario absent from
the current room report, produce a blocked report. They are not expanded to
all scenarios or treated as a partial selected-scenario result.

Report-level `review_ready` status requires every selected scenario to be
usable and review-ready under the approved method gate. If a selected scenario
is blocked, any peak from the remaining usable scenarios is only an included-
scope subtotal: the overall report stays `draft`, and the project peak remains
suppressed.

## Psychrometric mixing and validation status

Adiabatic mixing converts each path's volumetric flow to dry-air mass flow at
that stream's own state and pressure using its specific volume. Mixed humidity
ratio and enthalpy are then weighted by dry-air mass flow. This follows the
mass and energy balances for adiabatic mixing described in ASHRAE
Fundamentals, Chapter 1, Example 4. A fixed, versioned regression case is in
`tests/fixtures/ahu_psychrometric_benchmarks_v1.json`. It checks the mass-flow
conversion, the conserved mixture properties, and the published chart's
approximately read mixed dry-bulb result. This is an analytical software check,
not Australian design approval or DA09/CAMEL parity.

For cooling with condensation, coil total duty now subtracts the liquid
condensate enthalpy carried out at the leaving-air temperature. Condensate
mass flow is the dry-air mass flow times the humidity-ratio reduction. Liquid
water enthalpy is linearly interpolated over 0–30°C from ASHRAE Fundamentals
F25 Table 3; condensing results outside that interval are blocked. The AHU
benchmark fixture also checks ASHRAE Chapter 1 Example 3, including total,
sensible, latent, and condensate rates. Sensible duty uses the project's
current convention `m_da × 1.006 × ΔT` (dry-air heat capacity only), with latent
duty reported as the remainder so the components conserve the corrected total.
The ASHRAE example supports the total energy balance and condensate term, but
does not publish this sensible/latent split. The result now identifies this
split convention and marks it unvalidated. Do not treat the split as an
ASHRAE- or DA09-validated coil sensible heat ratio until an authorized reference
case and engineer-approved definition are available.

The AHU model still accepts design airflow in L/s without recording the
reference condition at which each rate was measured. The coil duty currently
uses the mixed-air specific volume to infer dry-air mass flow from the supply
airflow. The result now records that exact assumed basis, the mixed-air state
used, and `verified: false`; the website displays it beside the AHU coil result
as an assumption that must be confirmed before design use. Whether
project-provided supply L/s should be interpreted at mixed-air, coil-leaving,
or a standard-air condition remains unresolved. The fixed coil
benchmark validates one idealized cooling/dehumidification case; it does not
validate complex coil performance, heat-recovery interactions, or the unknown
airflow reference basis. Those limitations keep the method under its existing
draft/review gate; they must be resolved before interpreting coil components
as design-validated.
