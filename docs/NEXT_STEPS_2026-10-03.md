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
| 3 | Contractor workflow session | User runs it; Claude prepares script + project, analyses notes | after 1a–1c merged |
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

Start after Card F merges (both touch `frontend/js/app.js`).

```
Goal: a reviewer can accept the room/zone/area set in one pass: one reviewer name, bulk accept per group, decisions that survive re-render, and a visible preview/apply summary.

Facts (walkthrough findings #9–#12):
- 80 proposals + 42 review items; each accepted candidate needs its own reviewer field inside collapsed <details> (calculatorDraftCandidateMarkup ~3278); backend rejects missing reviewer per candidate ("Reviewer attribution is required: <id>").
- Saved decisions are restored but reviewer/source inputs are re-rendered empty, so a second save fails.
- Preview result (data.preview) is stored in apply_summary but not visibly rendered; "Changes previewed" toast only. Apply reported "0 records created" with no reason (apply_summary.unresolved had it).
- Preview with unsaved changes fails with a generic "Could not update calculator draft."

Change (frontend unless noted):
1. One "Reviewer" + "Review source" field at the top of the draft panel; on save, fill each accepted/edited decision's reviewer/source from it unless the candidate has its own value. Backend validation unchanged (every decision still carries attribution).
2. "Accept all in this group" button per draft group (zones, rooms, directly supported room inputs, …); it only sets decisions to accept, never edit.
3. Re-render saved reviewer/source/edited field values from savedDecision.
4. Render the preview and apply summaries in a visible block next to the buttons: counts of created / populated / unresolved / conflicts, and the first unresolved reasons in plain words (e.g. "Zone Bar: needs a reviewed floor").
5. Preview with unsaved changes: auto-save first, or show "Save the review before previewing" inline (not only in a toast).

Files: frontend/js/app.js, frontend/index.html (if a container is needed), frontend/tests/app.spec.mjs.
Tests (Playwright, mocked API): session reviewer fills all accepted decisions; group accept sets only that group; saved decisions re-render with reviewer; apply with unresolved items shows the reasons; preview with unsaved changes shows the inline message. Existing tests pass.
Verify: on the walkthrough project, accept the 15 zone/room/area candidates with one reviewer entry and one group-accept per group; report the number of clicks/fields needed vs. the original 15 reviewer fields.
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
