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

**Integration interfaces (for the Card P task framework, 2026-10-05):**
- `room_outline.simplify_outline(polygon, mm_per_px, jog_mm=150, max_area_change=0.01)`
  returns `(simplified_polygon, report)`.
  - Removes gap-closing artefacts and jogs or piers shallower than 150 mm,
    and merges near-collinear edges.
  - The area changes by at most 1 %; otherwise the original is returned and
    the report says so.
  - On Butcher Buffet: Kitchen 247 → 24 edges, Bar 75 → 9, dining 515 → 51,
    each under 1 % area change.
  - **Save the simplified outline as the trace shape, but keep the area
    measured on the original polygon** (`ai_measured_area_m2`).
- `room_outline.wall_runs(points, mm_per_px)` returns the wall runs of an
  outline.
  - **Grouping:** edges facing the same way, within 400 mm of one line and no
    more than 2 m apart along it, form one run; shorter edges (< 0.5 m) join
    their longer neighbour.
  - **Each run reports:** `run_index`, `edge_indices`, `direction_deg`,
    `length_m` (edges on the line) and `span_m` (end to end, what an
    elevation's overall dimension measures).
  - On Butcher Buffet the storefront is one run of 2 edges, span 11.769 m
    against the printed 11,900 (1.1 %).
  - **Use in P3:** ask about runs, numbering only runs with `span_m` ≥ 1 m on
    the crop; shorter runs take the answer of their nearest numbered
    neighbour, and a run's answer applies to all of its edges.
  - **Use in P4:** compare the elevation's printed total with the storefront
    run's `span_m` (2 %).

**Raster plans.** GE's general-arrangement plan is a raster image (21 image
tiles); the vectors hold only grid, dimensions and tags. Walls can be taken
from the rendered image instead (dark, thick areas after removing thin
lines). The GE kiosk is open-plan with no walls on most sides, so it relies on
printed areas (already read) or AI outlines.

**Answer key status (caseA):** Coolroom, Freezer, Kitchen and Bar are keyed;
the Bar was confirmed by the user on 2026-10-05 as the walled central island
(32.56 m²), and the Shop as the dining area including the top-middle buffet
stations (216.1 m², two enclosed areas merged). Both are measured from the
drawn walls, so for them the score tests naming and merging, not geometry.
The walkthrough trace had drawn the wrong area for Bar. Sheet 303 ("customised bar", "bar partition") is called up
from the walled central island (about 32.6 m²), and the top-middle area looks
like buffet stations (sheet 403).

