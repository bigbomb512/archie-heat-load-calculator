# Phase 2 audit (12 November 2026)

**User's aim:** Phase 2 (people, lights, equipment, outside air and infiltration) works on any drawing set without
problems, the way Phase 1 now does, and is checked for accuracy. The only trusted engineer number is Butcher Buffet,
35–50 kW (target about 39.6 kW). The Global Exchange 5.58 kW schedule figure is **not** a reference (user, 12 November).

## How it was checked
1. **Audit on saved jobs, AI blocked** (scratch tool, not in the repo; same approach as `tools/compare_job_versions.py`):
   copies of 11 saved jobs, model rebuilt from saved answers, full calculation run twice, every room's people,
   lighting, equipment, outside air and infiltration listed with where each value came from.
2. **Hand-worked rooms:** a house case added to the independent worksheet (`tests/reference/draft_reference_rooms.py`,
   case D), compared part by part with the draft (`tests/test_draft_reference_rooms.py`).
3. **Values against published sources:** the pack's Phase 2 numbers compared with the public tables found so far
   (below). The paid standards (AS 1668.2, the ASHRAE and AIRAH handbooks) were not available.

## Coverage
| Job | Result | Notes |
|---|---|---|
| Butcher Buffet (26 Oct copy) | 50.60 kW | Just above the 35–50 kW band; unchanged by these fixes |
| Butcher Buffet (20 Oct copy) | 51.96 kW | Older trace |
| Casa Nova | 25.02 kW | "Shop" (179 m²) is treated as retail at 0.15 people/m²; probably the dining area |
| Happy Cap | 2.01 kW | No engineer reference |
| Global Exchange | 1.30 kW | Trace covers about half the lease (Phase 1 gap) |
| Set 15, 8A New South Head Rd | 42.47 → 40.27 kW | House |
| Set 16, 26 Todman Ave | 31.32 → 30.30 kW | House |
| Set 18, 132 Old South Head Rd | 20.18 → 18.74 kW | House |
| Sunny Seafood, PMKC, Ogalo | no result | Old jobs with no traced rooms (Phase 1 never ran on them) |
| Sets 1–14 others, 17, 19, 20 | not run | No saved job with traced rooms; a live run needs an approved budget |

Every job gave the same total when calculated twice. No job crashed.

## Fixed (tests: `tests/test_phase2_house_values.py`, reference case D)
1. **House rooms got generic values.** The value resolver didn't know the house profiles and gave every house room
   the generic room's values (10 W/m² lighting, 75/55 W per person, 7.5 L/s outside air per person). These replaced
   the house profile in the draft. Now all three steps read `ai/house_profiles.py`.
2. **No house room had infiltration.** The airflow step looks for walls to outside before the traced walls join the
   proposal, so every room looked internal. The draft now decides from the walls it uses.
3. **House infiltration followed the room's occupied hours,** so a bedroom leaked no air in the afternoon. A house now
   leaks at every hour; a shop keeps its opening hours (its doors are the main path).
4. **Drawn cooking appliances in a house kitchen ran at 75 % from 3 to 10 pm.** They now run at the typical home
   cooking hours (half use, 5–7 pm), as the user's 30 October rule says house kitchens use home hours.
5. **Every typical or generic equipment value was labelled project evidence at 0.85 confidence.** Each item now
   carries its own origin (matched model 0.7, typical 0.5, generic 0.3); the room's equipment field takes the weakest
   and says how many of each.

Shop totals are unchanged by these fixes (checked on all five shop jobs).

## Hand-worked rooms
Case D, a single-storey house: main bedroom with a west window, bedroom 2 with no outside wall, a north living room, a
kitchen with typical home appliances and a hall. It checks house lighting, people, equipment, infiltration, the kitchen
appliances' hours, the household counted once across the house, and the sizing month and hour. It matched on every
component within 2 % at the first run after the fixes. With fix 1 undone it fails 27 checks.

The same caveat as cases A–C applies: the worksheet and the code come from the same team, so a shared misunderstanding
of a method can pass both. Passing shows the draft does the arithmetic as written, not that the method is right.

## Values against published sources
| Value in the pack | Pack | Published | Source | Status |
|---|---|---|---|---|
| Office lighting | 10 W/m² | max 4.5 W/m² (≥200 lx) | NCC 2022 Table J7D3a | Pack above the legal maximum for new work |
| Retail lighting | 12 W/m² | max 14 W/m² | NCC 2022 Table J7D3a | Within |
| Restaurant/café lighting | 14 W/m² | max 14 W/m² | NCC 2022 Table J7D3a | At the maximum |
| House lighting | 8 W/m² | 4 W/m² (NCC 2022 H6/13.7.6; not applied in NSW, where BASIX applies); 5 W/m² in NCC 2016–2019 | ABCB | Pack about double |
| Kitchen staff density | 0.3 /m² | default 0.2 /m² when the real number isn't known | ASHRAE 62.1 Table 6-1, kitchen (cooking) | Pack higher; Butcher Buffet kitchen 35 people (24 at 0.2) |
| Dining density (no seats) | 0.3 /m² | default 0.7 /m² | ASHRAE 62.1 Table 6-1, restaurant dining | Pack lower; printed seats replace it when found |
| Office outside air | 7.5 L/s/person + 0.3 L/s/m² | 10 L/s/person (secondary source) | AS 1668.2 Table A1, per a consultant's summary | Unverified: needs the standard |
| Dining outside air | 7.5 L/s/person + 0.3 L/s/m² | 10 L/s/person (secondary, indicative) | as above | Unverified |
| Heat per diner | 70 W sensible / 50 W latent | 81 / 81 W (incl. food) | ASHRAE Fundamentals Table 1, "sedentary work, restaurant" (secondary copies) | Pack cites AIRAH p. 47, not checked; differs from ASHRAE |
| Heat per kitchen worker | 85 / 135 W | 81 / 139 W | ASHRAE Table 1, "light bench work" (secondary) | Close |
| House infiltration | 0.36 ACH | — | uncited | Needs a source |
| Equipment typical values (~70 rows) | — | ASHRAE Fundamentals Ch. 18 and data sheets | `data/equipment_typical_values.xlsx` | Every row "Codex (needs partner review)"; not checked here |

