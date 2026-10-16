# Codex task: build the approved job workspace design

## Read first

1. `AGENTS.md` — follow it (scope checklist, tests, honest completion report).
2. `docs/PRODUCT_PHASES.md` — what Phase 1 and Phase 2 are, how room areas are found, what is never asked of a person.
3. `.claude/skills/frontend-design/SKILL.md` — the design discipline to work by (restraint, no templated defaults,
   plain copy, critique from screenshots).
4. `docs/design/workspace-mockup/` — **the approved design**: `1-drawings.dc.html`, `2-rooms-phase1.dc.html`,
   `4-results.dc.html` and the README. Open each file and read its markup: every size, colour, spacing and line of
   copy is inline. **Where this document and the mockup disagree about how something looks, the mockup wins. Where
   they disagree about behaviour, this document wins.**

## Background: what went wrong last time

A previous attempt (`frontend/js/guided.js`, `frontend/css/guided.css`, `frontend/tests/guided.spec.mjs`, the
`?guided` branch in `frontend/js/bootstrap.js`) was rejected by the user for its look, and the normal site was put back
to the old workspace. That attempt had a structure but no deliberate visual design. This time the design is fixed in
advance by the mockup: build exactly that, then extend the same system to the screens the mockup doesn't show.

- Do **not** reuse the rejected visual layer. Build the new screens in new modules and delete `guided.js`,
  `guided.css` and `guided.spec.mjs` once the new screens pass their tests.
- The rejected attempt also left uncommitted **backend** additions that are still useful: `stage`, `phase1_complete`,
  `phase2_complete`, `results_ready`, `needs_you` in `backend/job_service.py` `status()`; `phase2_room_inputs` in
  `backend/ai_preliminary_service.py` (workspace view); per-job stage in the projects list (`backend/web_app.py`); their
  tests (`tests/test_job_service.py`, `tests/test_ai_preliminary_workspace_view.py`, `tests/test_project_list.py`).
  Review them, fix them where this document asks (e.g. `needs_you.phase1`, below), keep them tested.
- **The old workspace stays the default** until the user approves the new one. Serve the new design behind
  `?preview=1` (replace the `?guided` switch in `bootstrap.js`). Switching the default is a separate, final step the
  user asks for.

## Design system (exact values; all from the mockup)

**Colour tokens** — define once as CSS custom properties, use only these:

| Token | Value | Use |
|---|---|---|
| `--page` | `#F2F4F4` | page background (cool tracing-paper grey; not cream) |
| `--sheet` | `#FFFFFF` | header, panels, the plan |
| `--ink` | `#163B50` | text, line work, done-step circles, table totals rule |
| `--ink-2` | `#4B616D` | secondary text, table headers, captions |
| `--ink-3` | `#6B7F89` | not-yet and locked steps, refrigeration outlines |
| `--line` | `#D3DCE1` | panel borders, header rule, table header rule |
| `--line-soft` | `#E3E9EC` | row separators inside panels |
| `--action` | `#246B91` | primary buttons, links, current step, selected room stroke |
| `--select` | `#DCE9F1` | selected room fill on the plan |
| `--row-select` | `#EEF5F9` | selected table row |
| `--hatch` | `#9AAAB3` | refrigeration hatching, waiting icons, not-yet-traced outlines |
| `--needs` / `--needs-ink` | `#FBEFD3` / `#5C3D00` | "needs you" only (the only warm colour) |
| load scale | `#E3EEF4`, `#C9DDE9`, `#A9C9DC`, `#86B3CE`, `#6F9FBF` | Results plan shading by W/m², light → dark |
| load parts | fresh air `#163B50`, people `#246B91`, lights `#6FA3C2`, equipment `#B9D3E2`, walls/roof/windows: white with a 1 px `--ink` outline | Results bars and legend |

No other colours, no gradients, no shadows.

**Type** — Alte Haas Grotesk only (`frontend/assets/fonts/`, `@font-face` 400 and 700; fallback Helvetica,
sans-serif), `font-variant-numeric: tabular-nums` everywhere. Six sizes only:

| Size | Use |
|---|---|
| 13 px | captions, table headers, step sub-labels, sources |
| 15 px | body, table cells, links, buttons |
| 18 px | the status sentence, panel sub-headings (h3), job name |
| 24 px | panel headings (h1/h2 in panels), key facts in a room |
| 36 px | the open room's name |
| 72 px | the Results total only |

Weights 400 and 700 only. Body line-height 1.45, large headings 1.1. Sentence case everywhere.

