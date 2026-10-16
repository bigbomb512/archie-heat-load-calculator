# Codex task: redesign the job website into one clear, guided journey

Read `AGENTS.md` first and follow it (scope checklist, tests, honest completion report). Also read
`docs/PRODUCT_PHASES.md` (what Phase 1 and Phase 2 are) before changing anything.

## Why

Toki turns an architect's PDF into a room-by-room cooling load. The AI now does most of the work: it reads every page,
traces the rooms, works out walls, roof, floor and windows, and calculates each room's **Phase 1 empty-room load**.
**Phase 2** then adds people, lights, equipment and fresh air, and only then is the **final result** produced.

The current website was built up feature by feature and is confusing:

- There are **four navigation systems at once**: the left rail of six tabs, sub-tabs under most tabs, the large
  "Next step" card, and "Next: X →" buttons. Users don't know which one to follow.
- **The Results tab and the rail's "— kW / Calculate" box are visible from the start**, although the final result
  only exists after Phase 2. People press Calculate too early or read a Phase 1 number as the answer.
- The work is organised **by component** (Walls & roof, Windows tabs) while the result is **per room**, so checking
  one room means visiting four tabs.
- Engineering and debugging tools ("Detailed tools", Pages & AI, Findings, manual P0–P6 fallback, AI usage) sit in the
  normal flow.
- Words like *skills*, *findings*, *P0*, *provider*, *fingerprint*, *scope* leak into the interface.

The goal: **someone who has never used Toki can take a job from PDF to final result without asking anything**, and
always knows (1) where the job is, (2) whether anything needs them, and (3) what the one next action is.

## Users and principles

The users are our own operators preparing loads for clients (not engineers). Design for them:

1. **One linear journey, one place to look.** A single stepper is the only navigation inside a job. No rail of tabs,
   no sub-tab rows, no separate "Next step" card, no "Next: X" buttons.
2. **The AI works, the person checks.** Default state: the AI is working or done; the person only sees what genuinely
   needs them. Optional checks are separate and clearly optional.
3. **Rooms are the unit.** Phase 1 and Phase 2 are both shown room by room. Everything about a room (area, height,
   walls, roof, floor, windows, equipment, people, lights, air) is in that room's card.
4. **Nothing final until it is final.** No total kW and no "Results" until Phase 2 has produced the final load.
   Phase 1 shows per-room *empty-room* loads labelled as such.
5. **Every number says where it came from**, with one consistent badge set (below).
6. **Plain words only.** Short sentences, no internal terms (see "Copy rules").
7. **Never a dead end.** Every waiting, stopped, empty or error state says what is happening and offers the one action
   that helps (wait / retry / fill this in).

## Information architecture

Routes (hash routing, as now):

| Route | Screen |
|---|---|
| `#/jobs` (default) | **Jobs** home: list of jobs + "New job" |
| `#/new` | **New job**: upload a PDF |
| `#/job/<id>/drawings` | Step 1 · **Drawings** (AI reading) |
| `#/job/<id>/rooms` (optional `/<room key>`) | Step 2 · **Phase 1 · Rooms** (empty-room load per room) |
| `#/job/<id>/operating` (optional `/<room key>`) | Step 3 · **Phase 2 · Operating loads** |
| `#/job/<id>/results` | Step 4 · **Results** (locked until Phase 2 is complete) |
| `#/job/<id>/details` | **Job details** drawer/page (name, address, weather location, building type, what is above) |

Old routes (`#/job/<id>/project`, `/walls`, `/windows`, `/drawings/<subtab>`, `/rooms/room-details`, …) must
redirect to the nearest new route so existing links keep working.

Engineering tools are **not** part of this IA. They stay reachable only with `?engineer=1` (and `?operator=1` for the
manual AI task review), behind an "Advanced tools" item in the job's `⋯` menu that is rendered only in those modes.

## The job frame (every job screen)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ← Jobs   Butcher Buffet · Melrose Central                 Job details   ⋯   │
│ ─────────────────────────────────────────────────────────────────────────── │
│ ① Drawings ✓ ── ② Rooms ● 2 need you ── ③ Operating loads ○ ── ④ Results 🔒 │
│ ─────────────────────────────────────────────────────────────────────────── │
│ [Status bar: one sentence + at most one button]                              │
│                                                                              │
│ [Step content]                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Header:** "← Jobs", the job name and site (from `/api/job-status`: `name`, `address` or `found_site`), a
  "Job details" link, and a `⋯` menu (Rename, Export, and Advanced tools only in engineer/operator mode).
