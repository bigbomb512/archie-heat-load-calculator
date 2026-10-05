# Card P — autonomous AI determination of heat-load inputs (design, 2026-10-04)

Goal: Archie's AI determines, **by itself**, the inputs a reviewer now declares
by hand (room outlines and areas, site, north, wall boundaries, roof exposure,
shopfront glazing) and applies them to the draft load. No contractor
confirmation step is required. The declaration screens built in Cards J/K/L
become optional overrides and an audit view.

## Ground rules (user decisions, 2026-10-04 unless noted)

1. **Applied directly, labelled.** Every AI value enters the calculation with
   its source marked "AI-determined", its evidence (page, printed text, crop)
   and a confidence. A reviewer may override any value. The override wins and
   is recorded as theirs.
2. **Measured before trusted.** A task may auto-apply only after it reaches
   **≥ 85 % correct** on the answer-key set
   (`tools/evaluate_autonomous_tasks.py`). The bar will be raised later. Below
   the bar, the task still runs, but its values are labelled "AI-determined
   (below accuracy bar)" in the result.
3. **Most likely case when the drawings don't say.** If the evidence does not
   settle a value, apply the most likely case for the building type
   (fallback table below), and say so in the result. Do not use the
   conservative case. The one exception is north: with no north arrow there is
   no likely value, so façade sun stays "not assessed".
4. **Small, targeted prompts** with a hard size budget per task; block with a
   reason rather than trimming evidence silently (decision 2026-10-02).
5. **Cross-check before applying.** Wherever the drawings allow, an AI answer
   is checked against an independent deterministic fact (vector geometry,
   printed dimensions, scale). Agreement → apply. Disagreement → one retry with
   the conflict stated, then the fallback, with the conflict reported.
6. **Transport:** the manual ChatGPT route for development (the operator
   pastes, never the contractor); a hosted model later, changing only the
   transport.

Answer keys: `evaluations/autonomous/<case>.json` (anonymised; identifying
facts such as the site live in ignored `output/evaluations/private/`).

**Baseline 2026-10-04 (caseA, no human input):** site 1/1; rooms 0/5 (named,
but no areas); north 0/1; storefront glazing 0/1; roof 0/3.

---

## P0 `room_geometry`: room outlines, areas and scale

The largest manual step today and the largest gap in the baseline. Code:
`ai/room_outline.py`; developer tool for the manual ChatGPT route:
`tools/run_room_outline.py` (packets → replies → `determinations.json`).

**Area source, in order of trust:**
1. a room area printed on the drawing (GE prints "Office 9 m²"; room detection
   already reads these);
2. an area fully enclosed by walls;
3. an AI outline snapped onto the drawing's lines (open plans).

A computed area that disagrees with a printed one by more than 5 % is
reported.

**Steps (findings from Butcher Buffet and Global Exchange, 2026-10-05):**
1. **Scale (vision task).** Dimension lines are found geometrically: a long
   thin line with ticks at both ends, measured between the tick centres (the
   line overshoots its ticks). The AI reads the printed number on a crop. The
   resulting mm/px must agree with the declared scale within 2 %; with no
   declared scale, two printed dimensions must agree with each other.

   On Butcher Buffet the numbers are drawn as vector outlines (no text
   layer), so only a vision read works. On GE the text layer has rotated
   numbers with their digits reversed ("6293" for 3926).

   Result: 11,825 mm → 14.108 mm/px, 0.02 % from 1:100.
2. **Hatch removal (automatic).** Styles drawn as many long parallel lines at
   a regular spacing (floor tiles) are dropped. This is what was chopping
   every room into tiles.
3. **Wall styles (vision task).** The plan is redrawn with each style in its
   own colour plus a numbered legend; the AI names the wall styles. Rules by
   colour or shape failed: one drawing mixes walls and furniture in the same
   style.

   On Butcher Buffet the answer an AI would naturally give (fills, heavy
   outlines, dark-grey outlines) separates the rooms. Wrongly choosing the
   white-fill style (mostly furniture tops) breaks it.
4. **Enclosed areas (automatic).** Door gaps up to 1 m are closed; areas not
   touching the plan edge are kept, with furniture islands filled.
5. **Naming and splits (vision task).** The enclosed areas are numbered on
   the plan image; the AI names each from what is drawn in it (equipment,
   tables, cold-room doors), merges areas belonging to one room, marks "not a
   room", or sketches a split along a drawn feature, which is snapped to the
   real lines.

   Room names cannot be placed by position: on Butcher Buffet they are in
   notes and legends, not inside the rooms.
6. **Outlines for open plans (vision task).** Rooms still without an area are
   sketched by the AI and each edge is snapped to the nearest parallel drawn
   line; corners are rebuilt where snapped edges meet.

   Simulated sketches with 10–20 px error come out within 2–4 % on large
   rooms. Snapping hurts small rooms (9–14 %), which is why enclosed areas are
   preferred.

