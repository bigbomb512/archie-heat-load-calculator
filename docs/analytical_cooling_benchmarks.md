# Analytical cooling benchmark suite

Wet-bulb calculations accept an explicit basis. Older project records without
one remain `legacy_unverified` and are not silently upgraded. `thermodynamic`
uses the above-freezing perfect-gas relation corresponding to ASHRAE
Fundamentals Eq. 33 and ASHRAE F25 Eq. 5 (IAPWS-IF97) for liquid-water
saturation pressure. Its result method ID is
`thermodynamic_ashrae_eq33_iapws_water_ice_v3`.
`psychrometer` retains the existing empirical
psychrometer-reading approximation. Its result is not a general instrument
correction: readings depend on psychrometer type and measurement conditions,
and this release has no matching published numerical reference case.
The v3 thermodynamic method adds ASHRAE F25 Eq. 6 saturation pressure over ice
and the below-freezing Eq. 37 relation. The measurement method remains
approximate near 0°C, as ASHRAE cautions that a wet/ice-bulb thermometer is
imprecise there.

The separate `tests/fixtures/psychrometric_basis_benchmarks_v1.json` records
ASHRAE Example 1 chart estimates and characterizes the legacy equation. The
chart values (6.5 g/kg and 56.7 kJ/kg) are approximate readings, so test
tolerances reflect the source precision and are not engineering acceptance
tolerances. The psychrometer case freezes arithmetic only. Neither suite
constitutes method approval or DA09/CAMEL parity.

Version 2 exact liquid-water property and load cases are in
`tests/fixtures/psychrometric_basis_benchmarks_v2.json`. They pin ASHRAE F25
Eq. 5 saturation pressure at six above-freezing temperatures, then check a
thermodynamic wet-bulb state and outside-air load from fixed published
equations. Version 1 remains a record of the prior approximation's behavior;
it is not used to characterize the new version 2 thermodynamic outputs.
Version 3 adds the ASHRAE F25 Eq. 6 ice-pressure and Eq. 37 sub-zero
thermodynamic cases in `tests/fixtures/psychrometric_basis_benchmarks_v3.json`.

Run the public-method regression cases with:

```sh
python3 -m unittest tests.test_analytical_cooling_benchmarks -v
```

The versioned expected values live in
`tests/fixtures/analytical_cooling_benchmarks_v1.json`. Inputs, units, equations,
source links, assumptions, exclusions, and rounding-only tolerances are recorded
with each case. Expected results are constants and are never obtained by calling
the production calculations. Failure messages show the benchmark, metric,
expected value, actual value, and signed deviation.

The suite covers single-surface opaque conduction; people, lighting, and
equipment gains; psychrometric outdoor-air and infiltration loads; equivalent
infiltration flow units; glazing conduction and solar transmission; invalid,
zero, and non-finite design inputs; and a 24-hour scheduled room peak with
non-coincident gain profiles.
The end-to-end case checks component sensible/latent totals, design total, and
peak hour. The test runner reuses the existing reviewed one-room model builder,
while every value that affects the benchmark result is explicitly specified in
the fixture.

## What a passing suite means

A pass shows that the tested calculator functions reproduce these documented
equations and fixed arithmetic cases to the stated output precision. It does
not mean the formulas are approved for design use, establish DA09/CAMEL parity,
or validate excluded factors. The legacy calculation suite continues to pin
its historical expected values. The new basis-aware suite checks the ASHRAE
thermodynamic chart example and separately characterizes the empirical
psychrometer approximation. Project-specific source classification still
requires engineering review. ASHRAE distinguishes psychrometer readings from
thermodynamic wet-bulb state in [Fundamentals Chapter 1](https://handbook.ashrae.org/Handbooks/F17/SI/f17_ch01/f17_ch01_si.aspx).

The broader verification approach is informed by [ASHRAE Standard 140](https://data.ashrae.org/standard140/),
but this load calculator is not being declared Standard 140 compliant. AIRAH
[DA09](https://www.airah.org.au/site/site/resources/da-manuals/da-09/Default.aspx)
remains the relevant load-estimation method reference; authorized DA09/CAMEL
case data and engineer review are still required for the separate parity and
release gate.
