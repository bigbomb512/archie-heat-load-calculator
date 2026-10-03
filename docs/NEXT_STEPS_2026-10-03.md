# Next steps plan — 2026-10-03

Context: after Cards A–E the Butcher Buffet walkthrough reaches a labelled AI
preliminary draft of **33.8 kW** (30.75 kW raw), with envelope = 0 kW, generic
Australian design-day weather and default kitchen equipment (see
`docs/WALKTHROUGH_2026-10-02_butcher_buffet.md`). The user reports the
engineer's actual cooling load for Butcher Buffet is **~39.6 kW for the entire
building**, and a result of **35–50 kW** is considered acceptable — the first
independent reference case and its tolerance. The finer basis (sensible vs
total, safety factor, weather location, whether "entire building" includes the
cold rooms) is not known.

The current draft (33.8 kW) is **below the 35 kW floor**. Being near the
reference is not evidence of accuracy anyway: it omits the envelope and
air-side loads and uses generic weather and equipment defaults, so errors are
partly offsetting each other.

## Sequence and ownership

| Step | Work | Owner | Can run in parallel with |
|---|---|---|---|
| 1a | Card F — remove ChatGPT-paste gate | Codex | 1b, 2 |
| 1b | Card G — default single floor | Codex | 1a, 2 |
| 1c | Card H — draft review in one pass | Codex (after F merges; both touch `app.js`) | 2 |
| 2 | Global Exchange walkthrough on current code | Claude (browser) | 1a–1c |
| 1d | Card M — room labels only from real plan rooms (added after step 2) | Codex | 1c |
| 1e | Card N — reviewer confirms room list before a draft number | Codex (after H) | 1d |
| 1f | Card O — trace on mechanical-only sets (2 parts) | Codex (after N) | — |
| 3 | Contractor workflow session | User runs it; Claude prepares script + project, analyses notes | after 1a–1e merged (O not required for Butcher Buffet) |
| 4 | Envelope + site weather, measured against 39.6 kW | Codex implements, Claude reviews + measures | after 1–2; cards refined from step 2 findings |

Workflow per card (unchanged): Claude writes card → Codex implements on the
card's files only, runs the listed tests → Claude reviews diff + real-UI check
on the walkthrough project → Codex fixes findings → user commits.

---

## Step 1 — make the path usable without workarounds

### Card F — reviewed workspace without a pasted ChatGPT reply

```
Goal: after "Confirm selected drawings", a contractor can reach the reviewed workspace (tracing, design inputs, calculator draft, guided resolver) without pasting a ChatGPT reply. AI evidence stays optional and its absence is shown honestly.

Facts:
- frontend/js/app.js ~578: workflowSkeleton is hidden unless data.has_reasoning_packet; ~582 showDesignRequirements also gated on it.
- backend/web_app.py ~4933: has_reasoning_packet = bool(project["reasoning_packet"]); the packet is only built by rebuild_reasoning_packet (~4650), which reads vision_response.json — i.e. only after a pasted reply (api_save_vision_response ~1655).
- api_save_design_requirements (~4296) raises "Create a reasoning packet before saving design inputs." when project has no vision_response.
- Walkthrough workaround that works today: paste `{}` under Advanced recovery → archived as accepted → packet built → workspace unlocks. So an empty vision response is already a valid input to the pipeline.

Change:
1. Add an explicit server action that builds the reasoning packet from an empty vision response marked as "no AI evidence" (e.g. vision_response.json with a top-level "evidence_source": "none" or equivalent field the validator accepts), archived in the paste history as "No AI reply — started without AI evidence", not as an accepted AI reply. Reuse the same code path the `{}` paste uses; do not fork the packet builder.
2. Call it automatically at the end of confirmSelection when the project has no vision_response yet (idempotent: never overwrite a real pasted reply; a later real paste replaces the empty one through the normal path).
3. UI: workspace visible after confirmation; one line in the vision panel: "No AI evidence yet — tracing and manual inputs work; paste a ChatGPT reply any time to add AI evidence."
4. Remove the dead-end toasts on "Open geometry editor" / "Open reviewed workflow" (they now open).

Files: backend/web_app.py (+ the vision-response/paste-archive helper it uses), frontend/js/app.js, tests (backend + Playwright).
Tests:
- Fresh upload → confirm → workspace visible, design inputs save, no paste performed; paste history shows the "no AI evidence" entry, not an accepted AI reply.
- Later real paste replaces it and is archived normally.
- Confirming again does not overwrite an existing real reply.
- Existing 32 Playwright tests pass.
Out of scope: changing what AI evidence does once present.
Verify on a fresh upload of the Butcher Buffet PDF in the real UI (new project, not the walkthrough one); report the steps.
```