**Shapes** — panels: `--sheet`, 1 px `--line` border, radius 4 px, padding 24 px, no shadow. Buttons: radius 6 px,
min-height 44 px, padding 12 px 20 px (primary) / 10 px 16 px (secondary). Primary: `--action` fill, white bold text.
Secondary: white, 1 px `--action` border, `--action` text. Links: `--action`, hover `--ink`. Focus: 2 px `--action`
outline, 2 px offset, on every control.

**Where a value came from** — drafting line styles, never colour:
- solid = measured from the drawing (printed area, printed dimensions, an outline scaled by a printed dimension);
- dashed (`6 4` on lines, `text-decoration: underline dashed` on values) = assumed (a labelled default, a most-likely
  case, an outline scaled by the sheet's printed scale alone);
- dotted (`1 5`, round caps) = AI estimate (measured off the plan, tenancy remainder) and, on the Drawings screen,
  rooms not traced yet.
Every assumed or estimated value also carries a tooltip with its reason (the text the backend already gives), and the
legend under the plan explains the three styles.

**Never use** (the skill's templated tells, all present in the old site): all-caps labels or eyebrows above headings;
meta strings joined with middle dots ("A · B · C"); "→" on buttons or links; identical rounded cards with soft
shadows; left-border accent stripes; gradient washes; emoji; colour as the only signal.

## The job frame (every job screen; see any mockup file)

- **Header** (`--sheet`, bottom border `--line`; inner max-width 1360 px, padding 14 px 40 px, flex-wrap, gap 24 px):
  wordmark (22 px tall) · "Jobs" link · job name (18 px bold) over the site line (13 px `--ink-2`) · "Job details"
  link on the right.
- **Stepper** (inside the header, same max-width and side padding; four equal steps, each `flex: 1 1 220px`, padding
  14 px 16 px 16 px 0): a number circle, the step name (bold) and a sub-label (13 px).
  - Done: 26 px circle filled `--ink`, white number. Link.
  - Current: 26 px circle filled `--action`, 3 px `--action` bottom border, `aria-current="step"`. Link.
  - Available, not current: 24 px circle, 1 px `--ink` outline. Link.
  - Not yet: text `--ink-3`, 24 px circle 1 px `--ink-3` outline, not a link, `title` saying when it starts.
  - Locked (Results before Phase 2 is complete): as Not yet with a dashed circle and the sub-label "After Phase 2".
  Steps: 1 Drawings, 2 Rooms ("Phase 1, empty-room load"), 3 Operating loads ("Phase 2"), 4 Results. The stepper is
  the **only** navigation inside a job.
- **Main**: max-width 1360 px, padding 28 px 40 px 48 px, column gap 24 px.
- **Status sentence** at the top of every step: 18 px, max 62 characters wide, at most one primary button beside it
  (e.g. "Continue to operating loads"). Derive it from `/api/guide`, `/api/job-status` and the job statuses. It replaces
  the old "Next step" card.
- **Needs you** (not in the mockup because Butcher Buffet had none): directly under the status sentence, only when
  something blocks this step. A panel filled `--needs`, text `--needs-ink`, no border stripe; heading "2 things need
  you" (18 px); a list where each item says what is missing and why in one sentence, with one inline input or button.
  Optional checks never go here; they go in the room's "What the AI assumed" list.
- Responsive: below 768 px the header and stepper wrap (the mockup's flex-wrap already does this), panels stack,
  tables scroll inside their panel, no page-level horizontal scroll, 16 px side gutter.
- Remove from the job view: the left rail of tabs, its total kW and Calculate box, "Detailed tools", the sub-tab row,
  the "Next step" card, all "Next: X →" buttons, the theme toggle (the new design is light only for now).

## Screens

### 1 · Drawings (`1-drawings.dc.html`)

Left panel "What the AI is doing": the five stages as a list, separated by `--line-soft` rules, each with a 22 px icon
(done: filled `--ink` circle with a white tick; in progress: `--line` ring with a `--action` arc that rotates, static
under `prefers-reduced-motion`; waiting: 1.5 px `--hatch` outline, row text `--ink-3`), the stage name (bold) and one
plain line of progress. Stages and sources: read every page (`/api/page-reading`), find the equipment
(`/api/page-extraction?kind=equipment_appliances`), trace the rooms and walls (`/api/drawing-tasks`, with the rooms
traced so far), work out walls, roof, floor and windows (`/api/skill-workflow`), calculate each room's empty-room load
(`/api/job-calculation`). A stopped stage shows its reason and a "Try again" secondary button using the existing retry
action for that job.

Right panel "Found so far": the plan drawing (traced rooms solid, rooms named but not traced yet dotted), then a
two-column list: site, weather (with how it was picked), rooms named on the drawings, what is above. Status sentence:
what the AI is doing and roughly how long is left, and that the page can be closed.

### 2 · Rooms, Phase 1 (`2-rooms-phase1.dc.html`)

Left panel (`flex: 1 1 560px`): floor heading ("Ground floor"; the level name) with its source on the right; the
plan drawing; the line-style legend; the "Rooms on this floor" table (room, area m², empty-room load kW, Phase 1
state; the open room's row highlighted `--row-select`; refrigeration rooms in `--ink-2` with "Not in this load" and
"Refrigeration"). Rooms with an area but no outline are listed in the table with their area source and a note "No
outline; walls not measured".

Right panel (`flex: 1 1 440px`), the open room:
- name (36 px bold) and use (`--ink-2`);
- three facts (13 px label over 24 px bold value): floor area, ceiling height, empty-room load — assumed values with
  the dashed underline;
- one 13 px paragraph saying where the area and height came from (from the area source and calibration label the
  backend gives: printed area, outline scaled by the printed dimension, sheet scale, printed dimensions, AI estimate,
  tenancy remainder, typed by you);
- "What the load is worked out from": the Phase 1 surfaces (`phase1_surface_audit` for this room) **grouped** by what
  they face — "9 walls to neighbouring tenancies", "9 walls to the Kitchen and Bar", "Ceiling under the floor above",
  "Floor slab", "Shopfront glazing" — with total m², U, "Beyond it" (temperature and what it is) and kW; a link "See
  each wall on the plan" that highlights the room's walls on the plan coloured by what they face and lists them one
  by one (this replaces the old Walls & roof tab);
- "What the AI assumed": the room's `phase1_assumptions`, one per line in plain words, each with a "Change" link
  opening the matching existing editor;
- secondary buttons: "Correct the area" (`/api/room-area-override`), "Change a wall or the roof" (the existing walls
  and roof editor for this room). Editors open in a panel that replaces the open-room panel content (with "Back to the
  room"), styled with this design system, reusing the existing editor logic and endpoints unchanged.
- After any edit, Phase 1 recalculates automatically (`/api/job-calculation`, `phase: "phase1_base_room"`) and only
  the affected room shows "Updating".

Status sentence examples: "Phase 1 is done for the three cooled rooms. …" with "Continue to operating loads"; "The AI
is finding the area of 2 rooms." (no button); "1 thing needs you before Phase 1 is complete."

**Never ask the person to draw or trace a room.** While an area step is still running
(`subtabs.rooms.measurements.state == "working"`) a room without an area shows "Finding its area", not a request.
Only when every step finished without one (`"needed"`) does Needs you show "Type the area of <room>" with one number
input. Drawing stays an optional link inside the open-room panel. Make `needs_you.phase1` follow the same rule (use
`job_service._areas_still_being_found`).

### 3 · Operating loads, Phase 2 (not in the mockup: build it from the Rooms pattern)

Same layout as Rooms: plan and rooms table on the left (table columns: room, area, Phase 1 kW, Phase 2 adds kW, state),
the open room on the right.

Open room: name and use; three facts — empty-room load (Phase 1), operating loads, room total (kW); then one short table
per section, each row with value, where it came from (line style + tooltip) and a Change link:
- **People**: count and how it was counted (seats, room type and size), W per person;
- **Lights**: W/m² or W, lamp type;
- **Equipment**: one row per item in this room (name, rating or typical value, data sheet / drawing / typical value),
  with the existing equipment review actions (accept, correct, reject, data-sheet rating) moved here from the old
  Drawings → Equipment sub-tab;
- **Fresh air and infiltration**: L/s and its basis.
Data: `phase2_room_inputs` from the `/api/ai-preliminary-model` workspace view, the equipment list from
`/api/page-extraction`, and the full report's per-room components.

Typical values never block: they are listed under "What's typical or assumed" for the room. Phase-2 questions from the
"What we need to find" list (`/api/skill-workflow` `information_needs`, answered with `answer_need`) go in Needs you
only when they block the final calculation.

The final calculation runs automatically when Phase 1 is complete and nothing blocks Phase 2 (`/api/job-calculation`,
`phase: "full"`, `include` = every room included in Phase 1; reuse the existing progress watcher). After an edit:
"Inputs changed. Update the final load" as the status sentence's button. Then the Results step unlocks.

### 4 · Results (`4-results.dc.html`)

Locked until the full report exists and is current (`results_ready` and not `result_stale`); before that the step is
not a link and nothing renders. Then:
- the total (72 px bold, "kW") under "Design cooling load for the tenancy"; one sentence: rooms counted, design
  conditions, the safety allowance in kW, what is sized separately; "Download the report" (existing export);
- left panel "Load per square metre": the plan shaded with the five-step load scale by W/m², labels with W/m², the
  caption "Darker rooms need more cooling for their size.";
- right panel "Each room": legend of load parts; table room · m² · "Where the load comes from" (a horizontal stacked
  bar, its width scaled to the largest room, parts in the load-part colours, an `aria-label` listing each part in kW)
  · kW (bold); a "Safety allowance, 10%" row; a total row with a 1 px `--ink` rule above;
- "Still assumed" from `/api/result-status`, each with a link to where it is fixed.
If inputs change after a result exists, the numbers turn `--ink-2` with "Out of date. Update the final load".

### Jobs and New job (not in the mockup)

Same header without job name and stepper. **Jobs**: heading "Jobs", primary "New job"; one table: job, site, where it
is (Drawings / Rooms / Operating loads / Results ready), needs you (count on a `--needs` chip, blank when 0), updated.
Empty state: "No jobs yet. Upload the architect's PDF to start." **New job**: one panel with a drop zone (1.5 px dashed
`--hatch` border, radius 4 px): "Upload the architect's PDF", "The whole drawing set in one PDF. The AI reads every
page.", a "Choose PDF" button; then file name, size and progress, then straight to the job's Drawings step. Keep the
existing automatic chain (`/api/analyse` when a new job opens, `preparePages()` posting `/api/prepare-pages`); the
person never presses "Analyse PDF". Errors (not a PDF, too large, upload failed) say what happened and keep the drop zone.

### Job details

From "Job details" in the header: a panel replacing the main content (with "Back to the job"): job name, site address
(prefilled with the AI's site text), building type, what is above the tenancy, weather location (select; "Picked by the
AI: Sydney (Melrose Park NSW)" when `weather_ai_pick` is set). Saved with `/api/job-setup`.

## The plan drawing (shared by Drawings, Rooms, Operating loads, Results)

Backend: add read-only `GET /api/job-plan?project_id=` (no calculation, unit-tested) returning, per plan page with
room outlines: `page`, `level_name`, a `view_box` (the outlines' bounds plus 4 %), and `rooms`: `room_id`, `label`,
`points` (page pixels, from `reviewer_room_geometry_service.current_records`), `label_point` (shapely
`representative_point`, so the label is always inside the room), `area_m2`, `area_source`, `line_style`
(`solid` / `dashed` / `dotted` per the rules above; `dashed` when the calibration status is `declared_scale_only`),
`kind` (`comfort` / `refrigeration` / `not_a_room`), `state` (`done` / `needs_you` / `finding` / `left_out`),
`phase1_kw`, `final_kw`, `w_per_m2` (when the full report is current), plus `rooms_without_outline` (label, area,
area source) and rooms named but not traced yet (for the Drawings screen).

Front end: one inline SVG per page (`role="img"` with an `aria-label` naming the rooms), `viewBox` from the response,
scaling to the panel width (max height ~620 px):
- rooms: fill `--sheet`, stroke `--ink` 1.5 in the room's line style; the open room fill `--select`, stroke `--action`
  3; refrigeration rooms hatched (45°, `--hatch`, 2 px every 8) with a `--ink-3` outline; not-yet-traced rooms dotted
  `--hatch`, no fill; Results: fill from the load scale;
- labels at `label_point`: name bold (size by room size, 16–20 px; 10 px for very small rooms) and a second line
  (area, or kW, or W/m² by screen);
- each room is clickable and opens it; the rooms table under the plan is the keyboard and screen-reader path (the
  polygons are not tab stops);
- several plan pages or levels: one drawing per level with its heading.

## Behaviour that must keep working

- All existing API actions: retries, `answer_need`, area/height overrides, envelope classification, equipment review,
  job-calculation, the automatic Phase 1 start, stuck-run detection.
- Don't redraw under someone typing; only the latest redraw paints (both guards exist in `workspace.js`: carry them
  over).
- Old routes (`#/job/<id>/project`, `/walls`, `/windows`, `/drawings/<sub>`, `/rooms/<sub>`) redirect to the nearest
  new screen.
- `?engineer=1` (the old detailed tools) and `?operator=1` (manual AI task review) still work, reachable only from a
  "⋯" menu in the header that is rendered only in those modes.

## Copy

Use: room, wall, roof, floor, window, drawings, page, AI, empty-room load (Phase 1), operating loads (Phase 2), final
load, typical value, assumed, needs you. Never show: skill(s), finding(s), subskill, P0–P6, provider, model names,
tokens, fingerprint, scope, manifest, run id, JSON, "reviewer trace", "below accuracy bar". One idea per sentence;
say what to do, not how the system works; errors say what happened and how to fix it. Buttons name exactly what
happens ("Continue to operating loads", "Correct the area", "Download the report"). AI cost and usage only in
engineer mode.

## Files

New modules under `frontend/js/job/` (e.g. `frame.js`, `plan.js`, `drawings.js`, `rooms.js`, `operating.js`,
`results.js`, `jobs.js`, `editors.js`) and one stylesheet `frontend/css/job.css` holding the tokens and components.
Load them from `frontend/index.html` via `bootstrap.js` under `?preview=1`. Reuse logic from `frontend/js/workspace.js`
by moving functions into the new modules, not by copying them. Backend: `backend/web_app.py` (the `/api/job-plan`
route), a small `backend/job_plan_service.py`, and the reviewed additions listed above.

## Process (per milestone)

1. Build the milestone.
2. Run the app on a saved job and screenshot each screen at 1440 px and 375 px wide **next to the matching mockup
   file**; list every visible difference and fix it. Screens not in the mockup are judged against the design system
   above and the skill's checklist (restraint, the "never use" list, plain copy).
3. Check keyboard-only use (Tab through the stepper, tables, buttons; visible focus) and `prefers-reduced-motion`.
4. Run `python3 -m unittest discover -s tests` and `cd frontend && npm test`; both must pass. Don't stop or reuse
   another process's test server on port 4173.

## Tests

- Playwright (mocked APIs, as in `frontend/tests/workspace.spec.mjs`), in a new `frontend/tests/job.spec.mjs`: the
  frame and stepper states (done, current, available, not yet, locked); status sentences and Needs you; Drawings stages
  (working, stopped with retry, done); the plan (selected room, line styles, refrigeration hatch, label inside, rooms
  table opens a room, rooms without outline listed); Rooms open-room panel (facts, grouped surfaces, assumptions,
  each editor opens and saves, automatic recalculation); "Finding its area" vs "Type the area" (never "draw");
  Operating loads sections, equipment review, automatic final calculation, "Inputs changed"; Results locked before
  Phase 2 and correct after (total, bars proportional, allowance, total row), stale results greyed; Jobs list and New
  job states; old routes redirect; engineer/operator modes; 375 px without horizontal scroll; keyboard path.
- Backend unit tests for `/api/job-plan` (label point inside a concave room, line style by calibration status, kinds,
  states, rooms without outline) and for every reviewed addition.

## Milestones

1. Design tokens, `job.css`, the job frame (header, stepper, status sentence, Needs you), `?preview=1`, route
   redirects; `/api/job-plan` and the plan drawing.
2. Rooms (Phase 1) screen to match `2-rooms-phase1.dc.html`, with editors and automatic recalculation.
3. Drawings screen to match `1-drawings.dc.html`.
4. Results screen to match `4-results.dc.html`, locked until Phase 2.
5. Operating loads (Phase 2) screen, equipment review, automatic final calculation.
6. Jobs and New job; Job details.
7. Remove the rejected `guided.*` files; full screenshot pass at 1440 and 375 px; fix differences.

Stop after milestone 7 and report; switching the default to the new design is the user's call.

## Done means

- [ ] The Drawings, Rooms and Results screens match their mockup files at 1440 px (layout, sizes, colours, copy
      pattern), checked from screenshots.
- [ ] Inside a job the stepper is the only navigation; no total kW, Calculate or Results numbers before Phase 2 is
      complete.
- [ ] Every value shows where it came from by line style and tooltip; nothing uses colour alone.
- [ ] Nobody is asked to draw a room; areas still being found say so.
- [ ] None of the "never use" patterns appear; no internal terms in the normal flow.
- [ ] Every waiting, stopped, empty and error state says what is happening and offers one action.
- [ ] Old links redirect; existing API actions work; both test suites pass; 375 px works without horizontal scroll;
      keyboard-only path works.
- [ ] The old workspace is still the default; the new one is behind `?preview=1`.
- [ ] Completion report per `AGENTS.md`, listing anything not done and why.
