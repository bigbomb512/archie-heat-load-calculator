# Houses: plan (30 October 2026)

Status: **in progress (started 30 October).** Evaluation sets 15–20 (six multi-storey detached houses, vector PDFs,
permitted on the same terms as sets 1–14) exercise a building type Archie has not run before. No engineer loads exist
for them yet, so they test coverage, not accuracy. Claims below were checked against the code, and independently
reviewed by Codex (read-only) on 30 October; its corrections are folded in.

## Rules decided by the user (30 October)

| Topic | Rule |
|---|---|
| Bedrooms | 2 people in the main bedroom, 1 in each other bedroom; the household is the sum. |
| Main bedroom | Labelled MASTER/MAIN; else the bedroom with the ensuite; else the largest. |
| Where people are | Bedrooms 10 pm–7 am. Living areas (living, family, kitchen, dining, study, media, rumpus, gym) 7–8 am and 3–10 pm. |
| Counting | Each living room is sized for the whole household at its busiest hour; the whole-house total counts each person once per hour. |
| No people | Garage, balcony, alfresco, pool, void, lift shaft, laundry, walk-in robe (and, by the schedule above, bathrooms). |
| Not cooled (default) | Garage/carport, laundry, walk-in robes, bathrooms/ensuites/powder rooms, outdoor spaces, lift shaft. **If the drawings explicitly show one is air-conditioned, it is cooled.** A void belongs to the room it opens into. |
| Entry, halls, stairs | Cooled, no people. |
| Kitchen and dining | House numbers, not restaurant ones: people from the household, appliances from the drawings else typical home values, home lighting and hours. |
| Two dwellings in one set | Each dwelling counts its own household. |
| Shops | Unchanged: house rules apply only when the drawings show a dwelling. |

Proposed default in the same spirit as the 15 October rule "use the most likely case for the building type,
labelled": **in a dwelling, an outside wall the AI can't classify is taken as facing outside** (in a shop it stays a
wall to a neighbouring tenancy).

## What breaks today

1. **Room uses.** Only BEDROOM, LIVING, LOUNGE, APARTMENT, DWELLING map to `residential`
   (`config/au_room_use_taxonomy_v1.json`, `ai/room_use_resolution.py:115 _label_matches`). BED/BED 2, ENS/ENSUITE,
   BATH, POWDER, LAUNDRY/L'DRY, WIR, PANTRY, FAMILY, MEDIA, GYM, ENTRY fall to `generic_conditioned` (cooled), as do
   GARAGE, CARPORT, BALCONY, ALFRESCO, DECK, POOL, VOID, LIFT (`ai/room_use_resolution.py:279–290`). KITCHEN/DINING map
   to the restaurant profile (kitchen as a process kitchen). LIVING/DINING matches two uses and drops to generic.
2. **Profile lookups elsewhere.** `_profile_from_label` (`ai/ai_preliminary.py:241`) sends any label containing
   kitchen, dining or bar to hospitality; the people-activity lookup (`:1175`), schedule fallback (`:634`) and the
   equipment allowance top-up (`:1194`, profile W/m² as a floor under listed items) all follow the profile. A house use
   has to reach every one of these.
3. **Unnamed spaces** can only be named Dining, Shop, Kitchen, Bar, Office, Store, Toilet or Corridor
   (`ai/room_outline.py:338`, validator `:646`).
