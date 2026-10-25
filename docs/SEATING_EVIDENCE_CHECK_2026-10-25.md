# Seating occupancy review fixes — 25 October 2026

## Scope and result

This report supersedes the earlier seating comparison in this file. The earlier claim that outside-air methods were preserved was incorrect: supplying occupants activated a record that dropped the per-area term. This review restores that term and the draft’s existing summed formula.

| Review item | Implementation | Verification |
|---|---|---|
| Outside-air area and formula | Reuse the exact prepared draft room-area selection. Compute `max(people × L/s/person + area × L/s/m², fixed minimum)`, with 0.001 L/s rounding matching the draft. Record area and both flow operands. Unknown area remains unknown. | Missing-raw-area unit case; every calculated room in both saved jobs has identical area, occupants and outside air in the resolver and draft. |
| Printed totals | Spatial scans limited to pass-1 `floor_plan` or `schedule` pages. Reject singular seat notes, dimensions, per-table capacities and outdoor capacities. Prefer structured pass-1 totals, then explicit totals, then one fully resolved schedule. Ambiguous or incomplete rows warn and are not applied. Repeated text in a citation is not an extra row. | All supplied examples, two-row sum, ambiguous row, detail-sheet exclusion and structured-source precedence tested. |
| Count precedence and area | Operator override > room’s own source count > allocated printed total > vector count > density. Density uses the existing resolved room area, never a sum invented from the raw traces. | Own cited count survives both allocation and vector evidence; density tests retain resolved 80 m² despite raw traces totalling 999 m². |
| Shared maps | `_airflow_room_inputs` supplies both airflow entry points. Main draft and standalone internal-gains resolution share the same prepared area selection. | Both airflow entry points produce identical outside-air values and operands on both copied jobs. |

No diversity or allowance factor was added. One seat = one design occupant remains a labelled draft assumption, not an AS 1668.2 compliance claim. The existing bench assumption and optional profile staff allowance logic were not changed. No current profile defines a separate staff allowance.

## Confirmed safety policy

The user confirmed that removal of the default 10% safety allowance was intentional and supersedes the review instruction to retain it. Keep the existing default factor at **1.00 (0% allowance)**. This review did not change safety-policy code or configuration.

Under that policy, Butcher Buffet’s final calculated total is **57.6466 → 58.3747 kW**. No default safety allowance is added. The previously calculated 1.10 audit results are retained below only as historical comparison; they are not the selected policy or final totals. There is no pending safety-policy decision.

## Saved-data method

Before = the uncommitted seating implementation at the start of this review, including its missing-area airflow regression. After = the four corrections above. Both runs keep the current multi-factor room-use result and the same seating policy assumption. They re-resolve room use and equipment (without assess/locate), assemble, confirm the supported room scope, and calculate on copies. `backend.ai_provider.get` was patched to raise for all real-job checks. No original job files were written, no real provider/network calls were made, no live server or jobs were restarted, and nothing was committed.

## Butcher Buffet

Job: `20260226-butcher-buffet-_-melrose-park-construction-rev-b_2_-1792650630`.

| Room / use | Resolved area m² | Occupants before → after | Outside air L/s before → after | Occupancy source after |
|---|---:|---:|---:|---|
| Kitchen / kitchen | 117.91 | 35 → 35 | 262.500 → 297.872 | Room-profile density × the resolved draft area, rounded (minimum one) |
| Bar / dining | 32.55 | 10 → 10 | 75.000 → 84.764 | Density fallback; one vector candidate is insufficient |
| Shop / dining | 203.53 | 140 → 140 | 1050.000 → 1111.058 | 140 printed seats, saved page-19 reading; takes precedence over 72 vector candidates on p20 |
| Coolroom / refrigeration process | 10.75 | Excluded → excluded | — | Outside comfort-HVAC scope |
| Freezer / refrigeration process | 7.88 | Excluded → excluded | — | Outside comfort-HVAC scope |

The page-19 saved reading already contains “Seating schedule: table TB1 1200 x 800 mm, quantity 35, seats 140”. No new reading was needed. Only Shop has a usable vector count, so it receives the tenancy total. The 72 versus 140 discrepancy (48.6%) remains disclosed; Bar stays on density. Rejected/unregistered/raster symbols are not treated as proof of no seating.

**Expected-flow check:** Kitchen 297.872, Bar 84.764 and Shop 1111.058 L/s all match the requested values within ±0.01 L/s. Shop is `140 × 7.5 + 203.525879… × 0.3`.

### Coincident load at 15:00

| Component | Before review kW | After review kW |
|---|---:|---:|
| People | 23.1300 | 23.1300 |
| Lighting | 4.4601 | 4.4601 |
| Equipment | 7.4926 | 7.4926 |
| Envelope conduction | 0.5415 | 0.5415 |
| Solar | 11.3586 | 11.3586 |
| Outside air | 9.5124 | 10.2405 |
| Fan heat | 1.1514 | 1.1514 |
| Calculated load | 57.6466 | 58.3747 |
| Selected safety allowance (0%) | 0.0000 | 0.0000 |
| Final total under confirmed policy | 57.6466 | 58.3747 |
| Historical comparison only: total with 10% | 63.4113 | 64.2122 |