### Card G — default single floor when no level is stated

```
Goal: when the drawing set states no building level, the calculator draft proposes one provisional floor so zones and rooms can be accepted directly, instead of requiring the hidden "Add floor" detour.

Facts:
- ai/calculator_draft.py ~263-273: floor candidates are created only from named levels; names starting "unassigned" raise floor_unknown and create no floor. Zones then get floor_id "" / floor_status "unresolved" (~382) and apply_calculator_draft (~635) refuses zones/rooms without a floor ("A zone cannot be applied without a reviewed floor assignment.").
- Butcher Buffet: user-confirmed that FL 01/FL 02 are floor-finish codes and no level is stated (memory/evaluation facts). All five rooms sit on "Unassigned level".

Change:
1. When no real floor is identified AND all room evidence is on an unassigned level, add one floor candidate: name "Single level (assumed — no level stated in drawings)", confidence "assumed", evidence = the plan pages the rooms cite, reason "No building level is stated; confirm this single-level assumption." Keep the floor_unknown issue but reword it to point at this candidate.
2. Map zones on the unassigned level to that floor candidate (floor_status "proposed", dependency on the floor candidate) so accepting floor + zone + room applies without edits.
3. Never apply this when any named level exists (multi-level sets keep today's behaviour), and never auto-accept it.

Files: ai/calculator_draft.py, tests for the draft builder/apply.
Tests:
- Unassigned-only rooms → one assumed floor candidate; zones depend on it; accepting floor+zone+room applies all three (records created > 0).
- A named level present (e.g. "Level 2", the Global Exchange case) → no assumed floor.
- Rejecting the assumed floor → zones stay unresolved with the existing message.
Out of scope: inferring levels from PDFs.
Verify: rebuild the calculator draft on the Butcher Buffet walkthrough project and report the floor candidate and zone mappings.
```

### Card H — calculator draft review in one pass

Refined 2026-10-03 against the code after Cards F/G (commit `70eb84b`).

