# End-to-end walkthrough — Global Exchange WSA (2026-10-03)

Purpose: test whether the Butcher Buffet fixes (Cards A–G) generalise to a
second, different project. Global Exchange is an international kiosk on
**Level 2 of Western Sydney Airport** (user-confirmed level; both sets are
permitted evaluation cases). Findings only — no code was changed.

Setup: run on commit `66fecb3` in an isolated git worktree with its own server
(port 8001) and its own `output/`, so Codex's in-progress Card H edits and the
main project list were not touched. Upload and analysis used the app's own
`/api/upload` and `/api/analyse` endpoints (the browser pane cannot attach
files); everything after that went through the UI, except one retry noted
below.

Two copies were run:
- **A — "Air Conditioning Design Full Set" (7 pages, mechanical engineer's set)**
- **B — "R2-025 Detail Design" (29 pages, architect's set + structural/electrical)**

## Result

| | Set A (mechanical) | Set B (architectural) |
|---|---|---|
| Rooms detected | 5, **all phantom** (from legend/notes text) | 11 records: 2 real (Office 9 m², Service Counter 13 m²), 9 phantom or duplicate |
| Traceable pages | **none** (mechanical plans are not accepted by the trace tool) | GA plan p.5 |
| Resolver | 422 — asks the user to trace "SHOPFITTER", "KITCHEN EXCHANGE" etc. | 200 |
| Draft cooling result | none | **3.04 kW design** (2.76 kW raw, 1.85 kW sensible, peak 2 pm), `complete_scope: false` |
| Rooms in the draft | — | Office 9.0 m² (real); "Retail space including a museum and Front of house" 27.9 m² (**phantom**); "area objects" 9.6 m² (**phantom**) |
| Real room left out | — | **Service Counter 13 m²** (room-use scope "unresolved") |

**Candidate reference (needs user confirmation before use):** Set A page 7
(GE-M410, Equipment Schedule) prints the engineer's design for the single fan
coil unit serving the kiosk: **FCU-01 total cooling 5.58 kW, sensible 5.31 kW**,
heating 1.13 kW, supply air 500 L/s, outside air 50 L/s. The architect's GA
plan gives the tenancy as **A = 40.8 m²**.

Archie's 3.04 kW is ~46% below 5.58 kW, but the two are **not comparable**: the
draft is built mostly from phantom rooms and omits the real service counter,
the envelope and air-side items, uses default equipment, and generic weather.
The total area in the draft (46.5 m²) looks plausible against the real 40.8 m²
only by coincidence — this is the most dangerous failure seen so far: a
plausible-looking number from the wrong rooms.

## Timeline

| Step | Set A | Set B |
|---|---|---|
| Upload + analysis | ~15 s | ~30 s |
| Confirm drawings (incl. no-AI workspace, Card F) | ~1 s | ~80 s |
| Guided resolve | 422 dead end | first click failed (see #9); after retry ~20 s |
| Calculate draft | — | ~20 s |

Set B reached a number in about 3 minutes of active time with **no tracing and
no draft review** — fast, but from the wrong rooms (see #1).

## What generalised from Cards A–G

- **Card F** (no ChatGPT paste): worked on both sets.
- **Card G** (assumed floor): correctly **not** applied — "Level 2" was found
  in both sets.
- **Card A/D gate**: correctly refused to produce a number from Set A's
  area-less phantom rooms.
- **Card E**: the Set B draft reports `complete_scope: false` with envelope and
  air-side items listed as not assessed.

## Findings

Severity: **B** = blocks or produces a misleading result, **H** = high
friction, **M** = confusing, **L** = polish.

| # | Sev | Finding |
|---|---|---|
| 1 | **B** | **Room detection reads single words from notes, legends and detail callouts as rooms.** Set A: "BUILDING EXISTING KITCHEN", "KITCHEN EXCHANGE" (legend: "base building existing kitchen exchange air ductwork"), "SHOPFITTER"/"FITOUT SHOPFITTER" (note: "coordinate with the fitout shopfitter"). Set B: "Bar" (cabinet detail "3mm FLAT BAR", p.13), "Shop" ("shop drawings" in general notes, p.5), "MANUFACTURE. SHOPFITTER", "SHOPFITTER CLEAN". |
| 2 | **B** | **Text from render/electrical pages becomes rooms with areas**, and those areas count as validated: "Retail space including a museum and Front of house · 27.9 m²" and "area objects · 9.6 m²" (p.25, classified render/photo), "Plant room · 9.6 m²" (p.27, electrical). Two of them are calculated in the draft. |
| 3 | **B** | **The real main space is silently excluded**: "Service Counter" (13 m², printed on the GA plan) gets room-use scope `unresolved_scope`, so it is left out of the draft while phantom rooms are included. |
| 4 | **B** | **Mechanical-only drawing sets cannot be traced.** The trace tool only accepts pages with role main/primary/supporting floor plan; Set A's 1:50 mechanical plans are `existing_hvac_plan` → "No supported rooms or rendered plan pages are available." |
| 5 | H | Duplicate room records: Office and Service Counter appear on "Level 2" (with areas) and again on "Unassigned level". |
| 6 | H | The draft does not show which rooms (and which source pages) make up the number, so a contractor cannot see that "Retail space including a museum…" is not a room in their kiosk. |
| 7 | M | Page-role errors: Set A cover sheet classified "Mechanical Services Plan", legend/notes page "Reflected Ceiling Plan"; Set B p.13 (cabinet details) classified "Dimensioned Top View Plan", p.16 "Shop Floor Plan" (structural set-out with the kiosk dimensions) not marked relevant. |
| 8 | M | Set A summary says "Archie found the core drawing context" although the set has no architectural floor plan. Analysis summary reports 7 relevant pages while 4 of 7 sheets are flagged not relevant. |
| 9 | M | Clicking guided "Resolve model inputs" right after confirmation failed with a generic "Resolver could not complete" because room detection was still running (`POST /api/skill-workflow` → 400). A retry seconds later worked. The UI should wait or say "room detection is still running". |
| 10 | M | Schedules on the drawings are not used: Set A's FCU schedule (5.58 kW, 50 L/s OA) and Set B's luminaire schedules (pp.24, 27) carry exactly the values the draft defaults. |
| 11 | L | Set A Level 2 purpose inferred as "plant / services" (from a mechanical plan title). |

## Implications for the plan

1. **Room identity quality is now the biggest risk**, ahead of the envelope.
   Butcher Buffet hid it because its plan had no room labels at all (we traced
   everything). On a labelled set, Archie produces a number from phantom rooms
   with no reviewer step. This should be fixed before the contractor session —
   a contractor would see a confident-looking wrong answer.
2. The envelope work (step 4) should treat **internal tenancies** as a
   first-class case: this kiosk is inside a terminal, so its envelope is mostly
   conditioned-to-conditioned. The "internal room" declaration (Butcher Buffet
   finding #26) matters here.
3. Drawing **schedules** (equipment, FCU, luminaire) are a strong, cheap source
   of real values and fit the "AI infers values from the PDF" goal.

Proposed cards (to be written in full after the user confirms priorities):

- **Card M — room labels only from plan views.** Accept a room label only when
  it sits inside a floor-plan/RCP view region and is not part of a note,
  legend, title block, schedule or detail callout; never from single generic
  words ("bar", "shop") without a room number or area tag. Tests from both
  sets' phantom examples.
- **Card N — reviewer confirms the room list before a draft number.** Show the
  draft's rooms with source page and area; rooms not confirmed by a reviewer
  are excluded (or clearly flagged) and "unresolved scope" rooms like Service
  Counter are surfaced for a scope choice instead of silently dropped.
- **Card O — allow tracing on any scaled plan page**, including mechanical
  plans, when no architectural plan exists.
- Later: read FCU/equipment and luminaire schedules as cited provisional values.

## Cleanup state

- Isolated worktree `scratchpad/ge-walkthrough` (detached at `66fecb3`) and its
  server on port 8001 are still running; both Global Exchange copies live only
  in that worktree's `output/` — the main `output/web_projects.json` was not
  touched.
