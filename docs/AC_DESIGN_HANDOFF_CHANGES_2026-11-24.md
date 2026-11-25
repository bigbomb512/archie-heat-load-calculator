# AC design handoff: what changed (22–24 November 2026)

For whoever builds on `GET /api/ac-design-input` (schema `toki.ac-design-input`, version 1, documented in
[ac-design-input.md](ac-design-input.md)). The schema is still version 1: nothing was renamed or removed, so code
written against it keeps working. Several **values** change, and one field was added. A contract test
(`tests/test_ac_design_input_service.py`, `HandoffContractTests`) now fails if a documented field goes missing.

## Added field

- **`result_label`**: `preliminary` until no open assumption could change the suggested unit, then `design`;
  `not_calculated` without a result. An out-of-date result is always `preliminary`. Suggested use: allow inspection of
  a preliminary result, but show its `text` and `open[]` next to any equipment selection, and don't treat it as final.

## Values that change

| What | Before | Now | Effect on the handoff |
|---|---|---|---|
| Kitchens | Every kitchen was cooled | A **commercial** kitchen (restaurant, shop) defaults to not cooled; a house kitchen is cooled as before | Commercial kitchens move from `rooms[]` to `not_cooled[]` with that reason; `totals.peak_kw` drops by the kitchen's share |
| Fresh-air moisture | Outside air drier than the room gave a *negative* latent load that lowered the total | Counted as zero; sensible keeps its sign | `totals.latent_kw` and `peak_kw` rise where the peak hour had dry outdoor air (Casa Nova 30.97 → 33.95 kW) |
| Answered wall/roof constructions | Sun on the surface used the pack's U-value; a typed answer's description didn't reach the thermal-mass model | Both follow the answer | Jobs with construction answers change, up or down |
| House certificate (NatHERS/BASIX) | Not read for walls/roof | Labelled wall and ceiling insulation read as provisional U-values | House loads change where a certificate is in the set (Turramurra) |
| House windows | An old window scan could leave a house with no windows | Old scans re-run; marks matched loosely | House glazing appears where it was missing (Turramurra 5.07 → 9.81 kW) |
| Uncounted rooms | Reason text from the saved building-load report | Same rooms; the summary card now names a live reason (needs an area / walls still being worked out) | `not_counted[]` unchanged in shape |

## Stale until updated

Rule changes are versioned (`MODEL_RULES_VERSION` in `backend/ai_preliminary_service.py`, now 4). A saved result
from older rules reports `status.state = "stale"` and `status.ready = false` until its load is updated in the app;
`result_label.status` is then `preliminary`. Per the contract, hold equipment sizing until `status.ready` is true.
