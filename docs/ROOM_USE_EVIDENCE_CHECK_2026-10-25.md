# Room-use evidence comparison — 25 October 2026

All resolutions were run read-only on temporary copies. No provider calls, no persisted job changes, no engineering validation claim.

## casa: `260320---interior-life---casa-nova-gosford---rev-d-3-1792837361`

| Name (unchanged) | Area m² | Position evidence | Factors found | Before → after | Confidence / weak evidence |
| --- | ---: | --- | --- | --- | --- |
| Bar | 4.8 | Internal edges; access not established | No independently localized contents; 12 name-based placements recorded with zero weight | dining → dining | medium; FLAG: insufficient independent evidence |
| Bathroom | 13.2 | Internal edges; access not established | No independently localized contents | ancillary_conditioned → ancillary_conditioned | low; FLAG: insufficient independent evidence |
| Dining | 33.6 | Mall/external edge; no applied glass/frontage proof | No independently localized contents; 1 name-based placements recorded with zero weight | dining → dining | low; FLAG: insufficient independent evidence |
| Kitchen | 12.9 | Internal edges; access not established | PIZZA, PREP; 16 name-based placements recorded with zero weight | kitchen → kitchen | high |
| Shop | 179.2 | Mall/external edge; no applied glass/frontage proof | DISHWASH | retail → retail | medium; FLAG: insufficient independent evidence |

Equipment resolution, placements and heat values were byte-for-byte equal as Python objects before and after use resolution.

## casa_misnamed: `260320---interior-life---casa-nova-gosford---rev-d-3-1792833415`

| Name (unchanged) | Area m² | Position evidence | Factors found | Before → after | Confidence / weak evidence |
| --- | ---: | --- | --- | --- | --- |
| Bar (two traced parts: 12.9 + 4.8) | 17.7 | Internal edges; access not established | PIZZA, PREP; 12 name-based placements recorded with zero weight | dining → kitchen | high |
| Bathroom | 15.0 | Internal edges; access not established | No independently localized contents; 1 name-based placements recorded with zero weight | ancillary_conditioned → ancillary_conditioned | low; FLAG: insufficient independent evidence |
| Dining | 177.3 | Mall/external edge; no applied glass/frontage proof | DISHWASH; 3 name-based placements recorded with zero weight | dining → dining | medium; FLAG: insufficient independent evidence |
| Kitchen | 33.6 | Mall/external edge; no applied glass/frontage proof | No independently localized contents; 15 name-based placements recorded with zero weight | kitchen → kitchen | low; FLAG: insufficient independent evidence |

Equipment resolution, placements and heat values were byte-for-byte equal as Python objects before and after use resolution.

## butcher: `20260226-butcher-buffet-_-melrose-park-construction-rev-b_2_-1792650630`

| Name (unchanged) | Area m² | Position evidence | Factors found | Before → after | Confidence / weak evidence |
| --- | ---: | --- | --- | --- | --- |
| Bar | 32.5 | Internal edges; access not established | 2 DOOR DRINK FRIDGE, ICE MAKER, UB FRIDGE, Underbench drink fridge / display fridge | dining → dining | high |
| Coolroom | 10.8 | Wall exposure unknown; access not established | No independently localized contents | refrigeration_process → refrigeration_process | high |
| Freezer | 7.9 | Wall exposure unknown; access not established | No independently localized contents | refrigeration_process → refrigeration_process | high |
| Kitchen (two traced parts: 104.1 + 13.8) | 117.9 | Applied shopfront/glazing | 2 DOOR DRINK FRIDGE, 3 DOOR DRINK FRIDGE, COMBI OVEN, OVEN, UB FREEZER, UB FRIDGE; 1 name-based placements recorded with zero weight | kitchen → kitchen | high |
| Shop | 203.5 | Applied shopfront/glazing | Digital menu board screen, Illuminated sign | retail → dining | high |

Equipment resolution, placements and heat values were byte-for-byte equal as Python objects before and after use resolution.

## Remaining uncertainty

- The misnamed 12.9 m² back strip (part of the room named Bar) changes to kitchen based on registered PREP/PIZZA text. Its companion 4.8 m² part shares the stable room-use identity.
- The misnamed 33.6 m² room named Kitchen is retained as kitchen at low confidence: its saved equipment locations follow the name, its P6 items cite an elevation rather than a localized plan item, and its storefront assignment is not established. It is explicitly flagged; the code does not manufacture dining evidence.
- In the corrected Casa run, the 4.8 m² Bar and main Shop also remain flagged where independent local evidence is weak. A dishwasher label alone cannot make the entire main Shop a kitchen.
- Butcher Buffet Shop changes retail → dining: menu-board contents, applied frontage/glazing, largest front room and hospitality tenancy support the change. Other uses and refrigeration scope remain unchanged.
- Missing door connectivity is not inferred from absence of openings. No unsupported back-of-house access points are awarded.
- Original files differing from the untouched copies: **0**.

## Verification

- `OPENROUTER_API_KEY= OPENAI_API_KEY= ANTHROPIC_API_KEY= DEEPSEEK_API_KEY= python3 -m unittest discover -s tests`: **990 tests passed in 19.509 s**.
- `cd frontend && npm test`: **exit 0**; all per-file Python scripts passed, followed by **50 Playwright tests passed in 8.9 s**.
- `python3 -m py_compile ai/room_use_resolution.py backend/room_use_resolution_service.py backend/ai_preliminary_service.py backend/job_plan_service.py`: passed.
- `git diff --check`: passed.
- Desktop and mobile room-use screenshots inspected. The browser case checks explanation, factors, the operator override payload and no horizontal page overflow.
- Saved comparisons were run with `backend.ai_provider.get` patched to raise if called. Equipment resolution objects were identical before/after. Final SHA-256 comparison against the untouched copies found **0 changed original files** across all three named jobs.
- No commit and no restart/stop of the user's server or jobs. The browser suite used its separate test server.

The scoring, fingerprints, precedence and UI are implemented and tested. Full correction of the misnamed Casa frontage remains **unresolved for lack of independently placed evidence**; it is flagged at low confidence rather than called successfully corrected. A validated shopfront assignment or localized room contents would allow the existing scorer to revisit it. Software test success does not establish engineering accuracy.
