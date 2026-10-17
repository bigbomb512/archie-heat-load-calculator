# Codex task: job workspace preview, round 2 fixes

You built the approved design behind `?preview=1` (`frontend/js/job/*`, `frontend/css/job.css`). It was reviewed on
the real Butcher Buffet jobs on 2026-10-16. The look is close to the mockup, but the screens show room data that is
wrong or contradictory. Some screens are slow and the code is hard to maintain. This task fixes all of that.
Everything stays behind `?preview=1`; the old workspace stays the default.

## Read first

- `AGENTS.md` (task completion rules), `docs/BROWSER_TEST_LOOP.md`, and `docs/CODEX_FRONTEND_REDESIGN.md` (the
  original build plan; still in force except where this file changes it).
- The mockup files in `docs/design/workspace-mockup/` (`README.md` first).
- `.claude/skills/frontend-design/SKILL.md`.
- The backend changes already made for you (below). **Don't change `backend/job_plan_service.py`,
  `backend/job_service.py`, `backend/ai_preliminary_service.py` or `backend/reviewer_room_geometry_service.py`.** If
  the screens need data these don't give, stop and say exactly what is missing.

## Already done in the backend (use it; don't redo it)

### `GET /api/job-plan` (the contract)

Response: `status` (the same object as `/api/job-status`), `rooms`, `outlines`, `pages`, `rooms_without_outline`,
`area_basis_labels`, `phase1_current`, `phase1_report`, `final_report`.

Each **room**:
- `room_id`, `name`;
- `level`: `""` when the drawings don't name a level. Show no level heading then; never show "Unassigned level";
- `area_m2`;
- `area_basis` and `area_label` (see the table below);
- `phase1_kw`: only from a current, unblocked Phase 1 result that included this room, otherwise `null`;
- `w_m2`: Phase 1 W/m²;
- `final_kw` and `final_w_m2`: only from a current full result, otherwise `null`;
- `kind`: `comfort` / `refrigeration` / `not_a_room`, from the AI's room-use scope, never from words in the name;
- `included`, `state`, `source_pages`, `has_outline`.

`state` is one of:

| state | Meaning | Rooms table shows |
|---|---|---|
| `done` | Phase 1 load is current | the kW, "Calculated" |
| `finding_surfaces` | area known; walls, roof and floor still being worked out | "Working out walls and roof" |
| `finding_area` | an AI area step is still running | "Finding its area" |
| `needs_area` | every area step finished without an area: the one thing the operators type | "Type the area" (Needs you) |
| `needs_use` | the AI couldn't tell what the room is used for | "Say what the room is used for" (Needs you) |
| `sized_separately` | refrigeration (coolroom, freezer) | "Not in this load · Refrigeration" |
| `left_out` | not a room, or left out by the operators | "Left out" |

`area_basis` → `area_label` → plan line style:

| `area_basis` | `area_label` | Line |
|---|---|---|
| `typed` | Typed by you | solid |
| `printed` | Printed on the drawing | solid |
| `measured` | Measured from a printed dimension | solid |
| `sheet_scale` | Measured at the sheet's printed scale only | dashed |
| `printed_dimensions` | Worked out from printed dimensions | dashed |
| `ai_outline` | Measured from the AI's room outline | dashed |
| `remainder` | What is left of the tenancy area | dotted |
| `estimate` | AI estimate from the plan | dotted |

Each **outline**: `room_id`, `name`, `level`, `page`, `points` (page pixels), `label_point`, `line_style`
(`solid` / `dashed` / `dotted`, already worked out), `area_basis`, `kind`, `state`, `source`.

Fields that were removed or renamed: `area_source` → `area_basis` / `area_label`; `rooms_named_not_traced` is gone (use
`rooms_without_outline`); there are no `named_not_traced` outlines with empty points any more.

### Other backend changes

- `status.needs_you.phase2` now counts only the open questions on the "What we need to find" list: the same items
  Operating loads asks (not answered, accepted or rejected). It is no longer the AI's internal low-confidence list,
  which showed 49–70 "needs you" on every job.
