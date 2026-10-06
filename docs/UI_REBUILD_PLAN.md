# Plan: a usable job workspace, CAMEL-style tabs adapted to Toki (2026-10-06)

## Why
The 2026-10-06 usability audit (`docs/USABILITY_AUDIT_2026-10-06.md`) found:
- the engineer page is ~130,000 px tall, with 1,605 inputs and ~37,000 words;
- the room tracing tool is ~100 screens down;
- without the AI step a contractor hits a dead end;
- the AI step itself is hidden behind `?operator=1`.

CAMEL+ (screenshots in `docs/camel_screenshot_feature_report.md`) shows the layout contractors already know:
a job-name header, a short list of input tabs in a left rail, one focused grid per tab, a validity badge
and an always-visible **Calculate** button.

## What we take from CAMEL, and what we change

| Take | Adapt for Toki | Don't take |
|---|---|---|
| Left rail with the job name and short tab list | Tabs follow our data (rooms first, plant last) and show **status** for each tab, not only "Valid" | CAMEL's colours, logo, fonts, layout pixel-for-pixel (our own design system) |
| One grid per tab, rows = items (rooms, windows) | Cells come **pre-filled from the drawings by AI**, each with a source mark; editing a cell records an "Edited by you" override | Empty grids the user must fill from scratch |
| Calculate always visible | Calculate tells you what's still needed and links to that tab | Running with silent defaults |
| Undo/redo, field help "?" | Help explains in plain words what the value means and where it came from (page, quote) | CAMEL's embedded tables, defaults or DA09 values (licensing, see AIRAH decision) |
| Tab order from project → envelope → loads → plant | Our order puts drawings and rooms first, because the AI starts there | Plant tabs (chillers, boilers, preconditioners) in V1: out of the preliminary scope |

## Principles
1. **One screen = one job.** No page longer than about two screens. Everything else lives in another tab or a closed "More" section.
2. **The rail is the guide.** Each tab shows ✓ Done / ● Check / ! Needed / ◌ Working. The first tab needing something is highlighted, and every tab ends with "Next: … →".
3. **AI fills, people correct.** Every value shows where it came from (AI from the drawings, Printed on the drawings, Assumed, Edited by you). Any value can be corrected in place.
4. **Calculate is never a dead end.** If something blocks it, say exactly what and link to it. If something is merely missing, calculate anyway and list it as "Not included".
5. **Plain words.** No "resolver", "coverage", "remediation", "calibrate", "assumption pack" on contractor screens. Engineer terms live in the Engineer review tab.
6. **Fast.** Each tab loads only its own data, and opening a job shows the rail within ~1 s.

## The workspace

```
┌─────────────── job header: name · address · draft total (kW) · [Calculate] ───────────────┐
│ LEFT RAIL                   │  TAB CONTENT (one focused page)                              │
│  Butcher Buffet             │                                                              │
│  ✓ Project                  │  Rooms                                   ● 1 to check        │
│  ◌ Drawings  (6/14 checks)  │  ┌──────┬───────┬──────┬────────┬────────┬─────────┐         │
│  ● Rooms                    │  │Room  │Area m²│Use   │Ceiling │Include │Source   │         │
│  ✓ Walls & roof             │  ├──────┼───────┼──────┼────────┼────────┼─────────┤         │
│  ! Windows                  │  │Shop  │217.3  │Dining│2.7 m   │ ☑      │AI · p20 │         │
│  ✓ People & equipment       │  │...                                              │         │
│  ● Kitchen                  │  └─────────────────────────────────────────────────┘         │
│  ✓ Fresh air                │  [Measure a room on the plan]  [Add a room]                  │
│  ─────────                  │                                                              │
│  Results                    │                                     Next: Walls & roof →    │
│  Engineer review            │                                                              │
│  [ Calculate ]              │                                                              │
└─────────────────────────────┴──────────────────────────────────────────────────────────────┘
```

