# End-to-end walkthrough — Butcher Buffet (2026-10-02, updated 2026-10-03)

Purpose: take a copy of the Butcher Buffet drawing set from upload to a cooling
result in the real web UI, as a first-time contractor would, and record every
blocker. The walkthrough itself changed no code; the blockers it found were then
fixed as Cards A–E (below) and the run was resumed on the same project. All room
boundaries, design conditions and the floor are **walkthrough assumptions**,
not reviewed engineering inputs; the reviewer name used everywhere was
"Claude walkthrough (not a real review)".

Project: `walkthrough-butcher-buffet-_not-for-design_-1790926231`
(still present under `output/web_review/`; `output/web_projects.json` backup is
in the session scratchpad).

## Result

**The path now reaches a labelled draft cooling result: 33.8 kW design total**
(30.75 kW raw coincident, 20.2 kW sensible / 10.5 kW latent, peak at 2 pm,
safety factor 1.1). The report is labelled "AI preliminary estimate — not
engineering reviewed or validated" and reports `complete_scope: false`.

| Component at peak | kW |
|---|---|
| People | 8.3 |
| Outside air | ~8 |
| Lighting | 4.1 |
| Equipment (preliminary default) | 3.2 |
| Envelope | **0.0 — not assessed** |

**This number understates the load and must not be quoted as a design value:**

- **Not included** (listed under the total in the UI, for Bar, Kitchen and
  Shop): envelope, infiltration, extract air, make-up air, minimum supply air,
  spill air, transfer air, vapour gain, steam gain, process latent load.
- **Weather is generic**, not site-specific: the AI preliminary Australian
  cooling day from assumption pack `au-preliminary-v3` (peak 36 °C dry bulb,
  35 °C / 24 °C wet bulb at 2 pm). The site location was never resolved (the
  PDF has no address; the city was not confirmed).
- **Kitchen equipment is a preliminary default** (3.2 kW total) — implausibly
  low for a commercial kitchen; no equipment schedule was available (Option B).
- Room areas are approximate walkthrough traces; ceiling height, setpoints and
  the single floor are walkthrough assumptions.
- Coolroom and freezer are excluded as refrigeration/process spaces.

| Room | Traced area (m²) | Calibration | In draft result |
|---|---|---|---|
| Shop (dining) | 229.06 | agreed 0.22% on 11825 mm | yes |
| Kitchen (incl. rear corridor) | 98.94 | agreed 0.22% | yes |
| Bar | 30.78 | agreed 0.22% | yes |
| Coolroom | 10.66 | agreed 0.22% | no (refrigeration) |
| Freezer | 7.67 | agreed 0.22% | no (refrigeration) |

## Working path (as of commit `7f7bf48`)

