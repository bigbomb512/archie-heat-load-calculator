# Phase 2 equipment catalogue

## Editable source

Edit `data/equipment_catalogue.xlsx`. This is the canonical workbook; the Downloads copy was moved after verifying the destination. The runtime reads it with openpyxl and never writes to it. No database server or generated catalogue copy is required.

All 442 existing IDs, numerical evidence, source references, refrigeration guidance and Room worksheet formulas were preserved. Start here was shortened and its column references corrected. Unresolved rows now start with: **Further research required if this item is used in the project.**

Add rows below the existing entries with a new, unique equipment ID. Keep IDs unchanged when sorting or renaming. Enter aliases separated by semicolons. The application discovers the header row and reads populated rows by header name, including added entries. Extend the Excel filter, selection and Room worksheet lookup ranges when adding entries beyond their current bounds.

## Typical values workbook

`data/equipment_typical_values.xlsx` is the generic database Phase 2 needs. It is kept separate from the catalogue so the app never rewrites the 442-row workbook and its formulas. It has two sheets.

- **Typical values:** one row per equipment type and size band (60 rows to start: kitchen and food retail first, then office and retail items, then one *generic* row per kind of item). Each row has drawing abbreviations as aliases, a rule for telling the size from the drawings, the catalogue rows it could be based on, sensible and latent W per unit free-standing, the same under a hood (cooking only), a use pattern and a heat rejection location. The four W columns ship blank: blank means *not filled in yet* (the row isn't used and isn't an error); 0 is a real zero. A filled row needs an HTTPS source.
- **Use patterns:** `CONTINUOUS` (on every hour, for self-contained refrigeration whose published heat is already averaged over compressor cycling) and `WHILE_OPEN` (follows the room's opening hours with the pattern's fraction, or the room type's equipment diversity when the fraction is blank).

Invalid filled rows are quarantined with their reasons and shown on the Operating screen; duplicate IDs quarantine every colliding row.

## The value ladder

Phase 2 never stops for a missing equipment value; it uses a labelled typical or generic value and says so (user decision 2026-10-20, replacing the strict applicability rule described in the earlier plan). Each item takes the first of:

1. **Data sheet** — the operator entered the heat to the room from the item's data sheet.
2. **Matched model** — the drawings print a make or model that matches a `CONDITIONAL_MATCH` catalogue row and the AI quotes it. Catalogue cooking rows are free-standing test values, so they aren't used for an item under a hood.
3. **Typical value** — the row for the item's type and size band. The AI picks the band when more than one fits. The under-hood value is used only when the drawings show the item under a canopy or hood (quoted); otherwise, and when it isn't known, the free-standing value (user decision 2026-10-20).
4. **Generic value** — a rating the operator typed × its factor; else the sheet's generic row for the kind of item; else the rated power printed on the drawings or the generic placeholder rating × heat-to-room factor (`ai/equipment_heat.py`).

Otherwise the item has **no value yet**. An item whose room isn't known is **room unknown**. Neither is counted, and every room still takes the larger of its listed items and its typical equipment allowance, so a room never drops to zero. Hoods, refrigerated rooms and lighting are excluded: they belong to airflow, refrigeration and lighting.

Room and quantity come from the AI's quoted answer, else the equipment reading (room, or a room named in the printed location), else the AI's unquoted room (labelled); quantity one is assumed and labelled when nothing shows it. Possible duplicates are counted and flagged for the operator.

The AI step (`equipment_applicability`) runs only in a requested full Phase 2 calculation, on consented pages, eight items per call, with only the cited pages' readings. It picks the size band, a printed model, whether the item is under a hood, the room and the quantity, quoting the drawings. Quotes are checked against the readings. Without consent, a key or a reply, the ladder still runs on the reading alone and the screen says why. A missing or unreadable workbook is reported but doesn't stop the calculation either.

The Operating screen labels every item (Data sheet, Matched model, Typical value TV-…, Generic value, No value yet, Room unknown) with its source, sheet row and use pattern, and sums the counts in one line. Results lists items with no value or no room under "to find", typical values as typical and generic values as placeholders. The report label names how many typical and generic values were used.

## Artifacts and freshness

`equipment_catalogue_resolution.json` stores applicability assessments, the workbook content hash, source hash, resolved emissions, reasons and coverage. Assessments are reused only for the same catalogue, extraction, decisions, rooms and reading evidence. Invalid workbook rows are quarantined with row-specific diagnostics; duplicate IDs quarantine every colliding row. A missing or unreadable workbook stops a new full calculation while retaining saved reports.

Existing equipment responses include `catalogue_resolution` for each finding and `equipment_coverage` for the job. The model response includes coverage and `hourly_ai_preliminary_load_report_stale`. Reports retain source hashes and provenance. Workbook changes mark dependent Phase 2 results stale; opening the screen does not replace an existing report. Use **Update final load** to recalculate. Initial Phase 2 calculation still starts automatically when the existing workflow is ready.

Envelope reports use a separate projection fingerprint excluding operational gains and equipment schedules. Room uses, ceiling heights and airflow are resolved from the room proposal without equipment, so neither workbook nor an equipment change invalidates Phase 1.

## Current state (2026-10-20)

- The typical values are not filled in yet; the partner fills them from sourced data (ASHRAE Fundamentals ch. 18 tables 5A–5E for cooking). Until then items fall to generic values or have no value, and the rooms' typical allowance covers them.
- Read-only check on the latest saved jobs, with no AI step and the sheet unfilled: Butcher Buffet 3 generic, 16 no value, 23 room unknown (most fridges and freezers have no room in the reading; the AI step places them); Casa Nova 11 generic, 26 no value, 3 room unknown.
- The AI step has not been run on a real job: it needs a valid OpenRouter key and the user's approval of the cost.

The sections below record the strict version's earlier verification; its applicability rule is replaced by the ladder above.

## Verification limits

This integration does not verify the engineering accuracy of every source value or finish the people, lighting and airflow workflows. The workbook contains 298 conditional rows, 141 reference-only rows and three preliminary assumptions. A source-by-source audit remains separate work.

Offline checks against the latest saved Butcher Buffet and Casa Nova jobs found 42 and 40 equipment findings respectively. Candidate retrieval found 29 and 14 items respectively. Before a new applicability assessment, all remained research-required: the saved jobs contained no catalogue applicability decisions or complete assigned equipment schedules. Those counts describe pending saved evidence, not a conclusion that every item can never be resolved by AI.

Focused tests cover loading, quarantine, cache invalidation, citations, ambiguity, overrides, zero/latent-only gains, consent, provider failure, schedule calculations, fingerprints and report retention. Browser tests cover equipment outcomes, editing, mobile layout and the existing guided journey. Live assessment results are recorded separately when exercised; passing software checks do not establish engineering validation.

### Live verification

The new assessment was attempted on temporary copies of both saved jobs, reusing their page inventories. OpenRouter rejected the configured key with HTTP 401, “API key expired”, on the first request for each job. No applicability assessments succeeded, no extraction was repeated and the original job files and reports were not rewritten. Consequently the live runs retained 42 and 40 research-required items. A valid key in the application runtime is required to complete live applicability verification; an automated fixture separately verifies successful assessment, citation checks and cache reuse.

The normal server's startup also contained a leftover reference to the removed `--test` option. The obsolete print statement was removed to allow the existing browser verification server to start. No test-mode functionality was restored.

### Final software checks

- 79 focused backend tests passed, including an end-to-end fixture that starts with an unaccepted drawing finding and saves a calculated Phase 2 report with 0.08 kW sensible and 0.02 kW latent equipment gain. Quantity two and the 0.5 operating fraction are applied once.
- 42 browser checks passed (three equipment catalogue scenarios plus 39 existing guided-journey scenarios), using the normal local server with provider credentials disabled for browser tests. Desktop and mobile screenshots were inspected; a wrapping issue in operating-load facts was corrected.
- The preliminary calculation, internal gains, hourly loads, room-scope confirmation, report packaging and read-endpoint script checks passed. Python compilation, JavaScript syntax and diff whitespace checks passed.
- All 442 rows load without quarantine diagnostics. The edited workbook was reopened and rendered. Original formula XML and untouched package parts were compared against the pre-move workbook.

Reproduce the focused checks with:

```sh
PYTHONPATH=. python3 -m unittest tests.test_equipment_catalogue tests.test_equipment_heat tests.test_equipment_allowance tests.test_job_progress tests.test_job_service tests.test_result_status tests.test_ai_preliminary_workspace_view tests.test_ai_preliminary_skill_bridge
cd frontend
./node_modules/.bin/playwright test tests/equipment-catalogue.spec.mjs tests/job-preview.spec.mjs
```

Live-provider validation remains blocked by the expired key available to the verification process. After supplying a valid key through the existing runtime setup and restarting the server, request the full Phase 2 calculation to assess the existing saved readings; no PDF re-extraction is required.
