# End-to-end walkthrough — Butcher Buffet (2026-10-02)

Purpose: take a copy of the Butcher Buffet drawing set from upload to a cooling
result in the real web UI, as a first-time contractor would, and record every
blocker. No code was changed during the walkthrough. All room boundaries,
design conditions and the floor are **walkthrough assumptions**, not reviewed
engineering inputs; the reviewer name used everywhere was
"Claude walkthrough (not a real review)".

Project: `walkthrough-butcher-buffet-_not-for-design_-1790926231`
(still present under `output/web_review/`; `output/web_projects.json` backup is
in the session scratchpad).

## Result

**No cooling result was reached.** The run stopped at a gate that the UI cannot
satisfy:

- `POST /api/model-input-resolution` → 422 `room_area_unresolved`
  ("no comfort-scope room has a validated area") even after all five rooms were
  traced, calibrated, accepted in the calculator draft and applied into
  `hourly_load_model.json`.
- Root cause (code read, not changed): `_room_area_coverage`
  (`backend/model_input_resolution_service.py:239`) only counts areas from
  `geometry_resolution.entities` of kind `area` with status
  `ai_estimated`/`geometry_confirmed`, `building_evidence.spaces`, or the AI
  preliminary run proposal. It never reads `room_geometry_proofs` (reviewer
  traces) or the reviewed rooms in `hourly_load_model.json`.
- The manual placeholder proposal cannot be used as a workaround either: the
  local room-inference proposal (all `area_m2: null`) takes precedence over any
  manual proposal (`run.get("local_room_inference_proposal") or ...`).
- Independently, the AI preliminary calculation needs `schedule_library.json`
  and `design_day_scenarios.json`; these are only created by the blocked
  resolver, and design-day weather also needs a resolved site location (the
  PDF yields 0 address clues; the city was not confirmed).

What *was* achieved:

| Room | Traced area (m²) | Calibration | Applied to hourly model |
|---|---|---|---|
| Shop (dining) | 229.06 | agreed 0.22% on 11825 mm | yes |
| Kitchen (incl. rear corridor) | 98.94 | agreed 0.22% | yes |
| Bar | 30.78 | agreed 0.22% | yes |
| Coolroom | 10.66 | agreed 0.22% | yes (excluded: refrigeration) |
| Freezer | 7.67 | agreed 0.22% | yes (excluded: refrigeration) |

Floor `floor_tenancy` (provisional, assumption), design inputs (24/21 °C
indoor, 35/5 °C outdoor, ceiling 2900 mm — all marked provisional
assumptions) were saved.

## Timeline (active time)

| Step | Time |
|---|---|
| Upload + analysis | ~1 min |
| Confirm drawings | ~1.5 min |
| Unlock reviewed workspace (placeholder paste) | ~10 min |
| First room trace (learning the tool) | ~19 min |
| Remaining 4 traces (keyboard, scripted) | ~10 min |
| Calculator draft: accept/save/preview/apply (two passes) | ~25 min |
| Floor + design inputs | ~10 min |
| Resolver / preliminary attempts until blocked | ~20 min |

A human contractor without scripted keyboard input would be slower on the
traces.

## Friction and bug log

Severity: **B** = blocks the path, **H** = high friction / likely abandon,
**M** = confusing, **L** = polish. "Bug" = behaviour contradicts the code's own
intent.