- `/api/projects` rows have `display_name` (job name typed in Job details, else the PDF name without ".pdf").
  `/api/job-status` has `name` (same rule).
- Speed: job-status went from ~2 s to ~0.1 s and job-plan from ~4.4 s to ~0.5 s, once warm. The first call after a
  job file changes is still a few seconds, so don't fetch the same endpoint twice for one screen.

### Real fixtures

`frontend/tests/fixtures/job-plan-bb-phase1-done.json`: Phase 1 done. Five rooms, including the Coolroom and Freezer
sized separately; every comfort room's Phase 1 load is 0.0 kW (all walls face the mall or neighbours, slab above).

`frontend/tests/fixtures/job-plan-bb-bar-without-area.json`: the Bar has no area or outline (`needs_area`); the
Kitchen and Shop are done.

Use these in the Playwright tests instead of hand-made plans.

## Fixes, by screen

### Every screen

1. **Header job name**: `status.name` (Job details name, else the PDF name without ".pdf"), never the raw filename.
2. **Stepper**: Drawings done when `status.stage` is not `drawings`; Rooms done when `status.phase1_complete`;
   Operating loads done when `status.phase2_complete`. Being on Results doesn't change the others.
3. **One request per endpoint per screen render.** Job-plan is fetched twice on one load today, and every screen
   re-fetches job-status next to job-plan, which already contains it. Find and remove the duplicate render or fetch.
4. **Loads**: always one decimal place ("0.0 kW", "12.4 kW"); areas one decimal ("259.6 m²"). Don't show "0" alone.
5. **Copy**: remove "needs a supported area source", "Surface findings", "Unassigned level", "Room areas still need a
   reliable source", "Status is not available yet", and any other internal wording (see Copy in the original plan).
   Each sentence says what is happening or what to do.
6. **Code style**: the new modules are packed into very long one-line functions. Reformat every file in
   `frontend/js/job/` and `job.css` to read like `frontend/js/workspace.js`: one statement per line, lines under about
   120 characters, named helper functions for repeated markup. No behaviour change from reformatting; do it first, as
   its own step, and run the tests before moving on.

### The plan drawing (`plan.js`)

7. **Don't draw the scanned page image under the outlines.** The mockup shows only the room outlines on white; with
   the page image underneath, the outlines can barely be seen. Remove the `<image>` underlay. An optional "Show the
   drawing" toggle (off by default, remembered per viewer in `localStorage` with try/catch) is allowed.
8. Line style comes from `outline.line_style` as given. Refrigeration rooms are hatched (see the original plan).
   Rooms in `rooms_without_outline` appear in the table only, with their `area_label` and "No outline".
9. Results shading uses `room.final_w_m2` (it read a field `w_per_m2` that never existed, so shading never worked).
   Labels: the area on Drawings and Rooms; W/m² on Results.

### Drawings (`drawings.js`)

10. **Stage states must match the data:**
    - "Read every page": `page-reading` fields `pages_read` / `page_count` and `status`.
    - "Trace the rooms and walls": done when `drawing-tasks.status == "done"`. Its line names the rooms with an
      outline (`plan.rooms` with `has_outline`) and those still being found (`state == "finding_area"`).
    - "Work out walls, roof, floor and windows": from `skill-workflow.status`. When it is `failed`, say "Working
      out the walls, roof and floor stopped part-way. What it found so far is kept." with "Try again". Don't show the
      response's `remediation` text: it is written for engineers ("evidence tasks", "exceptions").
    - "Calculate each room's empty-room load": done **only when `status.phase1_complete`**. A finished calculation
      job with a blocked or partial result is not done. Show `plan.phase1_report.blocked_reasons[0]` when blocked, or
      the rooms still left out (`phase1_report.phase1_coverage.rooms_left_out`).
11. The status sentence follows the first stage that isn't done: say what it is doing, or why it stopped and that
    "Try again" restarts it.

### Rooms (`rooms.js`)

12. Use `room.state` and the copy table above for the rooms table and the open room. Remove the fallback that reads
    loads straight from `phase1_report.room_peaks` (`room.phase1_kw ?? peaks?.design_total_kw`); it shows old or
    blocked 0.0 kW results as if they were current. Use `room.phase1_kw` only.