Against the user-provided engineer figure of **39.6 kW**, the final difference under the confirmed 0% allowance is **+18.0466 → +18.7747 kW**. The historical 10% comparison differences were **+23.8113 → +24.6122 kW**. No input was tuned toward the engineer figure. The engineer’s scope, weather, ventilation, solar, activity and diversity assumptions have not been independently supplied, so the difference is unresolved rather than explained away.

Historical context: before the original seating work, the density-based Shop had 61 occupants and the coincident load was 45.6213 kW before safety. Applying 140 printed seats raised the people contribution; this review fixes the separate area-ventilation regression. These are different comparisons.

**Phase 1:** before/after envelope input fingerprints are identical (`679fd9ce137810ae736b0e2d46e9007a64a58a999e205d8e273cf29eec7d6c7d`). No empty-room inputs changed. This compares the same ordinary working-tree safety policy on both sides; the separate 10% comparison does not change persisted inputs.

## Casa Nova

Job: `260320---interior-life---casa-nova-gosford---rev-d-3-1792837361`.

| Room / use | Resolved area m² | Occupants before → after | Outside air L/s before → after | Occupancy source after |
|---|---:|---:|---:|---|
| Bar / dining | 4.77 | 1 → 1 | 7.500 → 8.931 | Room-profile density × the resolved draft area, rounded (minimum one) |
| Kitchen / kitchen | 12.94 | 4 → 4 | 30.000 → 33.883 | Room-profile density × the resolved draft area, rounded (minimum one) |
| Bathroom / ancillary_conditioned | 13.23 | 1 → 1 | 7.500 → 11.470 | Room-profile density × the resolved draft area, rounded (minimum one) |
| Dining / dining | 33.60 | 10 → 10 | 75.000 → 85.079 | Room-profile density × the resolved draft area, rounded (minimum one) |
| Shop / retail | 179.24 | 27 → 27 | 202.500 → 256.271 | Density fallback; one vector candidate is insufficient |

No saved full Phase 2 report exists for this Casa job. As requested in the original task, this check reports occupants and airflow inputs rather than generating a new full Phase 2 total.

Seating remains unresolved: p33 setout has no usable indoor count; p32 layout contains raster furniture and only one inside vector candidate. P32 registers to p33 through four dimension anchors (110, 592, 1650, 1850 mm). P35/p39/p57 cannot be registered and are skipped. Outdoor/other candidates outside eligible cooled outlines are excluded. Zero detected symbols is not zero occupants. A verified seating schedule or registered readable furniture evidence is still needed.

Some current Casa room-use classifications remain weak. This review does not silently rename or reclassify them.

**Phase 1:** before/after envelope input fingerprints are identical (`7d9c0aa1359f1ee6f06ff1cb5e15e1c0df2b0d7d4a29167c95652b9babf762f0`). No empty-room inputs changed. This compares the same ordinary working-tree safety policy on both sides; the separate 10% comparison does not change persisted inputs.

## Parser interpretation

- `QTY 35 SEATS 140` resolves as 140 total places; `QTY 10 SEATS 4` resolves as 40.
- A small `S ≥ Q` pair such as `QTY 2 SEATS 4` supports both four row-total places and eight per-table places. It is deliberately unresolved, following the explicit ambiguous example in the review request. Larger row totals require an integer S/Q from 1 to 12; Q=1 is unambiguous.
- Rows from one schedule are summed only if every row resolves. Multiple labelled schedules are not merged. Explicit totals take precedence over schedule rows; structured pass-1 totals take precedence over parsed text. Conflicting totals at the same priority remain visible.

## Verification

```sh
OPENROUTER_API_KEY= OPENAI_API_KEY= ANTHROPIC_API_KEY= DEEPSEEK_API_KEY= python3 -m unittest discover -s tests
cd frontend && npm test
```

- Targeted `python3 -m unittest discover -s tests -p 'test_seating.py'`: 20 passed.
- Full discovery: 1,010 passed in 21.617 s (`/tmp/review-seating-tests.log`).
- Full `npm test`: every Python script test passed, followed by 52 Playwright tests passed in 9.4 s (`/tmp/review-seating-npm.log`).
- Offline scripts: `/tmp/review_seating_baseline.py`, `/tmp/review_seating_after.py`, `/tmp/review_seating_safety.py`, all run with the four provider-key variables blank and `backend.ai_provider.get` patched to raise.
- Copy artifacts are under the directory recorded by `/tmp/archie-seating-review-path`: `before.json`, `after.json`, and `comparison-10-percent.json` retain the actual inputs and reports.
- Both airflow entry points were compared for every copied room, including operands. Resolver and materialized draft outside air are equal in every calculated room.
- SHA-256 plus file-list checks: 915 original Butcher files and 1,711 original Casa files, **zero added, removed or changed**.
- `git diff --check`: passed. No UI code changed in this review.

## Remaining limitations / decisions

- Safety policy is settled: the user confirmed removal of the default 10% allowance. The selected default remains 0%; no safety-policy code was changed by this review.
- Casa’s seated occupancy cannot be established from the saved vector evidence; density stays labelled as fallback.
- Chair/bench recognition remains a draft heuristic. Live capture by the structured pass-1 prompt was not tested, under the no-provider-calls constraint.
- The engineer’s 39.6 kW comparison remains unexplained without matched calculation assumptions. No engineer validation is claimed.