```
Card H — calculator draft review in one pass

Goal: a reviewer can accept the floor/zone/room/area set of a draft with one reviewer entry and one click per group, see what preview/apply will do next to the buttons, and never lose saved decisions on re-render. Engineering rules do not change: every non-pending decision still carries reviewer attribution; edits still need a review source and citations.

Acceptance (real UI, FGCHECK project — fresh Butcher Buffet with the assumed floor, 1 floor + 5 zones + 5 rooms):
- One reviewer name, "Accept all" on the floors, zones and rooms groups, Save → Preview → Apply creates 1 floor + 5 zones + 5 rooms with no per-candidate typing.
- Report the count of fields typed and clicks needed (walkthrough baseline: 15 reviewer fields + 10 review-source fields + 25 selects, plus a failed save after re-render).

Facts (frontend/js/app.js unless noted):
- showCalculatorDraft ~3171 renders groups (~3207-3211) via calculatorDraftCandidateMarkup ~3302; each candidate has its own decision <select> and, inside a collapsed <details>, its own "Engineer review source" / "Reviewer" / citation fields (class calculator-draft-review-field, data-field="source"/"reviewer"/…).
- Candidate headings show kind + hash ("zone · zone_ca5ff29ae1923cb3"); the human name is only in value.name inside <details>.
- calculatorDraftDecisions ~3335 reads reviewer/source per row only; no session-level value.
- calculatorDraftCandidateMarkup restores the saved decision select (savedDecision.decision) and edited values (savedDecision.value) but renders reviewer/source/citation inputs empty → a second save fails with "Reviewer attribution is required: <id>" (ai/calculator_draft.py save_review ~597).
- Review items (calculatorDraftReviewMarkup ~3325) use data-reviewer-for; leave their behaviour as is.
- saveCalculatorDraft ~3372: preview with unsaved changes throws "Save the review before previewing changes." which only reaches a toast; the status line shows "Could not update calculator draft." (~3412).
- An "Application summary" with counts already exists (~3215-3222) but renders into #calculatorDraftSummary, which sits below #calculatorDraftReviewItems (42 items on Butcher Buffet) — effectively invisible — and shows no reasons. Preview results are passed into the same summary (~3393).
- index.html ~457-462: panel bar with #calculatorDraftStatus and the action buttons, then #calculatorDraftCandidates, #calculatorDraftReviewItems, #calculatorDraftSummary.

Implementation steps:
1. Session reviewer: add "Reviewer" and "Review source" inputs to the draft panel bar (next to the action buttons). In calculatorDraftDecisions, for every non-pending candidate decision, use the row's own reviewer/source when filled, otherwise the session values. If a non-pending decision would still have no reviewer, stop before the request and show "Enter a reviewer name (top of the panel) before saving." inline in #calculatorDraftStatus. Keep the session values in a module variable for the open project only (cleared when another project opens); no browser storage. Edited decisions still need their own citations — the session source does not replace them.
2. Group accept: each candidate group title gets an "Accept all in this group" button that sets every pending select in that group to "accept" (never "edit", never overrides a reject/needs_evidence/edit already chosen) and marks the draft dirty. No such button on review items.
3. Restore saved fields: in calculatorDraftCandidateMarkup, prefill reviewer, source, citation reference/excerpt from savedDecision (reviewer, source, citations[0]). Saving twice in a row must succeed without retyping.
4. Readable headings: candidate heading = "<kind> · <value.name or value.label or candidate_id>", keeping the hash in a <small> for traceability. For area candidates use the linked room name if available.
5. Visible outcome: move #calculatorDraftSummary to directly under the panel bar (above the candidates) and render, for preview and apply: counts (created / populated / already present / conflicts skipped / missing dependencies / unresolved) plus up to 8 plain-language reasons from unresolved and missing_dependencies, naming the candidate by its readable heading (e.g. "Zone Bar: a zone cannot be applied without a reviewed floor assignment."). When apply creates 0 records, say so and show those reasons.
6. Preview with unsaved changes: Preview saves the review first (same payload as Save), then previews, in one click; if the save fails, show the save error inline in #calculatorDraftStatus (not only a toast).
7. Keep the existing toasts but make every failure also visible inline in #calculatorDraftStatus with the server's message.

Files: frontend/js/app.js, frontend/index.html (panel bar inputs, summary position), frontend/css/workspace-theme.css only if layout needs it, frontend/tests/app.spec.mjs. No backend changes; if one seems necessary, stop and report why.

Tests (Playwright, mocked /api/calculator-draft):
- Session reviewer fills reviewer on every accepted candidate in the save payload; a per-row reviewer overrides it.
- No session reviewer and no row reviewer → no request sent, inline message shown.
- "Accept all" sets only that group's pending selects; an existing reject in the group is kept.
- Re-render after save keeps reviewer/source/citation values; a second save payload still carries them.
- Headings show names (e.g. "zone · Bar"), not only hashes.
- Preview with unsaved changes sends save_review then preview_apply, in that order.
- Apply returning 0 created + unresolved reasons shows the reasons in the summary block above the candidates.
- Existing 34 tests still pass.

Report: commands and results, the FGCHECK real-UI counts above, and screenshots or page text of the summary block after preview and after apply.
Out of scope: changing backend attribution rules, bulk handling of review items, the calculator maths, tracing UI.
```

### Card M — room labels only from real plan rooms