13. **Needs you** lists exactly the rooms whose state is `needs_area` or `needs_use`, one line each ("Type the area of
    the Bar" with a number input; "Say what the Bar is used for"). The count in the heading equals the number of
    lines. Rooms that are `finding_area` are not in Needs you.
14. The open room's area paragraph uses `area_label`. Assumed values (dashed or dotted basis) get the dashed underline.
15. Level heading: `room.level` when it isn't empty; otherwise no heading (or "Floor plan").

### Operating loads (`operating.js`)

16. **The automatic final calculation must not start twice or redo a current result.** First read
    `/api/job-calculation`. Start `phase: "full"` only when no job is `queued`/`running`, `status.phase1_complete`,
    `status.needs_you.phase2 == 0`, and there is no current result (`!status.results_ready || status.result_stale`).
    Keep the in-tab guard you have.
17. The questions list shows only `information_needs` findings with `field == "needs"` that are not accepted,
    rejected or answered (the answers are in the skill-workflow response's `answers`, keyed by finding id). Its count
    must equal `status.needs_you.phase2`.
18. Room rows: Phase 2 adds = `final_kw - phase1_kw` only when both are numbers; otherwise "Not calculated yet".

### Results (`results.js`)

19. Room areas and kW come from `plan.rooms` (`area_m2`, `final_kw`), matched by room name to the report's room peaks
    for the component bars. Today the m² column says "Not found yet" for traced rooms, and the load plan says there
    are no outlines when there are.
20. An out-of-date result keeps the banner and greys the numbers, as now.

### Jobs (`jobs.js`)

21. Show `display_name`, with the site underneath in `--ink-2`, and the upload date. Many jobs share one PDF name, so
    they must be told apart.
22. Needs you: the sum of `needs_you`, on the chip, blank when 0. The numbers are now real.

## Tests (must fail before your fix, pass after)

Add to `frontend/tests/job-preview.spec.mjs` (mocked APIs, real fixtures above):
- the plan has no `<image>`; outlines use the given line styles; refrigeration rooms hatched; the Bar (no outline) is
  in the table with "No outline";
- the Rooms table states and copy for every `state` value. Needs you count equals its lines; `finding_area` is not in
  Needs you;
- no "Unassigned level", "finding", "skill", "supported area source" text anywhere on any screen (one test that walks
  all screens with both fixtures);
- loads with one decimal (`0.0`);
- the Drawings "Calculate" stage is not ticked when `phase1_complete` is false, even if `/api/job-calculation` says
  `done`; the failed envelope stage shows the plain sentence and "Try again";
- Operating loads does not POST `/api/job-calculation` when a job is running or the result is current;
- Results m² and kW come from `plan.rooms`; shading uses `final_w_m2`;
- the Jobs list shows `display_name` and site;
- each screen requests `/api/job-plan` once per render.

Run `python3 -m unittest discover -s tests` and `cd frontend && npm test`; both must pass. Don't stop or reuse another
process's server on port 4173.

## Browser check

Follow `docs/BROWSER_TEST_LOOP.md` on the real saved jobs at 1440 px and 375 px, with screenshots next to the mockup
files:
- Butcher Buffet `20260226-butcher-buffet-_-melrose-park-construction-rev-b_2_-1792146872` (Phase 1 done);
- `…-1792058065` (envelope step failed, Bar without area);
- `walkthrough-butcher-buffet-_not-for-design_-1790926231` (Results, out of date).

**Don't open Operating loads on a real job unless the test above has passed.** It can start a calculation.

## Done means

- [ ] Every numbered fix above is done or reported as blocked, with the reason.
- [ ] No screen contradicts itself: the same room has the same state in the plan, table, open room and Needs you.
- [ ] The plan outlines are clearly visible, as in the mockup.
- [ ] Both test suites pass; the new tests above exist and use the real fixtures.
- [ ] Code in `frontend/js/job/` is readable (no long one-line functions).
- [ ] Old workspace still the default; completion report per `AGENTS.md`.