- **Stepper** (the only navigation): four steps, each with a state icon **and** text (never colour alone):
  `Done ✓`, `In progress` (spinner), `Needs you (n)`, `Not yet`, `Locked 🔒`. A step can be opened when it is Done,
  In progress or Needs you. Not-yet steps are visible but not clickable, with a tooltip saying why
  ("Starts when the AI has read the drawings"). **Results stays locked until Phase 2 is complete.**
- **Status bar** (replaces the "Next step" card): one plain sentence and at most one primary button, derived from
  `/api/guide` (`now`, `steps`) and the job statuses. Examples:
  - "The AI is reading your drawings — page 14 of 38, about 6 minutes left. You can leave this page."
  - "2 rooms need you before Phase 1 is complete." [Show me]
  - "Phase 1 is done for all 5 rooms." [Continue to operating loads]
  - "The AI stopped while tracing the rooms: <reason>." [Try again]
  - "Your final load is ready." [See results]
- Mobile (< 768 px): stepper becomes a compact "Step 2 of 4 · Rooms · 2 need you" bar that opens a list; content is
  one column; the room list becomes an accordion. No horizontal page scroll; 16 px side gutter.

Remove from the job frame: the left tab rail (`#wsTabs`, `.ws-rail` tab list and mobile section select), the rail's
`wsTotal` / `wsCalculate` / `wsCalcNote` box, the "Detailed tools" button and its note, the sub-tab row
(`#wsSubtabs`), the "Next step" card (`#jobGuide`), and every `data-ws-go` "Next: X →" button.

## Screens

### Jobs (home)

- Title "Jobs", a primary "New job" button.
- One row per job (`/api/projects`, then `/api/job-status` per visible job, or add `stage` + `needs_you` to the list
  response — see "Backend additions"): name, site, a stage chip ("AI reading", "Phase 1 · 2 need you",
  "Phase 2", "Results ready"), last updated. Click opens the job at its current step.
- Empty state: "No jobs yet. Start by uploading the architect's PDF." [New job]
- The old sidebar project list and "Current project / New analysis" nav are replaced by this page.

### New job (upload)

- One drop zone: "Drop the architect's PDF here, or choose a file." Plain hints: "The whole drawing set in one PDF.
  The AI reads every page."
- After choosing: show file name, size, upload progress; then go straight to the job's Drawings step. Keep the
  existing automatic chain: the page runs `/api/analyse` when a new job opens, `workspace.js` `preparePages()` posts
  `/api/prepare-pages`, and the server then starts page reading, equipment, drawing tasks, skills and Phase 1 on its
  own. Move those client-side triggers into the new code unchanged; the person never presses "Analyse PDF" (remove
  that header button from the normal flow).
- Errors: not a PDF / too large / upload failed — say so and keep the drop zone.
- Remove from this screen: marketing copy ("Engineering comfort…"), the "workflow preview", the scope note, and the
  local test workspace panel (keep it only when test mode is enabled, as today).

### Step 1 · Drawings

What the person needs: confidence the AI is working, how long, and what it found.

- **While working:** a vertical timeline of the AI stages in plain words with live progress, from
  `/api/page-reading`, `/api/page-extraction?kind=equipment_appliances`, `/api/drawing-tasks`, `/api/skill-workflow`
  and `/api/job-calculation`:
  1. Reading every page (n of N)
  2. Finding the equipment
  3. Tracing the rooms and walls
  4. Working through the drawings
  5. Calculating the empty-room loads
  Each stage: done ✓ / working (with its step label) / waiting / stopped (reason + "Try again", using the existing
  retry actions: page-reading `retry`, page-extraction `retry`, drawing-tasks `retry`, skill-workflow `retry`).
  Show a rough time remaining where the job reports progress; otherwise "usually 20–30 minutes".
- **When done:** a short summary: pages read, rooms found (names), equipment items found, site and weather location
  the AI picked ("Sydney — picked from 'Melrose Central'"), and the primary button "Continue to Phase 1 · Rooms".
- Poll every 3–5 s only while something is running; never redraw under someone typing (keep the existing guard).
- Remove from the default view: the page grid, consent checkboxes, AI usage/cost details, "Findings", "Pages & AI",
  "To find", operator/manual fallback. Pages & AI usage move to Advanced tools. The "To find" questions move into the
  room cards and the Needs-you list of the step they belong to (below). Equipment review moves to Phase 2.