Handed to Codex 2026-10-03 (prompt in chat; see the Global Exchange walkthrough
findings #1–#3, #5). Files: `ai/room_inference.py`, `ai/building_evidence.py`,
`ai/room_use_resolution.py`.

### Card N — reviewer confirms the room list before a draft number

**Status 2026-10-03:** implemented by Claude (with the three Card H follow-ups:
room-input names, "+ N more" reasons, "Apply changed nothing" wording).
Room list confirmation lives in `ai/room_scope_confirmation.py`; the gate is in
`backend/ai_preliminary_service._calculate`; "Not a room" is taxonomy category
`not_a_room` (scope `not_a_room`), honoured by room-use, the trace room list, the
room registry/calculator draft and the preliminary assembler. Local test mode
auto-confirms as "Local test mode (not a review)"; the provider auto-run stops
at `awaiting_room_confirmation`. Verified on the Butcher Buffet walkthrough
project: confirming Bar/Kitchen/Shop gives the unchanged 33.8 kW with the rooms
listed under the total.

Start after Card H merges (both touch `frontend/js/app.js`). Independent of
Card M, but verify after M lands so the list is realistic.

```
Card N — reviewer confirms the room list before a draft number

Goal: the AI preliminary cooling result is only calculated from rooms a reviewer has confirmed, and the result shows which rooms (area, source, page) make up the number. A reviewer can mark a detected "room" as not a room, and rooms with an unresolved use are surfaced for a choice instead of being silently dropped.

Why (Global Exchange walkthrough, docs/WALKTHROUGH_2026-10-03_global_exchange.md #2, #3, #6): the architectural set produced 3.04 kW from Office 9 m² (real) + "Retail space including a museum and Front of house" 27.9 m² + "area objects" 9.6 m² (both from a render page), while the real Service Counter (13 m², scope unresolved_scope) was excluded — with no reviewer step and nothing on screen showing the room list. Card M reduces phantom rooms; Card N makes the remaining ones visible and reviewable, because detection will never be perfect.

Facts:
- Rooms enter the preliminary model via backend/ai_preliminary_service.py _prepare_preliminary_proposal (~445–545: proposal rooms with area or traced area → calculation_rooms) and ai/ai_preliminary.py _space_rows (~300–330: building_evidence spaces with an area). Calculation: ai_preliminary_service._calculate (~778) → ai_preliminary.calculate.
- Room-use resolution already supports a reviewer override: POST /api/room-use-resolution {action: "apply_override", room_id, taxonomy_id, reviewer, ...} (backend/room_use_resolution_service.py ~74–97); UI "Save classification" (frontend/js/app.js ~1850–1870). The room-use panel only lists "uncertain rooms".
- Taxonomy has no "not a room" option.

Change:
1. Room list confirmation artifact (e.g. room_scope_confirmation.json in the review dir): one row per candidate calculation room after assembly — room identity (room-use id), label, level, area, area origin (printed / reviewer-traced / AI), source pages, room-use scope — plus reviewer, timestamp and a fingerprint of the candidate list. Each row: include / exclude (reason).
2. Calculation gate: _calculate refuses (clear message, HTTP 4xx like other gates) when there is no current confirmation, or when the candidate list fingerprint changed since confirmation ("The room list changed; confirm it again."). Excluded rows are left out of the model and listed in the report's excluded spaces with reason "Excluded by reviewer: <reason>".
3. "Not a room" taxonomy option in room-use overrides (scope "not_a_room"): such records never appear as calculation rooms, trace targets, or draft candidates.
4. Rooms with unresolved_scope appear in the confirmation list with a required use choice (reusing the room-use apply_override call) instead of being silently dropped; the list cannot be confirmed while an included row has no use.
5. UI (guided flow, after "Resolve model inputs" succeeds and before "Calculate draft load"): a "Confirm rooms" block — table of rows with include toggles, use select for unresolved rows, one reviewer field, "Confirm room list" button. The AI preliminary result panel shows the confirmed rooms (label, area, origin, page) directly under the total, next to the Card E "Not included in this total" list.

Files: backend/ai_preliminary_service.py (+ a small new module if cleaner), ai/room_use_resolution.py (not_a_room), backend/room_use_resolution_service.py, backend/web_app.py (route only if a new endpoint is needed), frontend/js/app.js, frontend/index.html, tests (backend + Playwright).

Tests:
- No confirmation → calculate refused with the message; confirmation present → calculates using only included rooms.
- Candidate list changes after confirmation (new room, area change) → calculate refused until re-confirmed.
- Excluded row appears in excluded spaces with the reviewer reason and contributes 0 kW.
- not_a_room override removes the record from the trace room list, calculator draft candidates and preliminary rooms.
- Unresolved-scope row must get a use before confirmation; after choosing "Retail / showroom" it is included.
- Playwright: confirm block lists rooms with area + page; confirming enables calculation; result panel lists the rooms under the total.
- Existing tests: update only those that calculated without a confirmation (list each and why).

Verify on real data: on the Butcher Buffet walkthrough project (output/web_review/walkthrough-butcher-buffet-_not-for-design_-1790926231) confirm Bar/Kitchen/Shop → result unchanged (33.8 kW design) and rooms listed under the total. Report the result panel text.
Out of scope: improving detection (Card M), tracing (Card O), calculator maths.
Report commands and results.
```

### Card O — trace rooms on mechanical-only drawing sets

Start after Card N (both touch the trace/room UI). Larger than F–N: do it in
two parts and stop after part 1 to report.

```
Card O — trace rooms on mechanical-only drawing sets

Goal: when a drawing set has no architectural floor plan (e.g. a mechanical engineer's set), a reviewer can still create the real room(s) and trace/calibrate them on the scaled mechanical plans, so the area gate and draft work.

Why (Global Exchange walkthrough #4): the 7-page mechanical set has 1:50 plans (pages 4–6, role existing_hvac_plan). The trace tool shows "No supported rooms or rendered plan pages are available": no vector/render pages exist for them and, after Card M, no rooms are detected either (correctly).

Facts:
- ai/vector_geometry.py page_is_geometry_capable (~127) and is_detail_or_reference (~146) exclude pages whose title/role mentions "mechanical", "hvac", "services" — so vector_geometry.json has no pages for this set and no full-resolution renders are matched.
- backend/reviewer_room_geometry_service.py _page_context (~170–215) accepts only roles main_floor_plan / primary_geometry_plan / supporting_geometry_plan / floor_plan.
- _rooms (~239–270) lists rooms only from building_evidence spaces, room-use records and the room-inference proposal; current_records (~62) drops traces whose room_id is not in that list.
- drawing_coverage page_roles carry main_scale (e.g. "1:50") for these pages.

Part 1 — fallback plan pages (stop and report after this part):
1. When a drawing set has no page with a floor-plan role, treat scaled plan pages (main_scale present; roles existing_hvac_plan / services plan / similar plan-view roles; not schedules, legends, details, covers) as fallback trace pages: generate vector geometry + full-resolution renders for them and accept them in _page_context, flagged "fallback_plan": true with the reason "No architectural floor plan in this set; tracing on a services plan."
2. Sets that have an architectural floor plan are unchanged (no fallback pages added).
Tests: synthetic coverage with only existing_hvac_plan 1:50 pages → pages offered for tracing with the flag; a set with a main_floor_plan → services pages not offered. Real-data check: list the trace pages for the Global Exchange mechanical set (existing projects in output/web_review, read-only in memory).

Part 2 — reviewer-added rooms:
3. In the trace workspace, "Add room": name, level (pre-filled from the page level or the assumed floor), use (taxonomy). Stored as a reviewer room record (reviewer, timestamp, page), included by _rooms, room-use resolution (as reviewer-confirmed), calculator draft candidates and the Card N confirmation list. Removable by the reviewer; never auto-created.
4. Card N's confirmation and the area gate treat a reviewer-added room with a current calibrated trace like any other traced room.
Tests: add room → appears in trace list → trace + calibrate → passes the area gate and appears in the confirmation list; delete room → its trace is dropped; reviewer attribution required.

Files: ai/vector_geometry.py, backend/reviewer_room_geometry_service.py, ai/reviewer_room_geometry.py (record schema), backend/room_use_resolution_service.py / ai/room_use_resolution.py (reviewer rooms), frontend/js/app.js, tests.
Verify (after part 2), real UI on a fresh upload of the Global Exchange mechanical set: add "Kiosk" on Level 2, trace it on page 5 calibrated on a printed dimension, confirm rooms, calculate; report the area and draft result (no comparison claim against the FCU schedule).
Out of scope: auto-detecting room boundaries from ductwork, reading the FCU schedule.
Report commands and results for each part.
```

---

## Step 2 — Global Exchange walkthrough (Claude, starts now)

Purpose: test whether Cards A–E generalise beyond Butcher Buffet, on a set with
a stated level ("Level 2", user-confirmed) and an HVAC design set.

Plan:
1. Create a copy project from `Global Exchange WSA_Air Conditioning Design Full Set Drawings_Rev 3-AF 260701.pdf` (name prefixed `WALKTHROUGH`); back up `output/web_projects.json` first. Existing Global Exchange projects are not touched.
2. Run the same path as Butcher Buffet on current `main`: upload → confirm → (paste-gate workaround until Card F lands) → trace 2–3 representative rooms → draft → resolve → calculate. Time each step.
3. Record: which steps behave differently with a named level; whether room identities/areas resolve without tracing; any new blockers; screenshots of failures.
4. **Check whether the AC design set prints design capacities** (equipment schedules, unit kW). If it does, report them to the user as a *candidate* second reference — only used after the user confirms the basis.
5. Output: `docs/WALKTHROUGH_<date>_global_exchange.md` in the same format, plus new cards for anything that blocks. No code changes during the walkthrough.

---

## Step 3 — contractor workflow session (after F, G, H merge)

Purpose: measure whether a real contractor can get from PDF to a labelled draft
without help, and how long it takes. It is a **workflow** test, not a load
accuracy test — the draft still omits the envelope.

Preparation (Claude):
- A clean project on `main` with a drawing set the contractor knows (or Butcher Buffet), server running locally.
- A one-page script: the task ("get a draft cooling load for this tenancy"), what not to explain, and a note-taking sheet: time per stage, every hesitation/question, every error, final result and whether they trusted it.

Session (user runs it, 30–45 min, screen recording if they agree):
- No guidance beyond the task statement; note where they ask for help.
- Afterwards ask: what did you expect the number to include? would you send this to a client? what would you need to trust it?

Analysis (Claude): turn the notes into the friction-log format, rank fixes,
write cards.

---

## Step 4 — accuracy: envelope first, then site weather

Measured against the Butcher Buffet reference (39.6 kW). Track the gap **per
component change**, not just the total, so offsetting errors stay visible.

Before starting, the user should confirm the reference's basis (see Questions).

Phased cards (outline; final wording after step 2, because Global Exchange may
change the design):

- **Card I — envelope surfaces from traced boundaries (data + calc).** Each
  trace polygon edge becomes a candidate wall surface (length × ceiling
  height). A reviewer classifies edges as external / adjacent tenancy /
  internal. External walls get provisional constructions from the preliminary
  pack (labelled, cited to the pack), roof included only when declared (Butcher Buffet: single-storey, roof declared by the user 2026-10-03).
  Surfaces flow into `effective_proposal["surfaces"]` →
  `ai_preliminary.assemble` (~950) so the existing envelope maths and Card E
  exclusion logic apply unchanged.
- **Card J — edge classification UI** in the trace workspace (click edge → type),
  plus an "internal room (no envelope)" declaration that clears the Card E
  envelope exclusion (walkthrough finding #26).
- **Card K — glazing/shopfront** from elevations or reviewer entry, with
  orientation; unknown orientation stays an explicit exclusion.
- **Card L — site location → cited design day.** Typed suburb/state when the PDF
  has no address; replace the generic `au-preliminary-v3` day with a cited
  design day; keep the generic day only as a labelled fallback.
- Later: kitchen equipment from an equipment schedule (needs the equipment
  list), ventilation/exhaust for the kitchen.

Acceptance for step 4 overall: each card reports the Butcher Buffet total and
component breakdown before/after, against 39.6 kW. The step is done when the
draft lands in **35–50 kW** with envelope and the major air-side loads
assessed (not excluded) — landing in band while components are still excluded
does not count. Report whether the result includes the cold rooms, since
"entire building" may or may not. No claim of general accuracy from a single
case; Global Exchange (if it yields a reference) is the second check.

---

## Questions for the user

1. ~~Basis of the 39.6 kW~~ — answered 2026-10-03: entire building, acceptable
   range 35–50 kW; finer basis unknown.
2. ~~Single-storey with roof?~~ — answered 2026-10-03: yes, single-storey with a roof directly above, so Card I includes the roof for Butcher Buffet.
3. Which contractor could do the step-3 session, and with which drawing set?