On a phone, the rail becomes a "Job sections ▾" menu above the content, and the Calculate button stays at the bottom of the screen.

## Tabs (V1)

Each tab lists what it shows, where the data comes from today (existing endpoints) and what is new.

### 1. Project
- **Shows:** job name, site address, building type (food tenancy, retail, office…), level, "what's above this
  tenancy?" (roof / another floor / not sure), design weather basis (read-only).
- **Data:**
  - `/api/site-location-resolution` (address, AI site task);
  - P5 roof answers (`/api/autonomous-tasks` `answer_roof`);
  - `/api/design-requirements` (building/project context);
  - `/api/site-design-conditions` (read-only basis).
- **New:**
  - one "about this job" form shown right after upload (name, address, building type, roof above);
  - roof answered once for the tenancy, not per room.
- **Status rules:** ! when the address and roof are both unknown; ● when the address was found by AI below
  the accuracy bar; ✓ otherwise.

### 2. Drawings
- **Shows:**
  - the pages used (thumbnails, with an include toggle);
  - the drawing check's progress ("6 of 14 checks done");
  - for the operator, **one task at a time**: prompt, image downloads, reply box, then the next task.
- **Data:** `/api/analysis`, `/api/decisions` (page choice), `/api/autonomous-tasks` (`run_all`,
  `validate_apply`, labels progress).
- **New:**
  - an operator mode inside this tab (shown when `?operator=1` or a setting is on), replacing the
    hidden list panel;
  - automatic drawing confirmation (already in the contractor view).
- **Status rules:** ◌ while checks are waiting; ● when some checks are blocked; ✓ when all are done.

### 3. Rooms
- **Shows:** a grid of room, level, area, use, ceiling height, include (☑), source. Below it:
  - "Measure a room on the plan": the tracing tool, full width, opened on the room's plan page, guided as
    corners → printed dimension → save;
  - "Add a room".
- **Data:**
  - `/api/ai-preliminary-model` `room_scope` (list, include, confirm);
  - `/api/room-use-resolution` (use override);
  - `/api/ceiling-volume-resolution` (height override);
  - `/api/reviewer-room-geometry` (tracing);
  - S1 printed areas.
- **New:**
  - **typed area override** ("I know it's 104 m²"), recorded as "Edited by you" in reviewer room geometry
    as an area-only record (the same shape as scanned printed areas, a different source);
  - the tracing tool extracted from the engineer page into this tab.
- **Status rules:** ! when no included room has an area; ● when any area or use is below the accuracy bar
  or assumed; ✓ otherwise. Confirming the list is implicit: it happens on Calculate, with the user's
  remembered name.

### 4. Walls & roof
- **Shows:**
  - a small plan with each wall coloured by what it faces (outside / mall / neighbour / another room), and a
    table of runs: length, faces, source;
  - roof exposed per room.
- **Data:** P3 results (`reviewer-room-geometry` edges, `edge_sources`), P5, `/api/thermal-surface-resolution`.
- **New:** an edit control per wall run ("faces: outside ▾"), recorded as a reviewer declaration (the
  backend already supports reviewer precedence per edge).
- **Status rules:** ! when any comfort room has unclassified walls; ● when walls or roof are AI below the
  bar or answered "not sure"; ✓ otherwise.

### 5. Windows
- **Shows:** a grid of room, wall it's on, width, sill, head, glass type, shading, source, plus the
  elevation image with panels outlined.
- **Data:** P4 results (trace openings), `/api/window-scan`, `/api/glazing-method-gate`.
- **New:** inline edit of width/sill/head and add/remove window, using the existing reviewer opening records.
- **Status rules:** ● when windows are AI below the bar; ✓ when there are none on outside walls, or all are
  confirmed.

### 6. People & equipment
- **Shows:** per room: people, lighting W/m², equipment W/m², operating hours, source.
- **Data:** `/api/internal-gains-resolution` (has overrides), `/api/schedules`.
- **New:** grid editing on the existing override API.

