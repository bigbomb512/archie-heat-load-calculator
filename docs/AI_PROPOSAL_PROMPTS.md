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

The largest manual step today (tracing took about 19 minutes for the first
room) and the largest gap in the baseline.

**Method (deterministic first, AI where judgement is needed):**
1. **Scale:** pick a printed dimension string on the plan and the vector line
   it annotates. mm/px from the printed value must agree with the declared
   scale within 2 % (the existing calibration rule; never scale-only).
2. **Candidate regions:** close the vector wall lines into regions
   (polygonise), and keep regions that contain one detected room label.
3. **AI task:** only where geometry is ambiguous: a label in no region, two
   labels in one region, an open plan without walls between areas, or a
   raster-only page. One crop of the ambiguous area with candidate outlines
   numbered. The AI chooses the outline for each label, or splits an open area
   along a drawn line (bulkhead, floor-finish change, joinery), citing it.
4. **Area** = calibrated polygon area.

**Cross-checks:**
- room areas sum to ≤ the tenancy outline area;
- the area matches any printed room area within 5 %;
- no room overlaps another.

**Fallback:** none for area. A room with no settled outline stays out of the
total and is listed, as today.

**Answer key caseA:** Bar 30.78, Kitchen 98.94, Shop 229.06, Coolroom 10.66,
Freezer 7.67 m² (±5 %).

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

**Fallback (most likely for the building type):**
- ground-floor tenancy (unit prefix "G") in a named centre, precinct or
  mixed-use building → `not_exposed`;
- a stand-alone single-storey shop or a top-level tenancy → `exposed`.

**Answer key caseA:** not exposed for Bar, Kitchen and Shop (apartments above;
confirmed by the project contact). The fallback reaches it from "Shop G38" +
"Melrose Central"; the plans alone do not show it.

---

## Fallback table (to review with the user)

| Value | Evidence missing | Applied value | Shown in result |
|---|---|---|---|
| Wall beyond a perimeter edge | Nothing drawn or noted | adjacent tenancy (storefront edge: mall or external, per P3) | "Assumed: typical for a tenancy in a centre" |
| Roof exposure | No section or level note | per P5 rule | "Assumed from building type" |
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
