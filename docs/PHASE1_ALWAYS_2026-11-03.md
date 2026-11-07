# Phase 1 on any drawing set (3 November 2026)

**User's rule:** Phase 1 gives a number on any PDF, every time. Each input uses the best evidence it can find and ends in a labelled value, and nothing waits or stays blocked for ever.

**Status (7 November):** merged after live runs on four jobs on OpenRouter, each giving a number with no crash: Happy Cap 0 kW, Global Exchange 0.53 kW, set 18 9.35 kW, set 15 9.22 kW (set 15 needed resuming after fixes). Known gaps: Global Exchange's trace covers about half the lease, room names can be wrong (set 15's "Ens" over the garage, 29.5 m² of "Stairs"), "complete" can't see rooms nobody listed, and house detection depends on what the page reading reports. A number from these rules is not validated: most of the fallbacks below are estimates, and no result here has been compared with an engineer's.

## Why
House set 15 (2–3 November) took over two hours and gave a non-credible 2.0 kW, not complete. The wrong pages were traced, upper floors had no level, rooms were duplicated, a house's floor was named Store and Bar, walls facing outside got 0 kW, and most of the time went on local rebuilding rather than AI.

## Where each input comes from, best first

| Input | Evidence | Last step | Where |
|---|---|---|---|
| Plan page | The page reading's proposed whole-floor plan per level, never a diagram (GFA, shadow, landscape, roof, site) or a sheet covering several levels; else floor plans | A scaled services plan; else the drawing with the most linework (both labelled) | `main_plan_pages`, `_p0_page_selection` |
| Level | The page reading on its main plans (coverage's spelling when both mean the same level); else drawing coverage. A main plan with no level joins the only named level, because they are one floor | "Unassigned level", one per job | `main_plan_levels`, `room_identity` |
| Scale | Printed areas; a printed number beside a dimension line, read from the text layer if it agrees with the sheet scale, else read by the AI | The sheet scale (unchecked); the AI reading the printed scale or scale bar; standard-size objects; an assumed 1:100 | `_p0_calibration_ready` |
| Rooms | Printed labels; notes and legends as clues | Named by use; house names in a dwelling; a note-only room the plan doesn't show is left out, labelled; a label seen only off the main plans that isn't a known room is set aside | `room_inference`, `rooms_not_on_plan`, `reconcile_spaces` |
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
- **Unlocked by the supervisor:** questions its fixes create are answered once at the end of its run, so the result uses them. Anything still waiting after it finishes doesn't hold the job; it is offered as "Check and resume".

## Rounds and caching
Each round's questions go out together (`ai_provider.parallel_calls()`, no PDF work in the workers) and are applied one at a time in a fixed order. The questions and calculation inputs are rebuilt once per round, not per answer. Wall and area geometry is cached per page, keyed on the page-analysis generation, wall styles and scale.

## Checking a change
- `tools/compare_job_versions.py` runs two checkouts on copies of saved jobs with AI blocked, and prints every difference in rooms, loads and questions. Each one needs a reason. Its last step runs the real drawing and supervisor code with a fake AI, which costs nothing and reaches crashes the saved answers don't.
- `tests/test_note_rooms_and_services_sets.py`, `test_phase1_pages_levels_site.py`, `test_north_unknown.py` and `test_drawing_task_speed.py` cover these rules.

## Lessons from the review (6 November)
The first text-layer scale passed its tests and would have made areas wrong by factors of hundreds on real jobs: text boxes were in PDF points against lines in pixels, the pairing score had no distance limit, and nothing compared the result with the sheet scale. A fixture can't catch this. Run new geometry on saved jobs before trusting it.

## Linework for main plans (7 November)
Linework is extracted before the AI reads the pages, from a shortlist by page text. A main plan that shortlist missed now gets its linework when the drawing step starts (`_add_main_plan_linework`). Before this, set 15's first floor had no walls to enclose and every outline drawn on it was refused.

## Room names in outline answers (7 November)
A name given again in one outline answer is another part of that room, and only a near-identical outline is dropped. In a house, a name the AI was asked to draw becomes a room once it is drawn. Before this, set 15's whole ground-floor answer was refused because four bedrooms shared the label "Bedrooms", and then its names were lost on saving. A shared name stays one room, so the occupant count for it is low until Phase 2 handles it.

## Floor counted once (7 November)
When an outline is saved, an outline that mostly covers a room already traced there is the same space under another name: it isn't counted again, and any earlier sketch of it is cleared. An outline that partly overlaps a room loses the overlap. A room drawn inside a larger one stays whole; where the larger room was traced first it still includes that floor, so small double counts can remain.

## Lessons from the first live checks (6 November)
All four jobs failed the first live run, for US$0.457, though 1,209 tests passed: a not-needed north record crashed the export (GE, set 18), a missing import crashed the supervisor on any house with an ensuite (set 15), diagrams were traced as floor plans, one floor was split into two levels (Happy Cap), and leftover questions held the job at "drawings". Run the fake-AI step on real job data before spending.