**Raster plans.** GE's general-arrangement plan is a raster image (21 image
tiles); the vectors hold only grid, dimensions and tags. Walls can be taken
from the rendered image instead (dark, thick areas after removing thin
lines). The GE kiosk is open-plan with no walls on most sides, so it relies on
printed areas (already read) or AI outlines.

**Answer key status (caseA):** Coolroom, Freezer, Kitchen and Bar are keyed;
the Bar was confirmed by the user on 2026-10-05 as the walled central island
(32.56 m²). Shop is **pending the user**: the walkthrough trace drew the wrong
area for Bar. Sheet 303 ("customised bar", "bar partition") is called up
from the walled central island (about 32.6 m²), and the top-middle area looks
like buffet stations (sheet 403).

**Stand-in run (Claude's own answers, not a model reply):** Bar 32.56,
Coolroom 10.76, Freezer 7.88, Kitchen 103.85, Shop 216.1 m². Scored keys 3/3;
Kitchen is +4.96 %, mostly a 0.8 m servery strip that the trace gave to the
Shop.

## P1 `site_identification` (text only, ≤ 4,000 characters)

Inputs: the title-block and notes excerpts already found by
`site_location_resolution.infer_pdf_context` (addresses, excluded addresses,
site names), each with its page.

> Here are short excerpts from the title blocks and notes of one drawing set,
> each with its page number. Decide which excerpt names the **project site**
> (a street address, or a tenancy in a named centre or building), and which
> addresses belong to **consultants** (architect, engineer, builder offices).
> Use only the excerpts given; do not add a suburb, postcode or state that is
> not printed. If no excerpt names the site, return `"site": null`.
> JSON only: `{"site": {"text": string, "page": int, "kind": "street_address"|"tenancy_in_centre"} | null,
> "consultant_addresses": [{"text": string, "page": int, "why": string}]}`

**Cross-check:** `site.text` is a substring of a supplied excerpt; it agrees
with the rule-based top candidate, or the disagreement is reported.

**Applied as:** the project site name/address. Coordinates still need G-NAF
or a cited position. A later task may geocode the site text once G-NAF is
configured.

**Answer key caseA:** private file (site contains the centre name; must not
be the architect's Pacific Hwy office).

## P2 `north_arrow` (≤ 3 crops per page, ≤ 1,024 px each)

Inputs: crops of the plan page's title block and corners, plus nearby text
items reading "N", "NORTH" or "TN".

> These images are crops of one architectural plan page. Find the **north
> arrow** (an arrow, a needle in a circle, or an N with a pointer). Give the
> pixel position of its **tail** and **tip** in the crop where you see it. If
> there is no north arrow, or you cannot tell which end is the tip, return
> `"found": false`. Do not use the sheet orientation, text direction or
> building shape.
> JSON only: `{"found": bool, "crop_index": int|null, "tail_px": [x,y]|null, "tip_px": [x,y]|null,
> "labelled_north": bool, "description": string}`

**Cross-check:**
- all plan pages of the same building should agree within 5° (they are usually
  drawn the same way up);
- disagreement → retry once, then apply the majority and report it.

**Applied as:** `declare_north` with source "AI-determined north arrow".
**Fallback:** none (façade sun not assessed).
**Answer key caseA:** p. 20 → 0° (unlabelled needle, title block).

## P3 `boundary_classification` (one room per prompt, 1 crop ≤ 1,536 px, ≤ 1,500 characters)

Inputs:
- a plan crop around the room, with its outline drawn and edges numbered;
- the edge list (number, length);
- nearby text items (e.g. "NEIGHBOURING TENANCY", "SHOPFRONT", "MALL",
  "FIRE-RATED WALL");
- which edges are shared with the tenancy's other rooms (from P0 geometry).

> The image shows one room on an architectural floor plan. Its outline is
> drawn with numbered edges. For each edge, say what is on the **other side**
> of that wall as drawn: `external` (outdoors: street, laneway, open air),
> `mall` (an enclosed public mall or walkway), `adjacent_tenancy` (another
> shop or tenancy), `internal` (another room of this tenancy), or `unknown`.
> Base each answer on what is drawn or written near the edge, and quote it.
> JSON only: `{"edges": [{"index": int, "boundary": "external"|"mall"|"adjacent_tenancy"|"internal"|"unknown", "evidence": string}]}`

**Deterministic first:** edges shared with another traced room of the tenancy
are `internal` without asking.

**Cross-check:** the edge that matches the storefront elevation's printed
total width (P4, within 2 %) is the shopfront. It must not be classed
`adjacent_tenancy` or `internal`.

**Fallback (most likely for a tenancy in a centre):** the shopfront edge is
`mall` if the set shows an enclosed centre, otherwise `external`; other
unknown perimeter edges are `adjacent_tenancy`.

**Answer key caseA:** Shop storefront edge (11.97 m) — **pending the user**
(outdoors or indoor mall).

Note: `mall` is a new boundary class. The envelope method treats it like
`adjacent_tenancy` (no conduction in the preliminary method) until a mall
temperature method exists.

## P4 `storefront_openings` (one elevation crop ≤ 2,048 px, ≤ 2,000 characters)

Inputs: the elevation view crop, its dimension and level strings with
positions, the shopfront edge length, and the room ceiling height.

> The image is an architectural elevation of a shopfront. List each **glazed
> panel** (glass you can see through; not signage boards, solid panels, menu
> boards or open doorways). For each, give its **width** and its **sill** and
> **head** heights above finished floor, **only from dimensions printed on the
> drawing**, quoting the printed text used. Return `null` for a value not
> printed; do not scale from the image. Also give the total printed width.
> JSON only: `{"total_width_mm": number|null, "total_width_text": string|null,
> "panels": [{"label": string, "width_mm": number|null, "sill_mm": number|null, "head_mm": number|null,
> "printed_text": [string]}], "excluded": [{"label": string, "why": string}]}`

**Cross-checks:**
- every number appears in `printed_text`, and that text exists in the crop's
  text layer;
- the panel widths sum to ≤ the total;
- the total is within 2 % of the shopfront edge;
- head > sill; a head above the ceiling is capped at the ceiling.

**Fallback:** a missing sill → 0 (glass to floor, typical shopfront); a
missing head → the ceiling height; a missing width → the panel is not applied
and is listed.

**Answer key caseA:** 2,025 mm panel, sill 1,100, head 2,700 (capped); the
3,250 roller-shutter doorway and signage/menu panels are not glazing.

## P5 `roof_exposure` (≤ 2,000 characters, optional 1 crop)

Inputs:
- drawing-set facts: levels present, whether any section/RCP/notes mention
  "slab over", "roof over", "level above", "apartments"/"residences",
  "podium";
- the site name and kind from P1;
- the tenancy level (e.g. "G").

> From these drawing facts, is the roof directly above this tenancy exposed
> to the sky? Answer `exposed`, `not_exposed` or `unknown`, quoting the fact
> you used. Do not guess from the business type.
> JSON only: `{"roof": "exposed"|"not_exposed"|"unknown", "evidence": string}`

**No building-type fallback (user decision 2026-10-05).** Roof exposure is
applied only from explicit drawing evidence: a section, a level above, a slab
or roof note, or "apartments/residences" above the tenancy. If the drawings
don't settle it, **ask the contractor** one plain question ("Is there a floor
or another tenancy directly above this shop, or is it the roof?") and apply
their answer as the declaration. This is the one place Card P asks a person.

**Answer key caseA:** not exposed for Bar, Kitchen and Shop (apartments above;
confirmed by the project contact). The fallback reaches it from "Shop G38" +
"Melrose Central"; the plans alone do not show it.

---

## Fallback table (to review with the user)

| Value | Evidence missing | Applied value | Shown in result |
|---|---|---|---|
| Wall beyond a perimeter edge | Nothing drawn or noted | adjacent tenancy (storefront edge: mall or external, per P3) | "Assumed: typical for a tenancy in a centre" |
| Roof exposure | No section, level or slab note | none: ask the contractor (user decision 2026-10-05) | "Answered by the contractor" |
| Shopfront sill | Not printed | 0 mm | "Assumed glass to floor" |
| Shopfront head | Not printed | ceiling height | "Assumed glass to ceiling" |
| North | No north arrow | none (sun not assessed) | "North not found; façade sun not assessed" |

## Plumbing (Codex)

1. **Task runner:** P0–P5 in order (P0 → P3/P4 need geometry; P1 → P5), each
   with its budget. It re-runs a task only when its inputs change (fingerprint).
2. **Packet builders:** crops from the full-resolution renders; numbered
   outlines for P0/P3. Over budget → block with a reason.
3. **Transport:** manual ChatGPT batch for development. One packet per task
   per target, a paste box per packet, and a "Run all" summary of what is
   still waiting for a reply.
4. **Validators and cross-checks** per task, as above.
5. **Apply:** write the value through the existing declaration path
   (`set_cited_location`, `declare_north`, `classify_envelope` with openings,
   room traces) with `source: "ai_determined"` or `"ai_fallback"`, the task
   ID, the evidence and the reply hash. Reviewer overrides keep precedence.
6. **Result labels:** each component shows AI-determined, assumed (fallback)
   or reviewer-declared. Tasks below the accuracy bar say so.
7. **Determinations export:** write `determinations.json` in the shape scored
   by `ai/autonomous_task_scoring.py`, so every run can be scored.

## Evaluation

```bash
PYTHONPATH=. python3 tools/evaluate_autonomous_tasks.py evaluations/autonomous/caseA.json=path/to/determinations.json
```

Per task: correct, wrong or missing; accuracy; confident-wrong rate; how many
values came from the fallback; and whether it passes the 85 % bar. With only
three drawing sets (Butcher Buffet as caseA plus the two Global Exchange
sets), samples are small. The report flags fewer than 20 scored items per
task, and a pass on a small sample is not evidence of general accuracy.