### 7. Kitchen *(shown only when a kitchen is detected)*
- **Shows:** the identified equipment list (P6), rangehood and exhaust rate.
- **Data:** P6 results.
- **New:** inputs for the exhaust rate and equipment heat. Heat values stay blocked until the AIRAH DA09
  source decision; the tab says so plainly ("Kitchen heat not included yet — needs AIRAH data").

### 8. Fresh air
- **Shows:** per room: outside air (L/s), exhaust, air leakage; the basis (AS 1668.2 / project rule) in
  plain words.
- **Data:** `/api/ventilation`, `/api/airflow-resolution`, `/api/au-ventilation-rules`.

### 9. Results
- **Shows:** the existing simple result: total, by room, included, not included, print/PDF, CSV.
- **Later:** a room-load breakdown chart and peak-hour chart (CAMEL report items 25–27), from the hourly report.

### 10. Engineer review *(last, collapsed by default)*
Holds the current engineer page's tools as **closed sections with a contents list**:
- evidence review (the ~80 forms become a summary table with a "Review" button per item);
- method gates;
- source packs;
- AHU / plant;
- annual;
- audit and packages.

Nothing is deleted; it is just out of the contractor's way.

## Calculate (job header and rail)
- Runs confirm-room-scope (with the remembered name), then calculate.
- **Blocking issues** (no rooms with area, drawings not confirmed): open a short sheet listing them, each
  linked to its tab.
- **Missing but non-blocking items** (kitchen heat, leakage): calculate, and show them under "Not
  included" in Results.
- The header shows the last total and "out of date" when inputs changed since then.

## Technical approach
- **Shell:**
  - new `frontend/js/workspace.js` and `frontend/css/workspace.css`, replacing `contractor.js` (its result
    rendering and flow logic move into the Results and Drawings tabs);
  - hash routes `#/job/<id>/<tab>`, so Back, refresh and links work.
- **Tabs are modules:** each has `load(jobId)` and `render(container, data)`, and loads only its own
  endpoints.
- **Status** comes from a new small endpoint, `GET /api/job-status?project_id=` (one call for every badge
  plus the header total). It is built from existing artifacts: room scope, task progress, the
  internal-gains/ventilation readiness and the last report. This avoids loading every artifact to draw the
  rail.
- **Grid component:** a small shared editable table (keyboard navigation, source chip per cell,
  undo/redo of unsaved edits, save per row). Edits call the existing override endpoints.
- **Reuse the engineer panels:** existing sections move into tabs keeping their element IDs, so the 70
  browser tests keep passing while sections migrate. The old long page remains reachable as Engineer
  review until it's empty of contractor tasks.
- **Backend additions:**
  - `GET /api/job-status`;
  - typed room-area override (area-only reviewer record, source "edited");
  - reviewer edit of one wall run;
  - "about this job" fields on the project record.

## Build order (each phase ends usable and tested)

| Phase | Delivers | Done when |
|---|---|---|
| 1. Shell + Project + Rooms + Results | Left rail, routing, job header with Calculate, status endpoint, Project form, Rooms grid (include, use, height, typed area), Results | A fresh Butcher Buffet upload reaches a number from these tabs alone, with room areas typed in if there's no AI; no page longer than about two screens; timed walkthrough recorded |
| 2. Drawings + operator mode | Page list, check progress, one-task-at-a-time operator flow inside the tab | Butcher Buffet's AI step done from the Drawings tab without `?operator=1` typing; time per task measured |
| 3. Measure a room | Tracing tool extracted into Rooms (full width, guided) | A room traced and calibrated without visiting Engineer review; first-room time under 5 min |
| 4. Walls & roof, Windows | Plan colouring, wall-run edits, window grid and edits | Butcher Buffet: every comfort wall classified and the shopfront window visible and editable |
| 5. People & equipment, Kitchen, Fresh air | Grids on existing overrides; kitchen tab with the AIRAH-pending note | All overrides round-trip and show "Edited by you" in Results |
| 6. Engineer review clean-up | Contents list, the 80 review forms → summary table, sections closed | Engineer review under 10 screens when expanded section by section |
| 7. Contractor session | Run `docs/CONTRACTOR_SESSION_PACK.md` on this UI | Session notes and the next fixes |