### Step 2 · Phase 1 · Rooms (empty-room load)

Layout: room list on the left (desktop) / accordion (mobile); the selected room's card on the right.

**Room list:** one row per room in scope: name, area, Phase 1 kW, and a state: `✓ Done`, `Needs you`, `Left out`
(no area yet), `Refrigeration — not in this load` (cool rooms, freezers: listed, greyed, with that reason). Top of the
list: "Phase 1 · empty-room load" and the note "Heat through walls, roof, floor and windows only, doors and windows
closed. People, lights, equipment and air come in Phase 2." **No job total on this step.**

**Needs you (top of the step, only if non-empty):** a short numbered list of the items that block Phase 1, each with
the room, one sentence why, and one input or button inline. Sources: rooms left out (`phase1_coverage.rooms_left_out`:
"No area yet — draw the room or type its area"), Phase 1 blocked reasons, and the Phase-1 questions from the
"What we need to find" list (`/api/skill-workflow` findings with `subskill_id: information_needs`, answered with the
existing `answer_need` action) whose kind is room area, ceiling height, construction, glazing, unconditioned
temperature or above/roof. Under it, collapsed: "Optional checks (n)" for labelled assumptions the person may confirm.

**Room card (Phase 1):**

- Header: room name · use · area with source badge · ceiling height with source badge · **Phase 1 load in kW**.
- "Surfaces" table from `phase1_base_room_load_report.phase1_surface_audit` (already per room, sorted, with
  `label`, `area_m2`, `u_value_w_m2k`, `boundary_method`, `boundary_temperature_c`, `total_kw`, `source`):
  columns Surface · Area (m²) · U (W/m²K) · Beyond · kW. "Beyond" shows "outside" for outside surfaces or the
  temperature, and a tooltip with the reason ("Faces the mall, air-conditioned to the same temperature").
- Windows: area, glass type, facing, kW (from the glazing rows of the same audit).
- "What the AI assumed" list for the room (`phase1_assumptions` filtered by `room_name`), each with a
  "Change" link where an edit exists.
- Equipment found in this room (count + names, read-only): "Counted in Phase 2."
- Edit actions, reusing the existing editors and endpoints (do not rewrite their logic):
  - Area → `/api/room-area-override`; Ceiling height → `/api/room-height-override`;
  - Draw / redraw the room outline → the existing trace/measure tool, opened for this room;
  - What a wall faces, the roof → the existing walls/roof editor (`/api/reviewer-room-geometry` classify_envelope),
    scoped to this room;
  - Wall/roof construction, glass → the existing need-answer forms (`answer_need` kinds `construction`, `glazing`);
  - Windows → the existing windows editor for this room.
  After any edit, recalculate Phase 1 automatically (POST `/api/job-calculation` with `phase: "phase1_base_room"`,
  the same auto path the workspace already uses) and show "Updating…" on the affected room only.
- Blocked or not-yet-calculated room: say why in one line and show the one action that fixes it.

**Step complete** when Phase 1 has a current report (not stale, not blocked) covering every room in scope or the
remaining rooms are explicitly left out by the person. Then the status bar shows "Continue to operating loads".

### Step 3 · Phase 2 · Operating loads

Same layout as Phase 1 (room list + room card). This step is where the final calculation happens.

**Room card (Phase 2):**

- Header: room name · area · Phase 1 load (small, "empty room") · **Phase 2 adds** kW (once calculated).
- Sections, each a short table with value, source badge and an Edit control:
  - **People:** count (and how: seats, room type and size), sensible/latent per person.
  - **Lights:** W/m² or W total, lamp type.
  - **Equipment:** one row per item from the equipment list for this room (name, rating or typical value, source:
    data sheet / drawing / typical value from the database), with the existing equipment review actions
    (accept / correct / reject, data sheet field) moved here from the old Drawings → Equipment sub-tab.
  - **Fresh air & infiltration:** L/s and basis.
- Needs you (top of step): Phase-2 questions from the "What we need to find" list (equipment rating, occupancy,
  lighting, exhaust, opening hours), equipment items in conflict, and anything the final calculation reports as
  missing. Values that fell back to a typical value are **not** blockers: show them with the "Typical value" badge
  and list them under "Optional checks", per the product rule "never stop the calculation; tell the person a typical
  value was used".
