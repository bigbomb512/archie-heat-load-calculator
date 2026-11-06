# Phase 1 on any drawing set (3 November 2026)

**User's rule:** Phase 1 gives a number on any PDF, every time. Each input uses the best evidence it can find and ends in a labelled value, and nothing waits or stays blocked for ever.

**Status:** on branch `phase1-always`. Offline tests and a comparison on saved jobs only, until the live checks are done. A number from these rules is not validated: most of the fallbacks below are estimates, and no result here has been compared with an engineer's.

## Why
House set 15 (2–3 November) took over two hours and gave a non-credible 2.0 kW, not complete. The wrong pages were traced, upper floors had no level, rooms were duplicated, a house's floor was named Store and Bar, walls facing outside got 0 kW, and most of the time went on local rebuilding rather than AI.

## Where each input comes from, best first

| Input | Evidence | Last step | Where |
|---|---|---|---|
| Plan page | The page reading's proposed whole-floor plan per level; else floor plans | A scaled services plan; else the drawing with the most linework (both labelled) | `_p0_page_selection` |
| Level | The page reading on its main plans (coverage's spelling when both mean the same level); else drawing coverage | "Unassigned level", one per job | `main_plan_levels`, `room_identity` |
| Scale | Printed areas; a printed number beside a dimension line, read from the text layer if it agrees with the sheet scale, else read by the AI | The sheet scale (unchecked); the AI reading the printed scale or scale bar; standard-size objects; an assumed 1:100 | `_p0_calibration_ready` |
| Rooms | Printed labels; notes and legends as clues | Named by use; house names in a dwelling; a note-only room the plan doesn't show is left out, labelled | `room_inference`, `rooms_not_on_plan` |
| Area | Printed area; outline; printed dimensions or AI measure | Remainder or AI estimate; a room with none is listed and the result says "Partial" | `P0_room_sizes`, `_phase1_coverage` |
| Height | Typed; printed | Pack default 2,700 mm, labelled | `ceiling_volume_resolution` |
| Walls | Operator; the AI | Shop: a neighbouring tenancy; house: outside | `_apply_p3`, `ai/house_envelope.py` |
| Roof | Operator; the AI; level plans in a house | Most likely case for the building type, labelled | `_roof_assumption` |
| Glass | Operator; elevation reading; house window schedule and tags | None read: no glass, listed | `_p4_packets`, `ai/house_windows.py` |
| Site and weather | Address, or a place, level and venue line | The AI's weather pick; the generic design day | `site_location_resolution` |
| North | True or corrected magnetic arrow, by majority | Sun averaged over the four directions, labelled | `_apply_p2`, `_averaged_surface_sun` |

**Known gaps in the table:** a house with windows on no schedule (sets 17, 19, 20) has no glass until the AI step exists. A room with an area but no outline has no walls, so its envelope isn't assessed and the result says "Partial".

## What the result says
- **Complete:** the included rooms have a number, so the job moves on. Only "no cooled room has a value" holds it back.
- **"Partial: N rooms not counted":** a room is left out, or "Partial: walls not assessed" if the rooms have no wall, roof or window resolved. A partial total undercounts, and the screen lists each reason.
- **Assumptions in this result:** a labelled fallback was used or a drawing check wasn't answered. These don't make a result partial.

## How a question ends
- **Answered:** applied, labelled below the accuracy bar where that applies.
- **Not needed:** nothing to ask (no kitchen, no dimension line, a page with no image), or another step takes over (a refused scale reading, a page no longer traced). Too few drawn corners keeps the AI's sketch.
- **Not answered:** refused twice, or the AI call failed twice. It doesn't hold the job; only a deliberate "Try again" or changed drawings ask again. A broken connection isn't counted, because it says nothing about the drawing.
- **Too long:** the evidence is shortened; what is still over its limit is asked with the overrun recorded. A limit never blocks.
- **North pages** wait only for pages that can still answer, and aren't asked again while waiting.

## Rounds and caching
Each round's questions go out together (`ai_provider.parallel_calls()`, no PDF work in the workers) and are applied one at a time in a fixed order. The questions and calculation inputs are rebuilt once per round, not per answer. Wall and area geometry is cached per page, keyed on the page-analysis generation, wall styles and scale.

## Checking a change
- `tools/compare_job_versions.py` runs two checkouts on copies of saved jobs with AI blocked, and prints every difference in rooms, loads and questions. Each one needs a reason.
- `tests/test_note_rooms_and_services_sets.py`, `test_phase1_pages_levels_site.py`, `test_north_unknown.py` and `test_drawing_task_speed.py` cover these rules.

## Lessons from the review (6 November)
The first text-layer scale passed its tests and would have made areas wrong by factors of hundreds on real jobs: text boxes were in PDF points against lines in pixels, the pairing score had no distance limit, and nothing compared the result with the sheet scale. A fixture can't catch this. Run new geometry on saved jobs before trusting it.