### Phase 1 status (2026-10-06)
**Built:**
- **Rail and routing:** a left rail with a status per tab (`/api/job-status`, one call); hash routes `#/job/<id>/<tab>`; a section menu at phone width.
- **Job header:** the total, plus Calculate (assemble → confirm cooled rooms → calculate, with the resolver run as a fallback).
- **Project tab:** name, address ("Use this" for the address found on the drawings), building type, and what's above. The answer to what's above settles every open roof question.
- **Drawings tab:** check progress, plus a link for the Toki team (`?operator=1`).
- **Rooms grid:**
  - a Cool tickbox and a use choice where the use is unknown;
  - a typed area and a typed ceiling height ("Edited by you"; they beat AI and drawing values);
  - "Trace on the plan" and "Add a room the drawings missed".
- **Results tab:** the total, a by-room table, what was included and what wasn't, and print/CSV.
- **Engineer review:** one click away; `?engineer=1` opens it directly.

**Verified:**
- `npm test` passes (Python suites plus 76 Playwright tests, 11 of them in `workspace.spec.mjs`).
- Real-data browser check on the Butcher Buffet walkthrough copy: typed areas give 34.8 kW, and a typed Shop height of 3.6 m reaches the model as a contractor override.
- Phone width (375 px): no page-wide horizontal scroll on Rooms, Results or Project.

