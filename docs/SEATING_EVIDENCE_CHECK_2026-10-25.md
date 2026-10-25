# Seating occupancy follow-up — 25 October 2026

Follow-up to commit `cbeecf9`. This report supersedes the earlier comparison in this file. The default safety factor remains **1.0**; no occupancy diversity or safety allowance was added.

## Completed fixes

| Requirement | Change | Verification |
|---|---|---|
| Furniture labels cannot become tenancy totals | Bare-total parsing skips BANQUETTE, BOOTH, BENCH, SOFA, COUCH, LOUNGE, STOOL, CHAIR, OTTOMAN, TABLE, PER and SEATER (including plural labels). Explicit totals and QTY schedule rows retain their interpretation. Explicit totals stay on their own line, preventing `BOOTH 4 SEATS` followed by `TOTAL SEATS 140` from also matching as “4 seats total”. | Supplied examples, plural labels, explicit total on the same page and saved BB schedule tested. |
| Reject implausibly low printed totals | A level total below half its qualifying vector count is rejected. Rooms use a qualifying vector count or labelled density fallback, with both numbers in the warning. Room-specific totals receive the same check against that room’s vector count. Exactly half is not rejected. Printed totals above the count retain precedence and the existing >15% discrepancy warning. | 6 versus 72 rejected; 140 versus 72 accepted; half-threshold, room-specific and density fallback tests. Service conflict diagnostics ignore rejected low totals. |
| Exclude non-comfort air paths and generated occupants | refrigeration_process, not_a_room and unresolved_scope get no generated design occupancy. Outside-air and infiltration records have `status: excluded`, `value: null` and an outside-comfort-HVAC rationale. Scope exclusion also applies after old airflow overrides and explicit candidates are considered, preserving those decisions for audit without activating them. | Resolved-area freezer, all three scopes, retained override and explicit candidate tested; comfort-room minimum and flows unchanged. |
| Retain fixed project airflow without a complete people/area sum | Use a supplied fixed airflow when the people/area contribution is unknown; mark it provisional with `people_area_unknown: true`, an unresolved-field reason and explicit rationale. With a complete sum use `max(people + area, fixed)`; with neither keep null. | Both outside_air_lps and fixed_minimum_lps without area; max(sum, fixed) with area; no sum and no fixed figure tested. |

Implementation uses the existing seating allocation, internal-gains and airflow paths. No new workflow, provider call or database was introduced.

## Saved-job verification method

Both original job folders were copied before calculation. Before = committed `cbeecf9` behavior. After = these follow-up fixes. The offline runs re-resolved room use and equipment (no assess/locate), assembled the draft, confirmed supported scope and calculated Butcher Buffet on the copies. `backend.ai_provider.get` was patched to raise throughout every real-job run. No real AI/provider calls were made, no keys were printed, no live server/jobs were interrupted, and no original job data was written or deleted.

## Butcher Buffet

Job: `20260226-butcher-buffet-_-melrose-park-construction-rev-b_2_-1792650630`.

| Room / use | Area m² | Occupants before → after | Outside air L/s before → after | Source / status after |
|---|---:|---:|---:|---|
| Kitchen / kitchen | 117.91 | 35 → 35 | 297.872 → 297.872 | Density × existing resolved room area; outside air = people term + area term |
| Bar / dining | 32.55 | 10 → 10 | 84.764 → 84.764 | Labelled density fallback; one vector candidate is insufficient; outside air = people + area terms |
| Shop / dining | 203.53 | 140 → 140 | 1111.058 → 1111.058 | 140 seats from saved AI page-19 schedule reading; outside air = 140 × 7.5 + area × 0.3 |
| Coolroom / refrigeration_process | 10.75 | 1 → None | 10.726 → excluded | Outside comfort-HVAC scope; no generated occupants; outside air and infiltration excluded with null values |
| Freezer / refrigeration_process | 7.88 | 1 → None | 9.864 → excluded | Outside comfort-HVAC scope; no generated occupants; outside air and infiltration excluded with null values |

Coolroom and Freezer were already excluded from the comfort-HVAC calculation, but their stored occupancy and provisional airflow records were misleading. They now have null generated occupancy and excluded/null outside-air and infiltration records. The three calculated rooms are unchanged: Kitchen 35 occupants / 297.872 L/s, Bar 10 / 84.764 L/s, Shop 140 / 1111.058 L/s. This matches the requested airflow tolerances of ±0.01 L/s.

The saved AI page-19 reading says: “Seating schedule: table TB1 1200 x 800 mm, quantity 35, seats 140”. The vector estimate remains 72 seats in Shop, so 140 is above the count and retains precedence with the 48.6% discrepancy warning. This task does not independently establish that the saved AI interpretation or simultaneous design occupancy is correct.

### Coincident load at 15:00

