# Australian psychrometric and design-weather review

Status: **comparison prepared; AIRAH/DA09 climate-data validation and engineer
review remain blocked pending authorised source material and reviewer access.**
Prepared 29 September 2026. This report does not approve a calculation method,
Australian design condition, ventilation rate, or product release.

## Evidence checked

- AIRAH describes DA09 (4th edition, 2022) as covering design conditions,
  infiltration and ventilation, and applied psychrometrics. Its climate data
  are available as Australian Comfort/Critical sets; AIRAH's access page says
  those supporting files are restricted to DA09 purchasers or members with
  digital access and for individual use. See [DA09](https://www.airah.org.au/site/site/resources/da-manuals/da-09/Default.aspx)
  and [AIRAH's design-condition access terms](https://www.airah.org.au/site/site/resources/da-manuals/extracts/da-09/DA09_extracts.aspx).
- AIRAH publishes a psychrometric chart marked 101.325 kPa. It is a useful
  Australian reference chart, but its plotted coordinates are not a
  high-precision numerical table: [AIRAH Psychrometric Chart](https://www.airah.org.au/Common/Uploaded%20files/Archive/Resources/2020_AIRAH_Psychrometric_Chart.pdf).
- The server-side variable `ARCHIE_AIRAH_DESIGN_WEATHER_PACK_PATH` is not
  configured in this environment. No authorised AIRAH weather pack is present
  in project configuration. The active project snapshot has no selected
  location/weather-resolution artifact and its `design_day_scenarios.json`
  contains no scenarios. Therefore no project Australian climate values were
  available for comparison. Synthetic fixtures were excluded from evidence.
- Standards Australia identifies AS 1668.2:2024 as the current mechanical
  ventilation edition in its [publication overview](https://www.standards.org.au/blog/spotlight-on-as-1668-2024).
  This public overview is not the standard text and does not provide enough
  detail to encode its airflow tables or determine applicability to a project.

## Reproducible psychrometric sensitivity check

At 101.325 kPa and the same dry/wet-bulb numbers, the code distinguishes
ASHRAE's thermodynamic wet-bulb equation from the project's empirical
psychrometer relation. The thermodynamic path now uses ASHRAE Fundamentals
2025 Chapter 1 Eq. 5 (IAPWS-IF97) above freezing and Eq. 6 (IAPWS 2008) below
freezing, with the matching wet-bulb equations. The psychrometer path keeps its
prior approximation. Versioned reference cases are stored in
`tests/fixtures/psychrometric_basis_benchmarks_v2.json` and
`tests/fixtures/psychrometric_basis_benchmarks_v3.json`.

The site design-weather resolver now labels wet bulb derived from a
nonnegative dew point as thermodynamic and retains the corresponding method ID
in conversion provenance. Subzero dew-point conversions remain
`legacy_unverified` because the source field does not distinguish frost point
from a supercooled-water dew point. The selected weather review displays the
basis and the number of hours derived from dew point. This aligns the label
with the calculation path; it does not confirm the source dataset's meaning or
approve the method for design use.

| State or result | Thermodynamic | Existing psychrometer approximation | Difference |
| --- | ---: | ---: | ---: |
| 40°C DB / 20°C WB humidity ratio | 6.40339 g/kg | 5.98142 g/kg | +0.42197 g/kg (+7.1%) |
| 40°C DB / 20°C WB enthalpy | 56.731 kJ/kg | 55.645 kJ/kg | +1.086 kJ/kg (+2.0%) |
| 100 L/s OA load at 24/18°C indoors, 35/24°C outdoors | 2.3560 kW | 2.3125 kW | +0.0435 kW (+1.9%) |

The 40/20°C thermodynamic result is consistent with the broad chart-read range
from ASHRAE Fundamentals Example 1 (about 6.5 g/kg and 56.7 kJ/kg). Replacing
the prior approximate saturation-pressure formula changes the thermodynamic
humidity ratio from about 6.36681 to 6.40339 g/kg at 40/20°C. At the separate
100 L/s outside-air case above, the version 2 total increases from 2.3510 to
2.3560 kW relative to the earlier thermodynamic implementation. The AIRAH
chart uses the same standard-atmospheric pressure, but the available public
chart does not give a tabulated exact value for this point. This is a
calculation-basis sensitivity check, not an AIRAH DA09 method comparison or an
engineer-approved discrepancy limit. The `psychrometer` branch is still the
legacy empirical relation; it does not correct a reading for a named
psychrometer, ventilation velocity, wick, radiation, or measurement setup.

## Australian weather and ventilation status

No DA09 location records were compared because no authorised pack or selected
project weather profile is available. AIRAH's public material distinguishes
Comfort/Critical design datasets and gives their period and access limits; it
does not grant this project the underlying spreadsheet rights. The climate
comparison must wait for an authorised import using
`tools/import_airah_design_weather_pack.py`, followed by a project location,
selected weather basis, and source citation. Do not use the synthetic test
weather or ordinary climate normals as a substitute design day.

Archie's ventilation calculation currently embeds no code airflow rates. It
requires supplied rate bases and records a basis name/source; this is safer
than silently applying a US or Australian default, but it does not itself
establish compliance. For each project, the reviewer must identify the
applicable NCC edition, jurisdictional requirements, applicable AS 1668.2
edition/clauses and any approved project brief, then enter the approved
occupancy/area/fixed rates and cite their source. The full standard is not
reproduced here and its public overview is insufficient to derive rates.

## Australian HVAC engineer review checklist

An Australian HVAC engineer should review and record a decision for each item
before these methods are promoted beyond draft use:

1. Confirm whether the project weather source reports thermodynamic wet-bulb
   temperatures or instrument readings; identify any instrument correction
   method and measurement conditions.
2. Compare humidity ratio, enthalpy, specific volume, and resulting outside-air
   and infiltration loads against authorised DA09 examples/data at the cited
   pressure. Reconcile psychrometer and thermodynamic bases separately.
3. Select the applicable AIRAH Comfort or Critical design-condition dataset,
   location/station, observation period, design percentile, pressure/elevation,
   and design-day construction method. Confirm the selected record is suitable
   for the actual site and project duty.
4. Review the implemented below-freezing thermodynamic branch and decide
   whether the psychrometer approximation is suitable for any supported input
   type. ASHRAE cautions that wet/ice-bulb thermometers are imprecise at 0°C;
   the branch calculates a thermodynamic property rather than correcting an
   instrument reading.
5. Confirm the applicable Australian ventilation basis (NCC/jurisdiction,
   AS 1668.2 edition and clauses, room use, and project requirements) and
   reconcile each required rate and exhaust/make-up-air calculation to it.
6. Record engineer name, credential, date, source edition/clauses, numerical
   comparisons, accepted method scope, and explicit exclusions in the relevant
   project method gates. Do not treat this report or a passing test as approval.

## Remaining blocker and next concrete step

An administrator with the relevant AIRAH/DA09 rights must configure and import
the authorised weather data outside project review folders. An Australian HVAC
engineer must then review the selected psychrometric method and project
ventilation basis. No engineer was contacted and no approval has been recorded
by this work.