## Decisions (12 November, delegated by the user to research)
The user asked for these to be decided from trustworthy sources. Each value is now in the pack's or the house
profiles' `value_sources`, and `tests/test_pack_plausibility.py` checks it equals its table row.

| Value | Was | Now | Source |
|---|---|---|---|
| Lighting where the drawings show none | office 10, retail 12, dining 14, kitchen 14, storage 6, generic 10 W/m² | office 4.5, retail 14, dining 14, kitchen 4, storage 1.5, generic 6 W/m² | NCC 2022 Vol One Table J7D3a maximum illumination power density, read from the ABCB page (generic: Note 1, 320–400 lx) |
| House lighting | 8 W/m² (garage 3) | 5 W/m² (garage 3) | NCC 2016–2019 Vol Two Class 1 lamp power density; NCC 2022 lowered it to 4 W/m² outside NSW. The larger is used. |
| People per m² with no seats or staff on the drawings | retail 0.15, dining and kitchen 0.3, storage 0.03 | retail 0.286, dining 0.667, kitchen 0.286, office 0.1, storage 0.1 | AS 1668.2-2002 Appendix A Table A1 net floor area per occupant (AIRAH-hosted extract, read directly). The standard calls them conservative. |
| House sun | in the room's occupied hours | in the NatHERS cooled hours: bedrooms 4 pm–9 am, living 7 am–midnight | NatHERS internal heat loads (nathers.gov.au) and the ANSNAC appendices |
| House infiltration | 0.36 ACH (uncited) | 0.45 ACH | CSIRO 2024, Air Infiltration of New Dwellings in Australia, Table 2: NSW two-storey new houses 9.07 at 50 Pa, ÷ 20 |
| Heat per person | AIRAH p. 47 | unchanged | ASHRAE's restaurant row (about 80/80 W) was found only in secondary copies; the cited Australian handbook stays |
| Outside air | 7.5 L/s per person + 0.3 L/s/m² | unchanged | AS 1668.2-2002 Table A1 gives 7.5 (Grade 2) for offices and dining; 10 L/s per person for the 2012/2024 editions comes only from consultants' summaries. Needs the purchased standard. |

Not decided here: kitchen staff come from the AS 1668.2 density (Butcher Buffet's 118 m² kitchen: 34 people), which the
standard itself calls conservative; real staff numbers from the client replace it.

Effect on the saved jobs (AI off): Butcher Buffet 50.60 → 51.18 kW, now 1.2 kW above the 35–50 kW band (more people
in the Bar, which has no counted seats; less kitchen lighting); Casa Nova 25.0 → 31.8; Happy Cap 2.0 → 3.0; Global
Exchange 1.30 → 1.19; set 15 40.3 → 54.4; set 16 30.3 → 37.1; set 18 18.7 → 25.0 (houses: the sun on bedrooms in the
afternoon and living rooms in the morning). Nothing was tuned towards the Butcher Buffet band.

## Found, not fixed
- **Equipment generic row TV-0060 ("any other appliance", 480 W) cites a planetary mixer data sheet.** It is used for
  heat lamps, a coolroom condenser and items known only by a code (E26, E32 …).
- **Generic cooking under a hood is 180 W** (TV-0054) for a combi oven, wok burner, pizza deck oven or six-burner range
  alike. Butcher Buffet's whole kitchen line-up gives 3.96 kW.
- **A house fridge is valued from a commercial Bromic data sheet** (TV-0055, 162 W).
- **Casa Nova's "Shop"** is classed as retail, though the job is a restaurant.

- **Butcher Buffet is above its band** at 51.2 kW. Its biggest parts are people (24.2 kW: 140 printed seats, 22 in
  the Bar by density, 34 kitchen staff by density) and outside air (10.8 kW). A room breakdown from the engineer would
  show which part differs.
- **Glass sun dominates the houses** (set 15: 35.6 of 54.4 kW). It uses one SHGC (0.45) with no angle dependence and no
  blinds; the window-by-angle work in the plan is the next accuracy step for houses.

## Next
1. Glass sun by angle, and blinds, for houses (largest share of their load).
2. A listed-equipment case (commercial kitchen, hooded and free-standing) in the worksheet.
3. Partner review of the equipment typical values against ASHRAE Fundamentals Ch. 18.
4. More engineer numbers: Butcher Buffet's room breakdown, the first client's job, design partners.