| Component | Before kW | After kW |
|---|---:|---:|
| People | 23.1300 | 23.1300 |
| Lighting | 4.4601 | 4.4601 |
| Equipment | 7.4926 | 7.4926 |
| Envelope conduction | 0.5415 | 0.5415 |
| Solar | 11.3586 | 11.3586 |
| Outside air | 10.2405 | 10.2405 |
| Fan heat | 1.1514 | 1.1514 |
| Calculated load | 58.3747 | 58.3747 |
| Safety allowance (0%) | 0.0000 | 0.0000 |
| Final total, factor 1.0 | 58.3747 | 58.3747 |

**Total unchanged: 58.3747 kW.** No factor or physical assumption was tuned to alter the result. The correction removes misleading out-of-scope records, not previously included heat contributions. Against the user-provided engineer figure of 39.6 kW the difference remains +18.7747 kW. A matched comparison of scope, occupancy, ventilation, weather and gain assumptions is still required; software tests are not engineering validation.

**Phase 1:** before and after fingerprints are identical: `7540cbb1461883e34bfdd55cef143b964d79e343166930507cf78e50693c36ef`. No empty-room calculation inputs changed.

## Casa Nova

Job: `260320---interior-life---casa-nova-gosford---rev-d-3-1792837361`.

| Room / use | Area m² | Occupants before → after | Outside air L/s before → after | Source / status after |
|---|---:|---:|---:|---|
| Bar / dining | 4.77 | 1 → 1 | 8.931 → 8.931 | Density × existing resolved room area; outside air = people term + area term |
| Kitchen / kitchen | 12.94 | 4 → 4 | 33.883 → 33.883 | Density × existing resolved room area; outside air = people term + area term |
| Bathroom / ancillary_conditioned | 13.23 | 1 → 1 | 11.470 → 11.470 | Density × existing resolved room area; outside air = people term + area term |
| Dining / dining | 33.60 | 10 → 10 | 85.079 → 85.079 | Density × existing resolved room area; outside air = people term + area term |
| Shop / retail | 179.24 | 27 → 27 | 256.271 → 256.271 | Labelled density fallback; one vector candidate is insufficient; outside air = people + area terms |

All materialized occupants and outside-air inputs remain unchanged. Casa has no saved full Phase 2 report, so this check reports occupants and airflow inputs without generating a new full Phase 2 result.

Its indoor seating remains unresolved. P33 setout has no usable indoor chair count; p32 layout has raster furniture and only one inside vector candidate. P32 registers to p33 through four dimension anchors (110, 592, 1650 and 1850 mm). P35/p39/p57 cannot be registered and remain skipped. Density is therefore labelled fallback, not a recovered seat count. Some current room-use classifications remain weak; this task does not reclassify them.

**Phase 1:** before and after fingerprints are identical: `520f73241d573fdc4687ffe3f11a07279e3c0e58f8d811ea579ff58d15ae3a9b`. No empty-room calculation inputs changed.

## Tests and artifact checks

```sh
python3 -m unittest discover -s tests -p 'test_seating.py'
OPENROUTER_API_KEY= OPENAI_API_KEY= ANTHROPIC_API_KEY= DEEPSEEK_API_KEY= python3 -m unittest discover -s tests
cd frontend && npm test
```

- Seating tests: **26 passed**.
- Full Python discovery: **1,016 passed in 21.480 s** (`/tmp/seating-followup-tests.log`).
- Full npm test: every Python script test passed, then **52 Playwright tests passed in 9.9 s** (`/tmp/seating-followup-npm.log`). No UI code changed in this follow-up.
- Offline baseline: `OPENROUTER_API_KEY= OPENAI_API_KEY= ANTHROPIC_API_KEY= DEEPSEEK_API_KEY= python3 /tmp/review_seating_baseline.py`.
- Offline after: `OPENROUTER_API_KEY= OPENAI_API_KEY= ANTHROPIC_API_KEY= DEEPSEEK_API_KEY= python3 /tmp/seating_followup_after.py`.
- Those scripts patch `backend.ai_provider.get` to raise; they operate on copies under the directory recorded in `/tmp/archie-seating-review-path`. `before.json` and `after.json` retain inputs, per-room flows and reports.
- Main and standalone airflow entry points were compared and agree on all copied-room outside-air values/operands. Every calculated room also matches the materialized draft’s area, occupants and airflow.
- Original-folder SHA-256 and file-list checks: **915 Butcher files and 1,711 Casa files; zero changed, added or removed**.
- `git diff --check`: passed. No commit created.

## Remaining limitations

- No requested software fix remains unresolved in the tested cases.
- The 140-seat source is a saved AI reading; its physical interpretation and design-hour applicability were not independently validated in this task. One seat = one design occupant remains the labelled draft assumption. No new occupancy diversity factor was added.
- Casa seating coverage is incomplete for the concrete reasons above. A verified seating schedule or correctly registered readable furniture evidence is needed to replace density.
- Vector chairs/benches remain estimates. Live capture by the structured pass-1 prompt was not tested under the no-provider-calls constraint.
- Engineering accuracy and the discrepancy from 39.6 kW remain unvalidated.