1. Upload PDF → analysis → confirm drawings.
2. Unlock the reviewed workspace (still needs a pasted ChatGPT reply — see #4).
3. Trace and calibrate each room on the dimension plan; save each trace.
4. Build the calculator draft; accept zone, room and area candidates with a
   reviewer name; edit zones/rooms to the manually added floor; save, preview,
   apply.
5. Save design inputs; add the floor in the hourly hierarchy (behind "Show all
   tools").
6. Guided "Resolve model inputs" → continues past a stale workflow, passes the
   area gate using the traces, hydrates the schedule library and design-day
   weather from the preliminary input set.
7. "Calculate draft load" (inside "Advanced AI, source, and evidence controls")
   → draft report with the exclusions listed under the total.

## Fixes made after the walkthrough

| Card | Commit | What changed | Findings resolved |
|---|---|---|---|
| A | `a94477e`, `0dfb0a1` | Model-input area gate counts current, calibrated reviewer traces and draft-applied areas linked to a current trace (matched by room identity, not floor name). Remediation text no longer says "do not use the drawing scale". | #1, #18 |
| B | `0dfb0a1` | Missing `artifact_snapshot` import; airflow/AHU/plant/safety-factor GET **and POST** routes pass the web module instead of the request handler. API test covers every resolution endpoint with and without saved artifacts. | #6, #7 |
| C | `ac81f10` | Guided resolver warns and continues when the skill workflow is stale/failed/blocked; aborts only when the workflow can't start or times out. | #3 |
| D | `d564576` | Shared `current_traced_areas` helper; the preliminary assembler fills missing room areas from current calibrated traces (provisional, cited), skips conflicting traces, tolerates a malformed trace file, and strips provider-supplied trace provenance. | #5 (schedules/weather now hydrate); #2 no longer blocks |
| E | `d564576`, `7f7bf48` | Draft no longer defaults to "envelope not applicable" or "confirmed absent" air-side components; report lists unassessed items, sets `complete_scope: false`, warns that the total understates the load only when something is unassessed; UI groups exclusions by component with room names. | Previously hidden scope gap |

Verification for each card: unit/API tests, full `npm test` (32 Playwright
tests at `7f7bf48`), and a re-run on this project in the real UI.

## Timeline (active time, original run)

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
| After fixes: guided resolve + calculate | ~2 min (resolver ~90 s) |

A human contractor without scripted keyboard input would be slower on the
traces. Roughly 1.5–2 hours of active time is far too long for a first user;
most of it is tracing and draft review (#9–#15).

## Friction and bug log

Severity: **B** = blocks the path, **H** = high friction / likely abandon,
**M** = confusing, **L** = polish. "Bug" = behaviour contradicts the code's own
intent.

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | **B, bug** | Reviewer traces never satisfied the model-input resolver's area gate; the resolver's own remediation told the user to trace rooms. | **Fixed (A)** |
| 2 | **B** | Local room-inference proposal (null areas) shadows any manual placeholder proposal, so there is no manual area route. | Open, no longer blocking (D uses traces) |
| 3 | **B** | Guided "Resolve model inputs" aborted on a stale skill workflow, which every draft edit causes. | **Fixed (C)** |
| 4 | **B** | Whole reviewed workspace is hidden until a ChatGPT reply is pasted; "Open geometry editor"/"Open reviewed workflow" dead-end. Workaround: paste `{}` under Advanced recovery (two collapsed sections deep). | Open |
| 5 | **B** | Preliminary calculation needed a schedule library + design-day weather that nothing could create. | **Fixed (A+C+D)** — weather is the generic preliminary day, not site weather |
| 6 | **B, bug** | `GET /api/hourly-load-model` crashed (missing `artifact_snapshot` import). | **Fixed (B)** |
| 7 | **B, bug** | Airflow/AHU/plant/safety-factor endpoints failed with "'Handler' object has no attribute 'safe_link'/'update_project'". | **Fixed (B)** |
| 8 | **B** | Calculator draft cannot apply zones/rooms without a floor; this set states no level, so no floor candidate exists. "Add floor" is hidden behind "Show all tools" and saving it requires design inputs first. | Open (worked around manually) |
| 9 | H | Calculator draft presents 122 decisions (80 proposals + 42 review items) with no bulk accept. | Open |
| 10 | H | Each accepted candidate needs its own reviewer name; each edited candidate also needs an "Engineer review source"; both inside collapsed `<details>`. Errors report one candidate at a time. | Open |
| 11 | H | Saved reviewer names are not re-displayed after re-render, so a second save fails until all are re-entered. | Open |
| 12 | H | Preview without saving fails with only "Could not update calculator draft"; preview output is not visibly rendered; Apply reported "0 records created" with no reason (the missing floor). | Open |
| 13 | H | Draft panel ~25,000 px down one page; 22 "Resolve/Build" buttons visible at once; three navigation schemes; "Calculate draft load" hidden in a collapsed advanced section. | Open |
| 14 | H | Tracing: switching room silently clears trace, calibration and page; calibration re-entered per room; keyboard focus lost after every click (~12 Tabs back to the plan); click precision ±80 mm. | Open |
| 15 | H | After saving a trace the room dropdown still says "area unresolved" and the panel says "No saved trace". | Open |
| 16 | M | Plan has no room labels; mapping rooms needed the RCP (p.22). RCP ceiling heights (CH 2600/2900/3150, dining to slab ~3750) and lighting legend are not used automatically. | Open |
| 17 | M | Design-inputs form starts empty although the app has preliminary profiles; ceiling height still reported "missing" after entering 2900 + provisional. | Open |
| 18 | M | Area-gate remediation said "Do not use the drawing scale", contradicting trace calibration. | **Fixed (A)** |
| 19 | M | Header shows "Rooms 0" while the workflow shows "Rooms 5/5"; review list only shows low-confidence detail pages, not the key plans. | Open |
| 20 | M | Three competing "next actions" after confirmation; "Confirm selected drawings" does not show the selection. | Open |
| 21 | L, bug | "Selection confirmed" toast renders raw `<a>` HTML as text. | Open |
| 22 | L | At 800 px width the "Guided next action" card collapses to one word per line with the button overlapping. | Open |
| 23 | L | UI is branded "Toki"; product is Archie. | Open |
| 24 | L | After the placeholder paste the UI did not refresh until manual reload. | Open |
| 25 | L | Draft headline shows "33.8286 kW" (four decimals). | Open |
| 26 | L | No way to declare a room internal (no envelope) in the draft path, so "Envelope — not assessed" can't be cleared for a genuinely internal room. | Open |

## Prioritised backlog (re-ranked 2026-10-03)

Ordered by what most reduces time-to-a-trustworthy-draft for a first real user.

1. **Remove the ChatGPT-paste gate on the reviewed workspace** (#4). A new user
   cannot reach tracing at all without a hidden workaround.
2. **Single-level default floor** (#8). Propose one provisional floor candidate
   when no level is stated, so zones/rooms apply without the hidden "Add floor"
   detour.
3. **Draft review ergonomics** (#9–#12): one reviewer field per review session,
   bulk accept per group, persist/re-display saved decisions, show the preview
   summary next to the buttons, explain "0 records created".
4. **Surface "Calculate draft load"** in the guided flow after a successful
   resolve, instead of inside the collapsed advanced section (#13).
5. **Envelope in the draft** (biggest accuracy gap — the result has 0 kW
   envelope). Start with opaque walls/roof from traced boundaries + provisional
   constructions, clearly labelled; then glazing.
6. **Site location → design weather.** Accept a typed suburb/state when the PDF
   has no address, and replace the generic preliminary day with a cited design
   day.
7. **Trace ergonomics** (#14, #15): keep calibration per page across rooms, warn
   before clearing an unsaved trace, keep focus on the plan, refresh room
   status after save.
8. **Use RCP ceiling heights and lighting legend** as cited provisional values
   (#16; fits the "AI infers values from the PDF" goal).
9. Polish: #17, #19–#26.

## Not done / follow-ups

- No reviewed (non-preliminary) hourly cooling report was produced; only the AI
  preliminary draft.
- The "Set up Archie AI estimate" fast path with a real provider and the
  ChatGPT-vision route (a real pasted reply with AI-estimated areas) were not
  exercised, to avoid spending the user's ChatGPT allowance.
- Walkthrough project and upload are still on disk; `output/web_projects.json`
  has not been restored.
