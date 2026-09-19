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

Annual results retain hourly demand, one-hour energy, monthly totals, annual
totals, governing hours, included scope, blockers, exclusions, and all input
fingerprints. Historical reports and packages are immutable. Annual AHU and
plant sections remain draft-only until their annual state adapters are complete.

This document is a software method description, not engineering validation or a
CAMEL+ equivalence claim.