- **Final calculation:** runs automatically once Phase 1 is complete and no Phase-2 blocker remains (POST
  `/api/job-calculation` with `phase: "full"` and `include` = every room included in Phase 1; reuse the existing
  job-calculation progress watcher). After any edit: "Inputs changed — Update the final load" (one button), or update
  automatically if no form is being edited.
- Remove the old "Calculate" everywhere else.

### Step 4 · Results (locked until Phase 2 is complete)

Unlocked only when the full report exists, is current (`job-status.tabs.results.state === "done"` and
`result_stale` is false) and covers the included rooms. Until then the step shows the lock and "Available when
Phase 2 is complete" — the page itself must not render numbers.

- Headline: **final design cooling load, kW** for the job, with the design day used ("Sydney summer design day") and
  the safety allowance stated.
- Per-room table: room · area · Phase 1 (empty room) kW · Phase 2 adds kW · **total kW** · W/m².
- Breakdown per room on expand: envelope, windows sun, people, lights, equipment, fresh air, infiltration.
- "What is still assumed" from `/api/result-status` (typical values, assumptions, items to find) with links to the
  room/section that fixes each.
- Export (existing report/export endpoints).
- If inputs change after results exist: the step shows "Out of date — update the final load" and the old numbers are
  greyed with "out of date" (never silently shown as current).

### Job details (drawer from the header)