**Open items:**
- **The "fresh upload" walkthrough is not timed yet** (it was checked on an earlier upload's copy).
- **Speed:** Calculate takes about 80 s; the first resolver run takes up to about 3 min; adding a room takes about 15 s.
- **Typed-area rooms have no outline**, so their walls and roof are not assessed (envelope 0 kW). On that job a typed height changes nothing, because only the envelope uses height. The Results "not included" list says so; tracing (phase 3) fixes it.

### Phase 2 status (2026-10-06)
**Built (Drawings tab):**
- **Drawing check progress.** Checks that wait on earlier checks are no longer counted as done.
- **Pages used:** thumbnails with tickboxes, main plans marked, "Show all pages", and "Use these pages". That last button prepares the pages again and rebuilds the checks.
  - The saved page selection is now returned as `selected_pages` and restored on reload, on the engineer screen too. Before this, unticked pages came back after a reload.
  - A re-render or tab switch during preparation waits for the same run, and leaving the page asks for confirmation.
- **AI step for the Toki team.** It opens with `?operator=1`, or with "Toki team: answer the checks here", which is remembered per browser. It shows one check at a time, in dependency order:
  - copy the prompt;
  - copy, download or drag the images;
  - paste the reply;
  - press Check and apply.

  A rejected reply stays in the box with the reason. "Skip for now" moves on. Below the current check are lists of blocked checks, roof questions left to the contractor, and finished checks.
- **Timing.** The time per check is sent as `operator_seconds` with each reply, stored with the reply attempt, and summed in the panel ("about N min each").

**Verified:**
- `npm test` passes (Python suites plus 81 Playwright tests, 16 of them in `workspace.spec.mjs`).
- Real-data browser check on the Butcher Buffet walkthrough copy:
  - a rejected reply, then an accepted stand-in reply (marked as a test) for the site check;
  - the north-arrow check advanced with its 3 images (1024 × 724);
  - Skip;
  - unticking and re-ticking a page end to end: 391 s, and the answered site check was kept;
  - phone width (375 px) with the prompt open.

**Open items:**
- **Time per check with real ChatGPT replies is not measured yet.** The timing is built in, but only stand-in replies have been run. The next real Butcher Buffet run through this tab gives the numbers.
- **Preparing pages is driven from the browser** (save decisions, then rebuild the prepared drawings). If the page is closed mid-way, the job is left half-prepared until the Drawings tab is opened again, which then prepares it automatically. A single server-side action would remove this.
- **Speed:** a page change takes about 6–7 min on the 38-page set.

### Phase 3 status (2026-10-06)
**Built (Rooms tab, "Measure on the plan"):**
- **A guided measuring view inside the Rooms tab** (no Engineer review). It opens on the main plan; when the room's name is printed on another page, it says which page. Its three steps:
  - **Outline the room.** Corners snap to the vector wall lines, and clicking the first corner again closes the outline. Undo and Start again are available.
  - **Set the scale from a printed dimension.** Click both ends, then type the printed number. This mirrors the server rule: the dimension must agree with the stated scale within 2%, or two dimensions must agree with each other. A scale already set on the page for another room can be reused.
  - **Save.** It shows the live area and needs the person's name.
- **Navigation:** zoom (wheel or buttons), drag to move, and keyboard input (arrows, Enter, Backspace). The step bar has a fixed height, so the plan never moves under the cursor, and the plan is sized to fit under the steps on one screen.
- **After saving:**
  - the room shows "Measured on the plan" straight away (`traced_rooms` in `/api/job-status`, computed from the saved outline and scale);
  - a measurement replaces an area the person typed earlier (the typed value is cleared, and the message says so);
  - Results is marked out of date until the next Calculate.

**Verified:**
- `npm test` passes (Python suites plus 83 Playwright tests, 2 new for measuring).
- Real clicks in the browser on the Butcher Buffet walkthrough copy, page 20:
  - **Kitchen:** 14 corners plus the printed 23,265 mm dimension (0.3% off 1:100), giving **104.4 m²** against the answer key's 104.9 m² (−0.5%). The save took 11 s. After Calculate, Results showed Kitchen 104.4 m² "Measured on the plan", total 34.4 kW (Calculate took about 2 min).
  - **Bar:** reused the Kitchen scale, giving 32.3 m². The save took 16 s.
- Phone width (375 px): no page-wide scroll.

**Open items:**
- **First-room time for a real person is not measured yet.** The scripted run (clicks computed from a known outline) took 67 s, which says nothing about a contractor finding the corners. The contractor session (phase 7) measures it against the 5-minute target.
- **Measuring doesn't yet bring walls and roof into the number.** A new trace's walls are "not classified yet", and its roof is "not checked yet":
  - the P3 wall check for a new trace only appears when the drawing checks are rebuilt;
  - the job-level "what's above" answer only settles open AI roof questions, not a hand-measured room's roof.

  Both belong to phase 4 (Walls & roof).
- **Phone:** the plan is small at 375 px and there's no pinch-to-zoom (zoom buttons only).

## Testing
- **Playwright, per tab:** loads its data, edits round-trip, status badges change.
- **Plus:**
  - one full-journey test (upload → result) with mocked endpoints;
  - one regression test per audit finding.
- **Per phase:** a timed real-data walkthrough on Butcher Buffet and the scanned mini-split set, following
  `docs/BROWSER_TEST_LOOP.md` (screenshots, console errors, phone width).
- **Measures tracked:** time from upload to first number; clicks to a number; longest page height;
  visible inputs on the screen in use.

## Decisions needed
1. **The tab layout replaces the 3-step simple view** (recommended; the steps become statuses and "Next").
2. **The tab list** above. In particular: separate Kitchen and Fresh air tabs (recommended; Kitchen shows
   only when detected), and no plant tabs in V1.
3. **A typed room area counts as an input** ("Edited by you") even without tracing (recommended: yes,
   labelled, and it beats AI values).
4. **Who builds what:** recommended split — Claude builds the shell, status endpoint, grid component and
   phases 1–3; Codex moves the existing engineer sections into tabs (phases 4–6) to the same pattern.