**Stand-in run (Claude's own answers, not a model reply):** Bar 32.56,
Coolroom 10.76, Freezer 7.88, Kitchen 103.85, Shop 216.1 m². Scored keys 3/3;
Kitchen is +4.96 %, mostly a 0.8 m servery strip that the trace gave to the
Shop.

**Unlabelled rooms, levels and sheet choice (2026-10-23, Casa Nova).** Casa Nova's Phase 1 had 53 m² of small rooms on
a tenancy of about 250 m²: the main dining space has no printed name, so it never became a room. Four changes, each for
every job:
- **Room names from notes:** a room word whose own line continues with a fitting word ("STAFF LOCKERS / SHELVES",
  "KITCHEN EXHAUST", "SHOP FRONT") is a note, not a room label (`ai/room_inference.py`).
- **Rooms named by their use:**
  - **Naming step (`P0_room_names`):** names an area that none of the printed names fits from a fixed list (Dining,
    Shop, Kitchen, Bar, Office, Store, Toilet, Corridor; each maps to one room-use type). A new reply must answer
    every numbered area: a listed room, a use name or `not_a_room`. Saved replies from before this change are still
    read.
  - **Open-plan outlines (`P0_room_outlines`):** may also draw room-sized spaces that no listed name covers, named the
    same way.
  - **Room list:** these rooms are kept in `ai_named_rooms.json`, per step and page, and join it through
    `backend/room_proposal.py` (source `named_by_use`).
- **Levels:** a bare "L1" is a legend or fixture tag ("LED STRIP L3") unless the page title or the word level/floor
  names it (`ai/drawing_coverage.py`). Before this change, two tags made the one-floor shop three levels, each with its
  own main plan.
- **Sheet choice:** a page that page reading (pass 1) calls an elevation, section or detail is never a main plan, even
  if the drawing-set classifier calls it a floor plan.

**Effect on the saved Casa Nova inputs (no AI):**
- **Main plans:** pages 32, 33 and 46 became page 33 only, the vector setout plan of the whole floor.
- **Room names:** Bar, Bathroom/Level 3, Kitchen/Level 1 and Staff became Bar, Bathroom and Kitchen on one level.
- **Butcher Buffet:** unchanged (page 20; the same five rooms).
- **Global Exchange:** not compared; no saved job has the needed files.

**Plan window and floor with no room (2026-10-24, Casa Nova).** The first run with these fixes still traced no dining
area, for two reasons:
- The room-finding window, taken from the densest cluster of drawing, cut off the right fifth of the long shop plan.
  Areas touching the window edge are never rooms, and the outline image was cropped to the window. The window now grows
  over every drawn line of 1 m or more that runs past it (`room_outline.grow_plan_viewport`; Butcher Buffet is
  unchanged).
- The prompt line asking the outline step for unnamed spaces was ignored.

There is now a separate step, **`P0_unnamed_space`** ("Checking for floor with no room"):
- **When:** once per main plan, after the naming and outline steps are answered.
- **What it asks:** it shows the plan with the traced rooms tinted and named, and asks for any untinted room-sized floor
  inside the tenancy, named by its use, or none.
- **Checks:** a use name that isn't already a traced room; at least 4 m² once the traced rooms it overlaps are removed.
- **Saving:** these rooms are saved like the other rooms named by their use.

**Repairs after the third Casa Nova run (2026-10-24):**
- **Self-crossing outlines:** an AI outline that crosses itself, usually one drawn around an inner room, is split where
  it crosses and kept as parts of the room (`room_outline.outline_parts`). It used to be refused.
- **Unanswered floor-with-no-room check:** the model check treats it as a first-priority gap.
- **Model-check notes:** worded as what to look at, never as a finding. An AI read "no applied shopfront glass" as a
  fact and excluded the whole shopfront.
- **Glass questions:** fitted to their 2,000-character budget instead of being blocked.
- **New step, `P0_tenancy_outline`** ("Outlining the tenancy"):
  - **When:** asked once per main plan as soon as its scale is known; the outline steps wait for it.
  - **What it asks:** the floor inside the shopfront glazing and the outer walls.
  - **Clipping:** AI-drawn room outlines are clipped to it, with a 300 mm allowance, so a room can't spill onto the
    paving, a mall or a neighbour (`room_outline.clip_to_tenancy`).
  - **Door detection was dropped:** finding doors from their swing arcs was tried first. On Casa Nova the door swings
    are drawn as many short segments, not arcs, and the dining floor still leaked somewhere else.

**Corner picking (`P0_outline_corners`, 2026-10-24).** Freehand outlines in pixel coordinates drifted: on Casa Nova
the tenancy sketch ran over the paving, cut across the curved shopfront and dipped below the back wall, and clipping
to it removed about 40 m² of real dining floor. Every AI sketch (the tenancy, open-plan room outlines, floor with no
room) is now redrawn from the drawing's own corners.
- **Corners** (`room_outline.corner_candidates`), merged within 300 mm:
  - **Straight lines:** the ends of wall segments of 0.3 m or more, and of other non-hatch segments of 1 m or more (the
    shopfront glazing is often not a wall style).
  - **Curves:** points about every metre along curves, including curves drawn as many separate short pieces, which
    are joined into chains first. Casa Nova's curved shopfront is 0.2 m pieces in 1.2 m runs between mullions.
  - **Left out:** curves under 1.2 m across (columns, heaters, door swings).
  - **Thinning:** above 80 corners, longer straight segments and wider merging are required, but curves always keep
    their 1 m minimum.
- **The question:**
  - **Image:** the region around the sketch plus 3 m (the whole plan for the tenancy), with the corners numbered and
    the sketch drawn faintly.
  - **Answer:** the AI lists the corner numbers in order.
  - **Check:** the outline is built from those exact corners, repaired if it crosses itself, and asked again if its
    area is off from the sketch by more than 40%.
- **Order:** the tenancy is asked first. Rooms wait for it, then are clipped to it and have rooms inside them removed
  (all traced rooms, for floor with no room).
- **Clipping:** only a corner-built tenancy clips rooms.
- **Image-only plans:** have no drawn corners and keep their sketches.
- **Previews (no AI):**
  - Casa Nova page 33: 82 corners for the tenancy and 62 for the Dining area, readable, with points along both
    curved shopfront ends and the bar arch.
  - Butcher Buffet page 20: 152 corners, as table edges add many; still readable, with every outer wall corner
    numbered. Its rooms are enclosed by walls, so there only the tenancy is asked.

**Model for outlines (2026-10-24).** The first corner-picking run on Casa Nova (Gemini 3.8 Flash at low reasoning)
gave unusable corner lists: two or three numbers, or outlines enclosing 3–11% of the room. On the same saved
questions:
- **Claude Sonnet 5.5** traced the tenancy along its walls and shopfront, 220 m², at about US$0.01 a question in
  under 10 s.
- **Gemini Pro** gave a similar outline at up to US$0.11 and 140 s.

The four outline steps (`P0_tenancy_outline`, `P0_room_outlines`, `P0_unnamed_space`, `P0_outline_corners`) now go to
a new model step, `OUTLINES`; the other drawing tasks stay on Gemini 3.8 Flash. `OUTLINES` started on Sonnet 5.5 and
moved the same day, on cost, to **Claude Haiku 5.5**. On the same saved questions, Haiku traced the tenancy at 242.5 m²
(Sonnet 247.8) and named the rooms identically, for about a twelfth of the cost (US$0.0019 for both questions).
GPT-5.4 mini missed floor (188 m²) and misnamed rooms. Qwen 3.7 Plus had no host meeting the no-storage setting. Room
naming (`P0_room_names`) joined it the same day. On Casa Nova's saved naming question, Gemini named all three enclosed
areas wrong (the dining end "Kitchen", the kitchen strip "Bar"). Sonnet named the dining end and the kitchen strip
correctly, and called the strip's left part "Bar" (US$0.007).

Changes from that test:
- **No check against the sketch:** the corner answer isn't compared with the AI's sketch, whose 58 m² tenancy sketch
  refused good answers. A room may not exceed the corner-built tenancy.
- **Curve points in green:** points along a smoothly bending wall or glazing line (no turn over 25°, at least 8° in
  all) are drawn green, and the AI is told to follow them. Small loops and door swings (under 1.2 m across, closed or
  turning 45°+) are left out.
- **Room crops:** room questions show the area of the corner-built tenancy plus 1 m, not only their sketch.

With these, Sonnet traced Casa Nova's tenancy around the curved shopfront: 32 corners, 247.8 m², no missing sides,
US$0.018.

**Fewer AI calls (2026-10-24).** Casa Nova's run made about 90 calls (46 US cents):
- 22 north-arrow reads (66 images) for one traced plan;
- 22 equipment calls, 19 of the 43 pages being renders;
- 19 page-reading calls that wrote more thinking (135k tokens) than they read.

Changes:
- **North arrow:** asked only on the main traced plan page(s) (`_north_pages`); every plan page is asked only once
  none of those shows a north arrow. The cross-page check waits only for the pages asked, and applies north to every
  page asked or already answered.
- **Equipment reading:** skips pages that page reading calls renders or photos (`page_extraction_service.SKIPPED_PAGE_TYPES`).
  - **Casa Nova:** no item was lost.
  - **Butcher Buffet:** four render-only items were lost (a TV screen, two drink fridges, a menu board), all apparent
    repeats of scheduled items.
- **Page reading:** "low" thinking (DeepSeek V4.1 Flash accepts it on OpenRouter) and six pages per call instead of
  four.
- **Roof question:** not grouped. It is still asked per room, because the operator's roof-answer flow, the export and
  the model check are keyed by room, and the 3–7 calls cost under 1 US cent.

A code-measured tenancy outline was tried and rejected: Casa Nova's floor is open to the outside at the entry doors and
to the kitchen through the servery, so no closed outline comes out of its walls.

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

**Answer key caseA:** Shop storefront edge (11.97 m) → `mall` (confirmed by
the user 2026-10-05: it faces an enclosed mall/walkway).

Note: `mall` is a new boundary class. The envelope method treats it like
`adjacent_tenancy` (no conduction in the preliminary method) until a mall
temperature method exists.

## P4 `storefront_wall`: which outside wall is the storefront (plan + elevation images, ≤ 1,500 characters)

Added 2026-10-20. The storefront is never left out (user decision): when the elevation's printed overall width
matches exactly one outside or mall wall, code picks it; one outside wall settles it alone. Otherwise (Butcher Buffet:
the width is drawn as outlines, so it isn't in the text layer, and three outside walls fit) the AI gets the plan with
the outside walls numbered in red plus the storefront elevation, and says which numbered wall(s) each elevation shows,
with evidence (elevation tags, entry doors and glazing on the plan, the street or mall, gridlines, width). Every
perimeter wall of the cooled rooms is offered, not only those the wall-facing step called outside or mall (2026-10-21:
on Butcher Buffet it had classed the 11.9 m storefront wall "adjacent tenancy"). The AI also says whether the front
faces a street or a mall, and the chosen walls are set to outside or mall accordingly; the wall-facing step then can't
call them internal. "Not on this plan" is accepted only when the plan has no outside or mall wall (e.g. an upper level).

**Cross-checks:** the numbers are numbered walls; a shopfront/storefront elevation shows at least one; evidence is
given; an overall width it reads must match the chosen walls' combined length (2 % or up to 600 mm wider). A rejected
answer is sent back with the reason. Every room on the chosen wall(s) then gets a `storefront_openings` task and takes
the share of the glass along its own length of the wall (rooms sharing the wall aren't counted twice).

## P7 `ceiling_heights` (one page image with the height labels boxed, ≤ 2,000 characters)

Added 2026-10-20. Heights are read **by code** from the printed labels in the text layer: ceiling plans ("CH:2600MM",
"C.H. 2700", "CLG HT 2400", "U/S OF SLAB 3750MM"; rotated labels come out backwards and words split over separate
items are joined); elevations and sections ("2600AFFL"). The AI only says which boxed label (H1, H2…) is which
room's ceiling, or why the sheet doesn't show it; a room with two ceiling heights lists both with the share of its
floor under each (area-weighted height). Ceiling plans are asked first; elevations and sections only for the rooms
their labels don't cover. The answered heights go into the room list (`ai_preliminary_service.
_with_printed_ceiling_heights`), where the ceiling resolver takes them over the preliminary 2,700 mm (a typed height
still wins), and into the storefront glazing (glass to ceiling). Only a room no sheet labels keeps the labelled
preliminary height.

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
- a number quoted from the crop's text layer is labelled printed; one that isn't there (dimensions drawn as
  outlines, as on Butcher Buffet's storefront elevation) is labelled *read from the image* (2026-10-21);
- the total is within 2 % of the traced storefront wall (or up to 600 mm wider);
- when anything was read from the image, the glazed and excluded widths add up to the total within 5 %;
- the panel widths sum to ≤ the total;
- head > sill; a head above the ceiling is capped at the ceiling.

**Fallback:** a missing sill → 0 (glass to floor, typical shopfront); a
missing head → the ceiling height; a missing width → the panel is not applied
and is listed.

**Answer key caseA:** 2,025 mm panel, sill 1,100, head 2,700 (capped); the
3,250 roller-shutter doorway and signage/menu panels are not glazing.

**Awnings and verandahs (2026-10-25, same call):** the reply also gives
`"shade": {"kind": "awning|verandah|canopy|eave|balcony|none", "depth_mm": n|null, "underside_mm": n|null,
"full_width": bool, "printed_text": []}`: how far it projects from the wall (printed, or on a section or side
view on the sheet) and its underside above floor level. The shade's height above each panel's glass head is
underside − the read head. In the calculation the shaded part of the glass (and, for a full-width verandah or
canopy, of the wall) gets no direct sun, hour by hour from the sun angle (`ai/sun.py`); diffuse sun is kept. An
unprinted depth is reported but gives no shade; an unprinted underside takes a labelled 0.3 m above the glass. The
shade wording shortened the room left for the elevation's text layer from about 725 to about 570 characters within
the 2,000-character budget.

## P5 `roof_exposure` (≤ 3,000 characters, optional 1 crop)

Inputs:
- drawing-set facts (each repeated note kept once, at most two per term, eight in all): levels present, and
  any note on any sheet mentioning "slab over", "roof over", "level above", "floor above", "apartments"/"residences",
  "podium", "car park", "U/S of slab"/"underside of slab", "structure above", roof sheeting, metal deck, skylights;
- the site name and kind from P1;
- the tenancy level (e.g. "G").

> Is the roof directly above this tenancy's ceiling exposed to the sky (exposed), or is there a floor, another
> tenancy, apartments or a car park above it (not_exposed)? Decide from the drawing facts below and quote the fact
> you relied on, word for word. "U/S of slab" or "structure above" means a concrete slab over the ceiling: over a
> shop in a centre that is most often a floor or car park above, while roof sheeting, a metal deck or skylights mean
> the roof. Weigh all the facts (levels, apartments, the site). Answer unknown only if the facts point neither way.
> JSON only: `{"roof": "exposed"|"not_exposed"|"unknown", "evidence": string}`

The quoted evidence must appear in the facts (ignoring case and spacing).

**Most likely case, labelled (user decision 2026-10-15; replaces the 2026-10-05 "ask the contractor" rule).** When
the AI answers unknown, `roof_fallback` applies, labelled "Assumed (most likely case)" with its reason:
- levels, apartments, a podium, a car park or a floor above → not exposed;
- a concrete slab over the ceiling ("U/S of slab", "structure above") with no roof sheeting, metal deck or skylight
  → not exposed (a floor above);
- otherwise → exposed, as for a single-storey shop.

Nobody is asked; the operators can correct it on Walls & roof.

**Answer key caseA:** not exposed for Bar, Kitchen and Shop (apartments above;
confirmed by the project contact). The ceiling and lighting plans' "U/S OF SLAB 3750MM" / "STRUCTURE ABOVE" notes
reach it through the slab rule even when the AI answers unknown.

---

## Fallback table (to review with the user)

| Value | Evidence missing | Applied value | Shown in result |
|---|---|---|---|
| Wall beyond a perimeter edge | Nothing drawn or noted | adjacent tenancy (storefront edge: mall or external, per P3) | "Assumed: typical for a tenancy in a centre" |
| Roof exposure | No decisive section, level or slab note | most likely case from P5 (user decision 2026-10-15) | "Assumed (most likely case)" |
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

---

## P6 `kitchen_equipment` (Card Q, design; source decision pending)

**Goal:** replace the 3.2 kW kitchen default with a load built from the
equipment drawn on the plans: heat to space by appliance, plus rangehood
exhaust. It is labelled "Assumed from drawn equipment" until the real
equipment schedule arrives.

**Task (vision, two crops ≤ 1,536 px + text labels ≤ 2,000 characters):**
- crops of the kitchen on the plan and the kitchen elevations;
- the AI lists each appliance as `{type, count, page, quote_or_symbol, under_hood}`,
  with types from the fixed list in `ai/autonomous_task_scoring.KITCHEN_TYPES`;
- validators: types from the list only; every item quotes drawing text or
  names a symbol on a supplied crop.

**Answer key caseA:** keyed only from equipment named in text on p. 30:
- 1 rangehood canopy, 1 oven, 2 upright fridges, 4 under-bench fridges,
  1 ice machine;
- the island's burners, woks and fryers are plan symbols only, so they are
  "not keyed" (listing them is not penalised).

**Heat-output and exhaust sources (research 2026-10-05):**

| Source | Covers | Access |
|---|---|---|
| ASHRAE Handbook – Fundamentals, ch. 18 (tables built on research project RP-1362: 83 appliances, hooded and unhooded, sensible/radiant/latent) | Heat gain to space by appliance type and size | Licensed (purchase) |
| AIRAH DA09 (4th ed., 2022), Section 8 "Internal and system heat gains" | Internal gains incl. equipment; whether commercial-kitchen tables are included is unconfirmed | Licensed; user plans to obtain |
| AS 1668.2 (2024) | Australian minimum kitchen hood exhaust rates (prescriptive procedure by hood size, hood type and cooking type) | Licensed (Standards Australia) |
| EPRI CKV performance reports (1997), OSTI-listed ASHRAE paper (1998) | Single-appliance tests; paper abstract only | Licensed / no free full text |
| Websites reproducing ASHRAE tables | Copies of the licensed tables | Not authoritative; not used |

No free, authoritative source was found. **Decision (user, 2026-10-05):** wait
for AIRAH DA09 and check whether Section 8 covers commercial-kitchen
appliances; if not, fall back to ASHRAE Fundamentals ch. 18, plus AS 1668.2
for exhaust. Until then the kitchen heat stays at the labelled default.

**Identification task — built (2026-10-05):** `ai/kitchen_equipment.py`
(packet, prompt, validator, determinations) and the developer tool
`tools/run_kitchen_equipment.py` (packets → reply → `determinations.json`).
- Labels are the page's text joined into short lines, leaving out long notes,
  bare numbers and the title block.
- A quote may wrap over lines: every quoted word must be on the cited page.
- Sinks, basins, racks and benches are refused as giving no heat.
- On Butcher Buffet the packet is one plan crop of the kitchen plus the
  kitchen elevations (p. 30), with a 1,884-character prompt.
- Wiring it into the Card P task framework is Codex's, using the same pattern
  as P0.

## Card S: scanned drawing sets (pages that are images with no text layer)

Before Card S, a scanned set produced almost nothing. On caseD (mini-split set,
pages 1–5 are images), "Run all tasks" built only the north task for p. 4: no room
task (and no message saying why), and the site task was blocked ("No cited site
excerpts"). There is no OCR in the project, so the AI's vision is the reader.
Module `ai/scan_reading.py`, tool `tools/run_scan_reading.py` (manual ChatGPT
route), tests `tests/test_scan_reading.py`. Not yet wired into the operator panel.

- **S1 printed room areas.** The plan (sheet minus the right title strip) is sent as
  four overlapping tiles. The AI lists each room with a printed area: tile, name,
  number, value, unit (m2 or sf), the printed text and the name's position in the
  tile, plus the printed scale. Checks without a text layer: unit is m2 or sf (sf is
  converted at 0.0929); the printed text shows the value; 0.5–5,000 m²; the tile was
  sent and the position is inside it; a room listed on two tiles must have the same
  value on both, or the reading is refused. Printed areas need no calibration and
  rank first in the area source order.
- **Area calibration** (user decision 2026-10-06). A room with a printed area and its
  own outline implies mm/px = √(area / outline pixels). At least two rooms must agree
  within 2% of their median; one room alone never calibrates; an outline holding two
  labels does not count. The declared scale, if printed, is reported alongside.
- **S2 imperial scales and dimensions.** `parse_scale`: 1:100 → 100, 1/8" = 1'-0" → 96,
  1" = 10' → 120, NTS → none. `parse_dimension_mm`: 12'-6" → 3,810 mm. The shared
  `dimension_wall_matcher.page_scale_denominator` now uses it, so an imperial scale is
  no longer read as 1:8.
- **S3 site from a scanned title block.** The AI first copies every line of the
  title-block crops (right strip and bottom band of each page); address-like lines
  are grouped into excerpts of up to four lines; the existing site prompt and
  `validate_site_reply` then choose from those excerpts (every quote must be an exact
  substring of one).

Run on caseD (2026-10-06, Claude's stand-in readings from the tile images, not a
model): rooms 5/5 within 5% and site 1/1 against the caseD key; north 0/3, walls 0/5,
roof 0/5 still missing. Area calibration was correctly blocked: the scan wall finder
found only 2 fragments on this sheet. Its walls are double thin lines with a masonry
zigzag between them (no solid fill), which the thickness filter removes; closing the
gaps first turns text into blobs and the 1 m door-gap rule leaves the large rooms open
(probe only, not kept). Outlines for scans therefore need another method (for example
the AI outline task on the scan), which is the next step for walls and roof on
scanned sets. The cover sheet's address block (top left of p. 1) is outside the two
title-block crops; on this set the title strips carried the site, but a set whose
site appears only on the cover would be missed.

## Checking the model after drawing tasks (2026-10-20)

`backend/phase1_supervisor_service.py` audits the current traced rooms, room uses, drawing task outcomes,
printed ceiling heights and assembled envelope inputs. It locally previews the empty-room calculation to
find omissions and zero-load outcomes. The skill review is limited to room identity/use and site/document
mapping; it is not the source of envelope repairs.

Material gaps are queued by importance. Independent provider requests share `ARCHIE_AI_PARALLEL_CALLS`
(default 8). Geometry, boundary, height/roof and storefront checks follow their dependencies within the
same investigation cycle, rebuilding between dependent checks. A short targeted note is included in each
usual task packet and its fingerprint. Existing validators and apply functions remain authoritative.
Each gap gets at most two targeted attempts, with an early stop when neither physical model inputs nor
the gap set changed, and three cycles as a backstop. Remaining gaps are listed with the draft result;
there is no spend cap, web lookup or automatic operating-load startup.

## Deterministic room-use evidence (2026-10-25)

A room's name and physical outline remain unchanged. `room_use_resolution_service.gather_use_evidence` reads saved
traces, room-placed equipment, current P6 answers, pass-1 tenancy context and text inside the outline. It adds no
provider or network calls and never writes equipment placements. Heat ratings and catalogue wattages are not use
signals. Both the normal room-use service and preliminary assembly pass the same evidence to the pure scorer in
`ai/room_use_resolution.py`; the source fingerprint includes the evidence and policy version.

Precedence: explicit operator use override, human reviewer decision, protected refrigeration/excluded scope,
then scored evidence. An existing refrigeration room stays in refrigeration scope. User exclusions continue to
apply. AI acceptance is not a human reviewer decision.

Weights are heuristic classification points, not heat-load coefficients:

| Factor family | Points |
| --- | ---: |
| Existing label/use | 3 |
| Cooking line/appliances | 8 per family, capped regardless of duplicates |
| Dishwashing | 5 |
| Prep refrigeration / PREP text | 4 |
| Exhaust hood | 4 |
| WC/toilet/urinal | 12 |
| Basin | 4 |
| Desks/workstations | 8 |
| POS, display refrigeration, menu/buffet/food stations, seating | 9 to dining (hospitality tenancy) or retail (retail tenancy) |
| Drink/ice equipment with a counter | 10 to dining/bar |
| Confirmed shopfront wall, or glazing on an exterior/mall wall | 5 to front of house |
| Largest traced open shopfront room | 3 to front of house |
| Explicit internal access through another room | 2 to back of house (only if established, not inferred from missing doors) |
| Enclosed area below 6 m² | 2 to ancillary; insufficient alone to change the use |

A decisive choice needs at least 8 points and a lead of 4. Reclassifying a room to kitchen also needs two independent
kitchen families or two distinct cooking items; one appliance does not turn a large trading space into a kitchen.
A decisive choice has confidence 0.85. Otherwise keep the original selection, confidence 0.60 with an independent
factor or 0.40 without one, and disclose the uncertainty. A retained specific label remains a usable draft selection;
lower confidence alone does not schedule a new paid supervisor investigation. Generic/conflicting unresolved uses
retain their existing review behavior. `use_assessment` records factors, weights, alternatives, scores and whether
the choice was decisive; `review_recommended` flags weak decisions.

Placement safeguards: a room-name-only equipment location is recorded as uncertain with zero weight. Code positions
inside a plan outline, cited placement checks and saved physical-zone mappings can supply contents evidence. P6
items are local only when their cited page is the plan crop's page; an elevation-only answer does not establish
where its items sit in plan, even if its crop was chosen from a room called Kitchen. Nested outlines use the smallest
containing polygon to avoid attributing a subsidiary room's contents to the whole tenancy.

For existing same-size proposed-layout and traced plan sheets, local text can be transferred only after a pure
same-scale translation agrees on at least four unique printed dimensions (100–20,000 mm), within 2 PDF points,
spread at least 100 points horizontally and 20 vertically. Other rotations/scales or insufficient anchors are
rejected. The recorded anchors, residual, source and destination pages disclose this inference. This is a heuristic
registration guard, not engineering validation. No OCR, new AI reading or cross-page construction assumptions are
introduced by this step.

The Rooms view shows the chosen use, confidence, rationale and an expandable factor list beside the unchanged name.
Its controlled use selector writes the existing operator override and recalculates through the existing flow.

### Seating evidence for operating loads

The `page-inventory-v2-seating` prompt asks the existing one-page pass to retain printed seating schedules as
`people_occupancy` information with optional `seat_total`, `seat_room` and `seat_scope`. The total is the seats
column, not the number of tables. Outdoor and mixed/unknown totals are not automatically assigned indoors.
This changes future page readings without adding an AI call. Live capture has not been verified in this task.

`backend/seating_service.py` also reads saved pass-1 information and spatial text, and estimates repeated chair/stool
symbols in calibrated PDF vectors. Furniture/layout pages require a dimension-anchor translation into the traced
plan frame. Full symbol containment excludes terrace seats outside cooled outlines. Views are alternatives, never
summed. Explicitly labelled benches use floor(length / 600 mm); this spacing is a draft assumption. Raster seats,
unregistered views, missing scale and weak counts remain unresolved; zero detected symbols is not zero occupants.
Desk/workstation recognition is deferred.

Precedence: operator occupancy, the room’s own cited count, printed seating total allocation, usable vector estimate, labelled room-profile density.
A tenancy total is allocated by counted seats where available, otherwise by area of eligible front-of-house rooms;
room-specific totals are reserved before distributing the balance. Conflicting totals are not selected arbitrarily.
More than 15% disagreement is disclosed and the printed total wins. Staff are added only when an explicit numeric
profile allowance exists. No current profile defines a separate allowance.

Basis: `docs/australian_ventilation_rules.md` and `config/au_ventilation_rules_v1.json` provide no released seated
occupancy rule. One seat is therefore assumed to be one design occupant; this is not an AS 1668.2 compliance claim.
The resolved occupant count feeds both people gains and the existing outside-air resolver. Phase 1 still projects
people and outside-air loads out. Seat evidence is fingerprinted with the internal-gains dependencies; operator
edits survive re-resolution. The Rooms detail displays occupancy and its rationale.