4. **Windows.** Openings on room walls come only from the storefront tasks (`P4_storefront_wall`, `P4_openings`;
   placed from `part["openings"]`, `backend/ai_preliminary_service.py:1815`). They only look at elevations titled
   shopfront/storefront/**external elevation** (`backend/autonomous_tasks_service.py:1672`), and the Phase 1 opening
   skills are no longer in the Phase 1 scope (`backend/skill_workflow_service.py:99`). **A house would get no
   windows** — usually its largest cooling load. A house sheet titled "External Elevation" would wrongly enter the
   shopfront logic.
5. **Walls.** An edge the boundary task doesn't classify becomes `internal` if it runs along another traced room, else
   `adjacent_tenancy`; both are zero temperature difference (`backend/ai_preliminary_service.py:1668–1685`). In a
   detached house every unclassified outside wall would add nothing, and a bedroom wall to an uncooled garage adds
   nothing.
6. **Floors.** One project-wide basis from drawing text (`_floor_basis`, `backend/ai_preliminary_service.py:2606`): if
   any page says e.g. "basement below", every floor is "unconditioned below" (an unresolved exception); otherwise every
   floor is taken as a ground slab or over conditioned space. There is no per-room, per-level floor neighbour.
7. **Occupancy and hours.** Density counts are tagged `controlled_preliminary_profile`
   (`ai/internal_gains_resolution.py:231`) and the assembler recomputes them from the profile density
   (`ai/ai_preliminary.py:1125–1134`), keeping only evidence/override origins. Schedules are chosen by profile
   (`ai/internal_gains_resolution.py:117`, `ai/ai_preliminary.py:634`) and assigned per room (`:1291`); there is no
   bedroom-versus-living-area schedule. The house total is the plain sum of zones (`ai/hourly_loads.py:1756`
   `aggregate_project`), so one household in three living rooms counts three times; fan heat is added after room
   contributions (`:1066`).
8. **Shop-only steps.** Tenancy-outline and boundary prompts talk about tenancies, malls and shopping centres
   (`ai/room_outline.py:428`, `ai/autonomous_tasks.py:395`); the kitchen-equipment task (P6) looks for commercial
   cooking lines and hoods.
9. **Thermal specifications** (from the PDF text layers, not code): the BASIX/NatHERS certificate pages in sets 15,
   16, 17, 19 are images or scrambled text, so their insulation and glazing values need AI page reading. Nothing reads
   certificate values today. Only set 18's window schedule is a clean text table.
10. **Ceiling heights** written "2700 FTC", "FCL"/ceiling RLs aren't read by the text reader
    (`ai/autonomous_tasks.py:297 _CEILING_LABEL`); the AI ceiling task (P7) may still read them.
11. **No dwelling identity.** Rooms are keyed by label + level (`ai/ai_preliminary.py:265`); floors get IDs from level
    text (`:1119`). There is no notion of which rooms belong to which dwelling (set 19 has two), or which ensuite
    belongs to which bedroom.

## Progress (31 October)

**Done, tested, not yet run on a real house job** (no house has been uploaded; that needs page reading and room
outlines by AI):

- **W0/W1 (Codex agent, reviewed and integrated):**
  - Dwelling recognition, persisted in `room_use_resolution.json`.
  - House room uses incl. abbreviations and combined labels; `not_cooled` scope (wet, service, garage, outdoor, lift
    shaft) excluded from the load but kept as neighbours.
  - House numbers in `config/house_profiles.json`, labelled "typical home value, uncited, for review".
  - No per-person outside air in a dwelling; the labelled infiltration rate counted instead.
  - House room names for unnamed spaces (only when the caller says it's a dwelling — not yet wired into the outline
    tasks).
- **W5 text part:**
  - `ai/house_windows.py` reads row and column window schedules (wrapped rows and sizes, glazed doors, skylights kept
    apart), finds window tags on plans, and places each tagged window on the outside wall it stands against. Several
    windows per wall while they fit; a mark with two sizes or no schedule row is listed, never guessed.
  - `backend/house_windows_service.py` runs it in a dwelling at the end of the drawing-task run and saves windows on
    the room traces (source `drawing_text`). A wall that takes a window is marked outside, and the result is written
    to `house_windows.json`.
- **Shop check:** both shop jobs identical; full suite passing.

- **W4 without AI (1 November):** in a dwelling (`ai/house_envelope.py`, wired into
  `backend/ai_preliminary_service.py`):
  - A wall the AI didn't classify, with no traced room beyond it, faces outside (labelled assumption). A wall to a
    garage, laundry, robe, bathroom or lift shaft is unconditioned (operators' temperature, else outdoor − 3 K). A
    wall to a balcony/alfresco faces outside. A reviewer's classification always stands; an AI "external" stands.
  - The lowest traced level sits on the ground. Each upper room's plan is matched to the level below — by the stair
    or lift traced on both, else by dimension text both sheets print, at the same scale — and its floor is split by
    what is under it: cooled rooms (no heat flow), rooms not cooled (buffer temperature), or outdoor/untraced space
    (the method's labelled adjacent-space estimate, not zero). If the plans can't be matched, the floor is taken as
    over cooled rooms, labelled.
  - The outside-walls AI question (P3) isn't asked for a house unless its drawings mention a shared or party wall,
    a dual occupancy or "Dwelling A/B" (set 19).
  - Tests: rules (`tests/test_house_envelope.py`), a two-storey house through the surface builder and assembler
    (`tests/test_house_envelope_assembly.py`). Not yet run on a real house: the dimension-text matching between
    level plans is untested on real sheets.
  - Not done: the "drawings show it's air-conditioned" exception for garages/laundries etc. (text or AI), and the
    shop wording of the P3 prompt for houses that do share a wall (W6).

- **W2/W3 household (1 November):**
  - `ai/house_household.py`: 2 in the main bedroom (labelled MASTER/MAIN → the bedroom an ensuite opens off → the
    largest), 1 in each other bedroom; each living area (living, family, kitchen, dining, study, media, rumpus,
    gym) takes the whole household; one household per dwelling ("Dwelling A/B", "Unit 1/2"), else one per set.
  - The internal-gains resolver records origin `household_rule` (a typed override still wins) and the room's
    `occupancy_group`; the assembler keeps the count and passes the group into the hourly model.
  - `ai/hourly_loads.py::count_shared_occupants_once`: any hour the group's rooms hold more people than the household,
    the excess people heat is taken off the whole-house total, recorded in `occupancy_adjustments` per hour and
    `occupancy_groups` per scenario. Room, zone and floor results stay as sized; fan heat follows (it is computed from
    the total's room sensible). Hours are the house profiles' (bedrooms 22–07, living 07–08 and 15–22).
  - Tests: `tests/test_house_household.py` (rule, count-once, a synthetic house through resolver → assembler →
    calculation). Both shop jobs identical; full suite and npm test passing.
  - Not done: the results screen doesn't show the "household counted once" line yet. The house profile hours also
    apply to a room's lighting and appliances, so a kitchen fridge counts only in living-area hours; that needs its
    own schedule.

- **Remaining no-AI work (1 November; two Codex agents and this session, reviewed and integrated):**
  - **Roofs from the level plans:** the top level has the roof. A lower room is under the matched rooms of the level
    above (no heat flow under a cooled room, buffer temperature under one that isn't cooled) and under the roof
    elsewhere or under an outdoor space. A reviewer's roof stands; unmatched plans keep the general roof rule.
  - **Air-conditioning shown on the drawings:** in a house, the ceiling and services plans are lined up with each
    room's plan too. A garage/laundry/bathroom/lift with "A/C", "ducted", "FCU", "cassette", "split system",
    "supply/return air" or "SA/RA grille" inside its outline is cooled (no occupants), citing the page. A lone
    "SA"/"RA" doesn't count (often a smoke alarm). Walls next to such a room are walls to a cooled room.
  - **House kitchen appliances:** when the drawings list none, labelled typical home values
    (`config/house_profiles.json`, "typical home value, uncited, for review"): a fridge 60 W all day, and cooking
    2 kW at half heat-to-space over 5–7 pm, each on its own hours.
  - **Shop-only steps off or reworded in a house (Codex):**
    - storefront (P4), commercial kitchen (P6) and roof (P5) questions aren't prepared, and P5 records are hidden for
      a house;
    - the outside-walls question (P3), when a shared wall is mentioned, speaks of party walls, not malls;
    - the tenancy outline asks for the house's footprint;
    - house room names are offered for unnamed spaces;
    - "2700 FTC", "C.L. 2700" and "CLG 2700" ceiling heights are read.
    Shop packets and prompts are byte-identical (checked for both shop jobs, images included).
  - **Results screen (Codex):** a house result shows "Household counted once: the rooms hold N people at H:00, the
    house has M — x kW of people heat not counted twice." A shop result doesn't.

**Text-layer window coverage on the six houses:**

| Set | Readable schedule | Tags on plans | Tags matching the schedule |
|---|---|---|---|
| 15 | 30 windows | 56 | 48 |
| 16 | 28 windows | 48 | 36 |
| 18 | 26 windows | 27 | 27 |
| 17 | drawn (no text) | 21 | sizes need AI (one schedule page) |
| 19 | not found as text | 25 | sizes need AI |
| 20 | none | none | elevations need AI |

Doors with no description (set 15) aren't counted as glass; the double-height W21 in set 18 is listed, not placed.

**Next:**
1. One small AI step for sets 17/19: read the drawn window schedule page into the same records.
2. An elevation step only for walls with no readable windows (set 20).
3. Only AI work remains: windows for sets 17/19/20, BASIX/NatHERS values (W7), air-conditioning drawn only as
   symbols, and a first real house run.

## Keeping AI to a minimum in a house (design, 30 October)

The drawing tasks today ask the AI per page or per room. In a dwelling most answers follow from geometry and text:

| Question | Shop today | House (planned) | AI in a house |
|---|---|---|---|
| Walls facing outside (P3) | AI per plan page | The building outline per level is outside wall; party walls only where the drawings name a neighbouring dwelling/boundary wall | only to confirm a named party wall |
| Roof exposure (P5) | AI per room | Stack the level plans (W4 registration): a room with no upper-level room over it has the roof above it | none |
| Windows (W5) | AI per storefront wall | Window schedule + window tags on plans (text layer) → room walls, applied directly with source `drawing_text` | only walls with no readable schedule/tags |
| Ceiling heights (P7) | text + AI | text (add FTC and ceiling RL − FFL) | only where nothing is printed |
| Certificates (W7) | — | text where readable | image-only certificate pages |

Still AI everywhere: reading the page inventory and tracing room outlines (P0). Applying text-layer answers needs a
new no-AI step in `backend/autonomous_tasks_service.py::_run_all` (the packet builders only have the job folder; the
step needs `web, project` to write openings onto traces as `_apply_p4` does).

## Workstreams

Each lands with tests and the shop check: Butcher Buffet and Casa Nova copies, frozen clock, identical fingerprints
and reports before/after (the harness used 25–28 October).

### W0 — Recognise a dwelling (no AI)
Add a `residential` tenancy type to the inventory-context inference (`backend/room_use_resolution_service.py:212`):
two or more bedroom labels, or "dwelling", "residence", "house", BASIX/NatHERS. **Pass it into the room-use resolver
(`ai/room_use_resolution.py:262 _record`) and persist it with its evidence**, so every later step reads one flag.
*Tests:* each house set's saved page text → residential; both shop jobs unchanged.

### W1 — House room uses, names and profiles (no AI)
- Dwelling-only room uses: `house_bedroom`; `house_living` (living, lounge, family, rumpus, media, cinema, theatre,
  games, study, gym, kitchen, dining, meals; pantry/scullery join the kitchen); `house_wet` (bath, ensuite, powder,
  WC); `house_service` (laundry, WIR, robe, linen); `garage` (garage, carport); `outdoor` (balcony, alfresco, deck,
  terrace, patio, pool); `lift_shaft`; `circulation` (entry, hall, stair, lobby). VOID joins the room it opens into.
- Abbreviations BED, BED 2, BR, MBR, ENS, L'DRY/LDRY, WIR/W.I.R., PDR, MEALS, KITCH; combined LIVING/DINING,
  KITCHEN/DINING are one living area in a dwelling, not a conflict.
- New space scope `not_cooled`: coordinated through `SPACE_SCOPES` (`ai/ai_preliminary.py:33`), taxonomy validation,
  the assembler (excluded from the load like `:1087–1097`, but **kept as a neighbour** for W4) and the report's
  excluded-space list with its reason. Outdoor spaces keep their geometry as boundary records — not `not_a_room`,
  which removes them from tracing and totals.
- House profiles in the assumption pack (lighting, equipment allowance, schedules), reached by every lookup in
  breakage 2, including `_profile_from_label` in a dwelling. Kitchen appliances follow the equipment value ladder;
  **typical home appliance values come from the user's generic-value database** or are labelled typical.
- Add Bedroom, Living, Family, Bathroom, Ensuite, Laundry, Garage, Study, Entry to `USE_ROOM_NAMES` and the naming
  validators, accepted only in a dwelling.
*Tests:* every label found in sets 15–20 maps to its intended use and profile; shop labels and profiles unchanged.

### W2 — Household occupancy and hours (no AI)
- Dwelling grouping (one per set unless the plans label dwellings, as set 19 does); ensuite-to-bedroom association
  from shared traced edges; main bedroom by the agreed rule with a deterministic tie-break and its reason.
- Occupancy: bedrooms 2/1; living areas the whole household; wet, service, circulation none. A new origin
  (e.g. `household_rule`) carried through the internal-gains record and rationale (`ai/internal_gains_resolution.py`
  occupancy precedence `:222–264`, record/schedules `:335–363`) and accepted by the assembler (`ai/ai_preliminary.py`
  `:1125`).
- Two new day profiles (bedrooms 10 pm–7 am; living 7–8 am and 3–10 pm), selected **by room role**, not profile,
  assigned per room (`:1291`), including weekend/holiday day types.
*Tests:* end-to-end synthetic 4-bedroom house through resolver → model → hourly output: household 5, people by hour,
ensuite and largest-bedroom fallbacks, two-dwelling grouping.

### W3 — Count each person once in the whole-house total (no AI; calculation engine)
Rooms of one dwelling form an occupancy group capped at its household. For each hour where the group's rooms hold
more people than the household, scale down **at room level, for the house total only**, the people sensible/latent,
the per-person part of outside air, and the fan heat that `hourly_loads` adds on that sensible (`:1066`), then roll up
(`:1756`). Room peaks stay as sized (each living room for the whole household). The adjustment is its own labelled
line in the report; zone and floor subtotals stay room sums and are labelled as sizing values.
*Tests:* 3 living rooms + 3 bedrooms → room peaks use 5 people each; house total never above 5 people per hour; OA
and fan heat adjusted with it.

### W4 — Walls and floors next to spaces that aren't cooled (no AI for the default; AI for the exception)
- In a dwelling, unclassified outside walls are taken as outside (proposed default above), not neighbouring tenancy.
- Same-level neighbours: an edge along a `not_cooled` room (`_edges_along_other_rooms`, same page, 600 mm,
  `backend/ai_preliminary_service.py:2556`) becomes `unconditioned_space`. The surfaces then use the existing labelled
  buffer-space assumption, outdoor minus 3 K (`ai/thermal_surface_resolution.py:322`).
- Between levels: register each level's plan to the one below (the plan-to-plan text registration used by seating,
  `plan_text_translation`), and give each room a floor basis from what lies under it. Rooms over a garage or basement
  take the buffer temperature, rooms over cooled rooms zero, ground-floor rooms the ground slab. This replaces the
  project-wide `_floor_basis` in a dwelling.
- Exception: text inside the room outline on a registered plan/RCP/electrical page (SA, RA, SUPPLY AIR, A/C, AC,
  DUCTED, FCU, SPLIT, CASSETTE) makes it cooled, with the citation; where outlets are symbols only, an AI drawing task.
*Tests:* bedroom beside a garage → buffer wall; first-floor bedroom over a garage → buffer floor; garage with a cited
"SA" → cooled; detached house with unclassified walls → outside.

### W5 — Windows on every wall (AI) — largest piece
A drawing task per external wall run: pick the elevation that shows it (north arrow + wall bearing → N/S/E/W
elevation; registered geometry), read each window (window schedule when it is a text table, as set 18; plan window
tags + schedule; else the elevation at scale), and assign **each window to one room edge** — no duplicates on shared
walls or across levels — with opening validation and surface-ledger ownership as the storefront openings have.
Skylights from skylight schedules onto roofs; eaves/awnings through the existing shade fields. In a dwelling, house
elevation titles (including "External Elevation") use this task, never the shopfront path.
*Tests:* schedule-table parser on set 18's table; synthetic replies; ownership/duplicate rejections. *Needs:* AI spend.

### W6 — Other shop-only steps in a dwelling
Tenancy outline → building footprint per level (prompt wording); boundary choices without "mall", with
"neighbouring dwelling" (party wall, zero as now); P4 storefront eligibility off in a dwelling; the kitchen-equipment
task lists home appliances (no hoods or cooking lines); ceiling heights also read "FTC" and ceiling RL minus FFL;
roofs: verify the roof task assigns exposure per room across levels (top-floor rooms, lower rooms under a roof
section or a balcony above) — `ai/autonomous_tasks.py:891–973`.

### W7 — BASIX / NatHERS specifications (AI)
An AI page-reading task for certificate pages: wall, roof, ceiling and floor insulation R-values, glazing U-value and
SHGC (per window or default), each cited. Mapped to surface and opening targets through value resolution with an
explicit precedence (certificate over preliminary construction; conflicts between certificate and schedule shown,
not silently chosen) — `ai/thermal_surface_resolution.py:290–303`.

### W8 — Evaluation
Draft answer sheets for sets 15–20 (floors, rooms per floor, bedroom count, main bedroom, cooled/not cooled) for the
user to confirm before committing (evaluations/README); house steps added to the Phase 1 supervisor's gap checks
(`backend/skill_workflow_service.py:99`, `docs/PHASE1_SUPERVISOR_REPORT.md`). Portfolio scorecard after AI runs.
These remain coverage checks: no house result is validated until engineer loads exist.

## Suggested order

| Step | Workstreams | AI | Size |
|---|---|---|---|
| 1 | W0, W1 (dwelling flag, room uses, profiles, names) | no | M |
| 2 | W4 defaults (outside walls, not-cooled neighbours, per-level floors) | no | L |
| 3 | W2, W3 (household, hours, count once) | no | L |
| 4 | W6 (shop-only steps) | no to change; yes to run | M |
| 5 | W5 windows | yes | L |
| 6 | W7 certificates; W4 AI exception | yes | M |
| 7 | W8; first full house run (set 18 first: readable window table, area plans) | yes | — |

Steps 1–4 need no AI and leave both shop jobs identical. Phase 1 (envelope) is what must be "right every time", so
W5 could come before W2–W3 if preferred.

## Decisions (30 October, second round)

1. **Ventilation:** houses use infiltration (the labelled preliminary air-change rate), not per-person outside air.
2. **Home appliances:** labelled typical values (not waiting for the generic-value database).
3. **Order:** windows first — W0/W1 (needed by everything), then W5, then W4, then W2–W3, W6, W7.
4. **AI kept to a minimum:** windows come from the text layer (window schedules + window tags on plans) wherever
   readable; an AI task only for walls with no readable window evidence. Certificates (W7) and the AC exception in W4
   stay text-first too.
5. **Codex agents** take separable pieces (each in its own worktree); results are reviewed and integrated here.
6. Taken as accepted: set 19 split by its dwelling labels, else one household; unclassified outside walls of a dwelling
   face outside.

## First real house run: set 18 (1 November)

Set 18 (132 Old South Head Road, Vaucluse; 22 pages: existing, demolition, proposed and set-out plans for three
levels) was run end to end in this worktree's own `output/` (OpenRouter, the normal job flow). What it showed, and
what was changed:

| Found | Change | Check |
|---|---|---|
| Each drawing-task round spent minutes before and after every AI call: the room-label scan measured the page's text extent once per word (quadratic), ~30 times a round | Title-block extent measured once per page (`room_inference.title_block_test`); the rooms named on the drawings are worked out once while their four input files are unchanged | Same `infer` output on set 18, BB, Casa Nova, GE; `run_all` 742 s → 52 s under the profiler; a round of 14 answers took 5 min (was ~2 min per answer) |
| A dimension the AI says it can't read (`value_mm: null`, as the question allows) was refused and asked again every round and on every resume (3–5 times each) | A null reading closes that dimension task as blocked ("could not read"); a wrong reading is still asked again | `tests/test_drawing_task_speed.py` |
| Plan choice ignored the page reading: the existing ground floor (p5) and the roof plan (p11) were traced, the proposed lower ground floor (p8) wasn't, and the First Floor plan had no level | When the page reading found main plans, only those are candidates (proposed whole-floor plans; existing only when there is no proposed one), grouped by the page reading's level; the ranking inside a level is unchanged. A page with no level in drawing coverage takes the page reading's | Selection unchanged on BB (20), Casa Nova (33), GE (5), Woolworths (5), markup (1), Sunny Seafood (4, 6); set 18 now 9, 10, 17. `tests/test_house_plan_pages.py` |
| Outlines were matched to rooms by name only, so the First Floor's Bedroom/Corridor/Ensuite were attached to rooms on another level | A name found on more than one level goes to the page's level's room; one-level jobs are unchanged | same tests |
| The whole lower ground floor was named "Shop" | A house is not offered the shop-only names Shop and Bar | same tests |
| The text layer gave one "Bedroom" for four bedrooms (house plans draw most room names as lettering) | On a house, the page reading's room list for that plan ("Bedroom 2", "Master bedroom", "WIR"…) is offered in the naming step; a chosen name becomes a room, cited as "drawn on the plan" | same tests |
| Corridor and Office fell into shop categories; Porch was cooled | House rules: corridor/passage/hallway → circulation, office → living area (like a study), porch/verandah/courtyard → outdoor | same tests; shop "Office" still `office` |

Still open from this run:

- **Lower Ground level:** the set-out plan (p17) still outranks the proposed plan (p8) on dimension count. The page
  reading lists only alfresco, pool, lift and substructure void on that level, so no cooled room is expected there.
- **Store / Storage** in a house stays a cooled generic room: no decision covers it yet.
- **Run cost so far:** first pass US$0.45 (143 calls, including the repeated null dimensions and 13 north calls). Its
  skills step was cut off and no calculation ran. A fresh rerun on the fixed code (with the first run's reply caches
  copied in) stalled in page reading while the computer slept and was stopped before any AI spend. Next: rerun set 18
  with the computer kept awake, then check rooms per level, windows placed, household and the house total.

## Set 15 on the live app (2 November)

The user ran house set 15 (26 Todman Avenue, West Pymble) on the live app. It stopped at "Trace the rooms and walls"
and calculated 2.96 kW for Bar, Dining, Kitchen, Store, Toilet and WC, with "Shop" left out.

- Cause: the live app runs the main checkout, which has none of this house work (it is uncommitted in this worktree).
  It treated the house as a shop: one plan traced (Ground, page 6), the basement and first floor skipped, a "Shop"
  invented that had no outline. Two drawing tasks were left over: a dimension the AI said it could not read (fixed
  here 1 November) and an unnamed-floor check refused because "Dining" was already traced.
- With this worktree's code (AI off, on a copy): set 15 is found to be a house (BASIX), and the plans chosen are
  Basement p5, Ground p6, First p7/p13.
- Fixed: set 15's page reading lists each plan's rooms in one sentence ("Basement floor plan includes Garage, Lift,
  Mud, Cellar, Gym, Entertainment, Bar, Sauna and Bath."), which gave no names. Such lists are now split into names.
- Fixed: Entertainment is a living area; WIP / walk-in pantry is a pantry (living), not a robe.
- Open (user decision): Cellar, Sauna, Mud room and ST/Store fall to the generic cooled room.
