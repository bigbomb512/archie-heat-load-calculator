# Annual energy method gate

`annual_energy_analysis_v1` is an orchestration method for a cited, non-leap
8,760-hour EPW/TMY weather sequence. It expands reviewed 24-hour schedules over
an explicit 365-day calendar and reuses the existing room, heating, radiation,
AHU, and plant adapters.

The project-local `annual_method_gate.json` is `placeholder` until a qualified
engineer supplies the method version, scope, approval date, credential, and
citations. Placeholder runs are development/review output only. They may be
`draft`, but cannot produce `review_ready` or `validated` status.

Annual weather, holidays, schedules, surface-plane radiation, topology, and
source citations are required inputs. No live weather lookup, country holiday
default, inferred surface orientation, schedule copying, or equipment operation
is permitted. Leap-year 8,784-hour files must be normalized outside this method
and the transformation recorded before import.

Annual weather records must form a continuous 8,760-hour local standard-time
sequence: each timestamp advances exactly one wall-clock hour and any explicit
UTC offset remains fixed. Missing/repeated hours or daylight-saving transitions
are rejected; normalize them to the source's documented fixed-time basis before
import and retain that transformation with the weather source. The annual
schedule expander maps records by 365 calendar days × 24 hours, so accepting a
23- or 25-hour local day would shift subsequent schedule assignments.
For EPW files, the EnergyPlus format describes hour values as 1–24 (hour 1
represents the first interval ending at 01:00) and carries daylight-saving
periods in the header separately from the data rows; Archie normalizes each
hour to the start of its represented interval. See the [EnergyPlus Auxiliary
Programs weather-file documentation](https://energyplus.readthedocs.io/en/latest/auxiliary-programs/auxiliary-programs.html).
The declared timezone on weather, calendar and any supplied annual radiation
artifact must also match exactly; the engine combines these records by hour
index and does not perform timezone conversion.

When an annual weather row supplies dew point but no wet bulb, nonnegative dew
points are converted to humidity ratio with ASHRAE Fundamentals F25 Eq. 5 and
then solved for thermodynamic wet bulb using the above-freezing Eq. 33 method.
The generated wet-bulb basis is recorded as `thermodynamic`; load results using
it record method ID `thermodynamic_ashrae_eq33_iapws_water_ice_v3`. Below-zero
dew-point inputs remain on the prior `legacy_unverified` conversion because
the source field does not distinguish frost point from a supercooled-water dew
point. See the fixed v2 psychrometric benchmark for the 25°C dry-bulb / 15°C
dew-point reference case. Annual cooling and heating use the same resolved
wet-bulb value and basis, so the recorded psychrometric provenance matches the
method used by each load calculation.

Annual results retain hourly demand, one-hour energy, monthly totals, annual
totals, governing hours, included scope, blockers, exclusions, and all input
fingerprints. Historical reports and packages are immutable. Annual AHU and
plant sections remain draft-only until their annual state adapters are complete.

Annual room-hour cooling and heating currently apply the legacy room safety
factors before annual aggregation. Each room result records the factor, source,
citations, and room-hour application point. A missing heating factor blocks
that hour rather than silently assuming 1.0. Annual analysis does not yet
consume the project-level safety policy; whether a design margin belongs in
annual energy totals remains an open owner/engineering decision.

Annual section aggregation aligns each room's rows by the stored 0–8,759 hour
index. Missing hours are not allowed to shift later demand into a different
time or month. Calendar dates determine month attribution even when every room
is missing an hour. Sections with gaps remain draft and report the affected
rooms, missing-hour counts, and compressed hour-index ranges; totals are
explicitly incomplete subtotals with the missing hours excluded. Hourly exports
leave incomplete demand and energy cells blank and include status and missing-
room IDs; monthly exports include incomplete-hour counts. An explicitly empty
or unsupported section selection blocks instead of silently selecting all
sections.

This document is a software method description, not engineering validation or a
CAMEL+ equivalence claim.