| # | Sev | Finding |
|---|---|---|
| 1 | **B, bug** | Reviewer traces never satisfy the model-input resolver's area gate (see Result). The resolver's own remediation tells the user to trace rooms. |
| 2 | **B** | Local room-inference proposal (null areas) shadows any manual placeholder proposal, so there is no manual area route. |
| 3 | **B** | Guided "Resolve model inputs" starts the skill workflow first; applying the draft makes the workflow `stale`, and the guided flow aborts on `stale` before calling the resolver. Every edit re-triggers this. |
| 4 | **B** | Whole reviewed workspace is hidden until a ChatGPT reply is pasted; "Open geometry editor"/"Open reviewed workflow" dead-end. Workaround was pasting `{}` under Advanced recovery (two collapsed sections deep). |
| 5 | **B** | Preliminary calculation requires schedule library + design-day weather that only the blocked resolver creates; no UI to create them directly. |
| 6 | **B, bug** | `backend/web_app.py:3049` calls `artifact_snapshot` without importing it (it lives in `ai/hourly_loads.py`) → `GET /api/hourly-load-model` raises NameError once a model exists. Present in committed HEAD. |
| 7 | **B, bug** | `GET /api/ahu-resolution`, `/api/safety-factor-resolution`, `/api/plant-resolution` return 400 "'Handler' object has no attribute 'safe_link'" on every project. |
| 8 | **B** | Calculator draft cannot apply zones/rooms without a floor; this set states no level, so no floor candidate exists. "Add floor" is hidden behind "Show all tools" and saving it first requires design inputs to be saved. |
| 9 | H | Calculator draft presents 122 decisions (80 proposals + 42 review items) with no bulk accept. |
| 10 | H | Each accepted candidate needs its own reviewer name; each edited candidate also needs an "Engineer review source"; both inside collapsed `<details>`. Errors report one candidate at a time. |
| 11 | H | Saved reviewer names are not re-displayed after re-render, so a second save fails until all are re-entered. |
| 12 | H | Preview without saving fails with only "Could not update calculator draft"; the real reason flashes in a toast. Preview output is not visibly rendered; Apply first reported "0 records created" with no reason (reason was the missing floor). |
| 13 | H | Draft panel sits ~25,000 px down one page; 22 "Resolve/Build" buttons visible at once; three navigation schemes (9-stage workflow, 5-step guide, tab bar). |
| 14 | H | Tracing: switching room silently clears trace, calibration and page; calibration must be re-entered per room; keyboard focus lost after every button click (~12 Tabs to reach the plan); click precision ±80 mm. |
| 15 | H | After saving a trace the room dropdown still says "area unresolved" and the panel says "No saved trace". |
| 16 | M | Plan has no room labels; mapping rooms needed the RCP (p.22). RCP ceiling heights (CH 2600/2900/3150, dining to slab ~3750) and lighting legend are not used automatically. |
| 17 | M | Design-inputs form starts empty although the app has preliminary profiles; ceiling height still reported "missing" after entering 2900 + provisional. |
| 18 | M | "Resolve model inputs" 422 remediation says "Do not use the drawing scale", contradicting trace calibration which uses it. |
| 19 | M | Header shows "Rooms 0" while the workflow shows "Rooms 5/5"; review list only shows low-confidence detail pages, not the key plans. |
| 20 | M | Three competing "next actions" after confirmation; "Confirm selected drawings" does not show the selection. |
| 21 | L, bug | "Selection confirmed" toast renders raw `<a>` HTML as text. |
| 22 | L | At 800 px width the "Guided next action" card collapses to one word per line with the button overlapping. |
| 23 | L | UI is branded "Toki"; product is Archie. |
| 24 | L | After the placeholder paste the UI did not refresh until manual reload. |

## Prioritised launch backlog

Ordered by "unblocks a first real user reaching a draft cooling number", then
by effort. Each item is small and independently testable.

1. **Connect reviewer traces to the area gate.** `_room_area_coverage` should
   accept current, calibrated `room_geometry_proofs` (and reviewed
   `hourly_load_model` rooms) as validated areas. Test: trace-only project
   passes the gate; uncalibrated/stale trace does not.
2. **Fix the two crashing endpoints** (#6 import, #7 `safe_link`). Add API
   tests that GET each resolution endpoint on a fresh project.
3. **Guided resolver must not abort on `stale`.** Treat stale skill output as
   "continue with available evidence" (the status text already promises this)
   or re-run only stale tasks.
4. **Create provisional schedule library and design-day weather without the
   full resolver**, clearly labelled preliminary, or let the preliminary
   calculation hydrate them itself. Design weather needs a site-location step
   that accepts a typed suburb/state when the PDF has no address.
5. **Single-level default floor.** When no level is stated, propose one
   provisional floor candidate in the draft instead of blocking every zone.
6. **Remove the ChatGPT-paste gate on the reviewed workspace** (#4); show the
   workspace with "no AI evidence yet".
7. **Draft review ergonomics:** one reviewer field for the whole review
   session, bulk accept per group, persist/re-display saved decisions, render
   the preview summary next to the buttons, explain "0 records created".
8. **Trace ergonomics:** keep calibration per page across rooms, warn before
   clearing an unsaved trace, keep focus on the plan, refresh room status
   after save.
9. **Use RCP ceiling heights and lighting legend** as cited provisional values
   (fits the "AI infers values from the PDF" goal).
10. Polish: toast HTML, narrow-width card, header counts, branding.

## Not done / follow-ups

- No hourly cooling report was produced, so the report UI, the
  `artifact_snapshot` crash in context, and the "Set up Archie AI estimate"
  fast path with a real provider were not exercised end to end.
- The ChatGPT-vision route (paste a real reply with AI-estimated areas) was not
  tried, to avoid spending the user's ChatGPT allowance.
- Walkthrough project and upload are still on disk; `output/web_projects.json`
  has not been restored.