Fields: job name, site address (prefilled with the AI's site text), building type, what is above the tenancy, weather
location (select; shows "Picked by the AI: Sydney (Melrose Park NSW)" when `weather_ai_pick` is set), all from
`/api/job-status` and saved with `/api/job-setup`. The AI fills these; the person only changes them. Remove the
separate Project tab and its "Job & site / Tenancy context" sub-tabs.

## Shared components

- **Source badges** (one set, used everywhere, text + icon, defined once in CSS):
  `From drawings` (printed or read on a page; tooltip shows the page) · `AI worked out` (traced/derived) ·
  `Assumed` (labelled default or most-likely case; tooltip shows the reason) · `Typical value` (database/pack value) ·
  `Entered by you` (operator override). Map existing origins (`pdf_evidence`, `printed (read from image)`,
  `ai_determined`, `reviewer_traced`, `edited`, `preliminary_fallback`, `controlled_fallback`, …) to these five in one
  function.
- **State icons/chips** for steps and rooms (Done / In progress / Needs you / Not yet / Locked / Left out / Out of date).
- **Needs-you item:** room · what is missing · why it matters (one sentence) · inline input or one button · source
  picker where the answer needs one (existing `ANSWER_SOURCES`).
- **Progress timeline** for AI stages; **status bar**; **room list**; **room card**; **surface table**.
- **Empty / loading / error** states for every list and card, all in plain words with one action.

## Visual design

Redo the look as one consistent system (keep the existing brand colours, logo and light/dark support; use the
existing CSS custom properties in `frontend/css/design-system.css` / `workspace-theme.css` as the token base and
remove unused duplicates):

- Content width max ~1120 px; 8 px spacing scale; one card style; one table style; one button hierarchy
  (one primary per screen region, secondary, link).
- Type scale: page title, step title, card title, body, small/fine — no more than five sizes.
- Status colours always paired with an icon and text; contrast WCAG 2.1 AA in light and dark.
- Numbers right-aligned in tables, units in the column header, kW to one decimal.
- Motion: subtle (spinner, step change); respect `prefers-reduced-motion`.
- Keyboard: every control reachable and visible on focus; the stepper is a `nav` with `aria-current="step"`.

## Copy rules

- Use: room, wall, roof, floor, window, drawings, page, AI, Phase 1 (empty-room load), Phase 2 (operating loads),
  final load, typical value, assumed, needs you.
- Never show: skill(s), finding(s), subskill, P0–P6, Card P, provider, model names, tokens, fingerprint, scope,
  manifest, evidence fusion, run id, JSON, "reviewer trace", "below accuracy bar". Translate them (e.g. "AI-determined
  (below accuracy bar)" → badge `AI worked out` with tooltip "Not yet checked against a person's measurement").
- One idea per sentence; tell the person what to do, not how the system works.
- AI cost and usage appear only in Advanced tools.

## Backend additions (small, read-only, no change to calculation methods)

Only add what the screens need and the current endpoints can't give compactly:

1. `GET /api/job-status`: add `stage` (`drawings` | `phase1` | `phase2` | `results`), `phase1_complete`,
   `phase2_complete`, `results_ready` and `needs_you` counts per step, derived from the existing statuses,
   `phase1_base_room_load_report` (current and not blocked) and the full report (current). Keep all existing fields.
2. `GET /api/projects`: add `stage` and `needs_you` per job (cheap, from saved files only), for the Jobs list.
3. A compact per-room Phase 2 view for the operating-loads cards (people, lights, equipment, air with value, unit,
   origin) — add it to the existing workspace response of `/api/ai-preliminary-model` (from the current input set's
   `materialized_fields` / internal-gains and airflow resolutions and the full report's per-room components) rather than
   a new calculation.
Each addition needs unit tests. Do not change how any load is calculated.

## What to keep working

- All existing API endpoints and actions (retry, answer_need, overrides, envelope classification, equipment review,
  job-calculation, automatic Phase 1 start, the stuck-run detection).
- `?engineer=1` (old detailed tools page) and `?operator=1` (manual AI task review), reachable only via the `⋯` menu
  in those modes.
- The guard that does not redraw a screen under someone typing, and "only the latest redraw paints".
- Light and dark themes; the landing page (`frontend/landing.html`) is out of scope.

## Files

Expect to rewrite `frontend/js/workspace.js` into smaller modules (e.g. `frontend/js/job/frame.js`, `drawings.js`,
`rooms-phase1.js`, `operating-phase2.js`, `results.js`, `components.js`, `jobs.js`) loaded by `frontend/index.html`,
and to replace `frontend/css/workspace.css` (+ remove dead rules from `styles.css`, `analysis-workspace.css`,
`simple.css`). Keep `frontend/js/app.js` only for `?engineer=1`; do not load it in the normal flow if it is not needed
there. Update `frontend/index.html` to the new frame (remove the old rail, sub-tabs, guide, results section from the
default view). Backend: `backend/job_service.py`, `backend/web_app.py` (projects list), `backend/ai_preliminary_service.py`
(workspace view) for the read-only additions.

## Tests

- Rewrite `frontend/tests/workspace.spec.mjs` for the new screens (mocked APIs, as now). Cover at least: Jobs list
  states; upload → Drawings; each AI stage working / stopped (retry) / done; Phase 1 room list states and a room
  card's surface table, assumptions and each edit path (one test per editor); Needs-you items answered inline;
  Phase 2 room card sections, equipment review, automatic final calculation and "Inputs changed — update";
  Results locked before Phase 2 and unlocked after; stale results greyed; old routes redirect; engineer/operator mode
  still reach their tools; mobile layout (375 px) without horizontal scroll; dark mode; keyboard-only path through
  the stepper.
- Backend unit tests for every addition.
- Run `python3 -m unittest discover -s tests` and `cd frontend && npm test`; both must pass.
- Then check the real app in a browser on a saved job (use `docs/BROWSER_TEST_LOOP.md` for what to check, but report
  per `AGENTS.md` — no "zero issues" claims): take the job through every step, at desktop and 375 px, light and dark,
  and fix what you find.

## Build order (each milestone ends working and tested)

1. Job frame: header, stepper, status bar, route redirects; remove rail/sub-tabs/guide/next buttons/rail Calculate.
   Results step locked. `job-status` additions.
2. Step 1 Drawings timeline and summary.
3. Step 2 Phase 1 room list + room card + Needs you + edits + automatic recalculation.
4. Step 3 Phase 2 room card + equipment review + automatic final calculation; Phase 2 workspace view addition.
5. Step 4 Results (unlocked only after Phase 2), stale handling, export.
6. Jobs home and New job; projects list addition.
7. Visual system pass (tokens, components, dark mode, mobile, accessibility) and dead-CSS/JS removal.
8. Real-app browser pass and fixes.

## Done means

- [ ] Inside a job there is exactly one navigation (the stepper) and at most one primary action per region.
- [ ] No total kW, "Calculate" or Results numbers appear before Phase 2 is complete.
- [ ] Phase 1 and Phase 2 are both room by room; every surface, value and assumption shows its source badge.
- [ ] Everything that needs the person is in that step's Needs-you list, answerable inline; optional checks are
      separate.
- [ ] Every waiting/stopped/empty/error state says what is happening and offers one action.
- [ ] No internal terms in the normal flow; engineering tools only in engineer/operator mode.
- [ ] Old links redirect; existing API actions still work; tests pass; real-app pass done at desktop and 375 px in
      light and dark.
- [ ] Completion report lists anything not done and why (per `AGENTS.md`).
