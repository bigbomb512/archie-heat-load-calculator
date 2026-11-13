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

## Glass sun by angle and inside blinds (12 November)
Source: ASHRAE Handbook—Fundamentals 2001, fenestration chapter, as reproduced in Arici's course notes (Michigan Tech
MEEM 4200, Chapter 7, Tables 7-3 and 7-4). EnergyPlus's simple-glazing curves (Arasteh, Kohler and Griffith) were
also read; their U/SHGC-to-curve map is a figure not available as text, so the ASHRAE table was used.

- **Angle** (`ai/glazing_angle.py`, `ai/sun.py`): the window's normal SHGC is kept, and the sunlight on the glass is
  scaled first. The direct sun on the sunlit part is multiplied by SHGC(angle) / SHGC(0) of the most similar ASHRAE
  window, linear between the tabulated angles and 0 at 90°. Diffuse sky and ground light is multiplied by
  SHGC(diffuse) / SHGC(0). This follows the source's advice when a maker gives only the normal SHGC. The glass types are:
  - single clear (1a): U 4.5 W/m²K or more;
  - double clear (5a): lower U with a normal SHGC of 0.70 or more;
  - low-e double (21a): otherwise.

  The incidence angle each hour is pvlib's. Without a site sun (the generic cardinal profiles) there is no angle
  dependence, and the glass says so.
- **Blinds:** an inside venetian blind or roller shade multiplies the glass's solar gain by the table's IAC for the
  device and glass. A blind counts only when a window schedule names it (`house_windows.internal_shade`) or an operator
  enters it on the opening (`internal_shade` on a traced opening; no screen for it yet). With the colour or opacity not
  shown, the device's highest IAC is used. Curtains get no credit (the drapery table isn't captured) and say so.
- **Checked:** the angle table, the blind lookups, the incidence angle against plain geometry, glass totals hour by
  hour (`tests/test_glazing_angle.py`), a blind's effect through the draft (light venetian on the pack's glass: × 0.78),
  and the reference rooms. The worksheet applies the same table on its own sun geometry and interpolation; the draft
  matches it within 2 %.
- **Effect** (AI off): set 15 54.4 → 52.5 kW, set 16 37.1 → 35.5, set 18 25.0 → 24.0; glass sun about 10 % lower.
  Shops unchanged: no saved shop has sunlit glass. No saved house has a blind on its schedule.
- **Still open:**
  - Every house window uses the pack's glass (U 5.8, SHGC 0.45, so the single clear curve) until the BASIX or window
    certificates are read. Real house glass is often clearer (single clear 0.86, double clear 0.76), so this is now
    the largest glass uncertainty for houses.
  - There's no screen to enter blinds.

## House glass from the energy certificates (12 November)
**What the six house sets hold:**
- No set prints a window U-value or SHGC in its text layer.
- Set 16 (page 3) and set 19 (BASIX page 19; NatHERS pages 20–21, one per dwelling) paste the certificate in as a picture.
- Set 17 carries the BASIX certificate as text, but it says "Upgraded windows, refer to NatHERS Certificate", and that
  certificate isn't in the set.
- Set 15 only says "refer to BASIX certificate"; sets 18 and 20 have none.

Set 19's NatHERS certificate lists window types with U and SHGC, and a window schedule by room. Read at 250 dpi in
sections, its text is legible.

- **New drawing question P8** (`_p8_packets`, `validate_glazing_certificate_reply`, `_apply_p8`):
  - It's asked for each page whose page-reading title names NatHERS or BASIX, houses only.
  - The page goes to the AI in 250 dpi sections.
  - The AI transcribes the window types (ID, description, U, SHGC) and the schedule (room, type, height, width,
    orientation). The checker refuses a U outside 0.5–7.5, an SHGC outside 0.05–0.95, or a schedule row whose type
    isn't listed.
  - A sheet with no window table must say why.
- **Using it** (`ai/certificate_glazing.py`, `_with_certificate_glazing`): each house window takes the area-weighted U
  and SHGC of the certificate windows in the room of the same name.
  - Names match on their words (abbreviations expanded); numbers must agree.
  - A room the schedule doesn't name takes the whole dwelling's area-weighted values.
  - Types without a schedule give their average.
  - With two dwellings, each certificate serves its own.
  - A window with its own U and SHGC keeps them.
  - The answers are part of the draft's input fingerprint, so a new reading refreshes it.
- **No certificate:** the NatHERS default windows (NatHERS fact sheet Table 1; NSW Planning Portal BASIX default
  window data), aluminium single clear:
  - ALM-001-01 A (hinged or projected: awning, casement, bi-fold), U 6.7 / SHGC 0.57;
  - ALM-002-01 A (fixed or sliding), U 6.7 / SHGC 0.70.

  The frame group comes from the window schedule's description; unknown is group B. Set 19's certificate uses exactly
  these two. This replaces the pack's uncited U 5.8 / SHGC 0.45 for houses only; shops keep the pack.
- **Checked:**
  - `tests/test_house_glazing.py` covers the defaults, the frame group, the reply checker, room matching, the service
    and the page choice (titles from the page reading: set 16's drawing-set title for page 3 is a title-block artefact).
  - The house reference case now uses U 6.7 / SHGC 0.70.
  - A fake-AI run of the drawing step on set 16 asks P8 for page 3 and finishes.
- **Effect** (AI off, so default glass): set 15 52.5 → 73.8 kW, set 16 35.5 → 45.2, set 18 24.0 → 29.9 (glass sun up
  about 55–70 %). Shops unchanged.
- **Live check (13 November, Codex CLI with the ChatGPT sign-in; the user chose Codex over OpenRouter):** 4 sheets, every
  reply passed the checker.
  - Set 16 page 3 and set 19 page 19 were rightly answered "no window table". Set 16's BASIX page is blank apart from
    its title block; set 19's BASIX page lists commitments only.
  - Set 19 pages 20 and 21 (Dwellings A and B) gave both window types exactly: ALM-001-01 A 6.7 / 0.57 and ALM-002-01 A
    6.7 / 0.70.
  - Checked by eye against Dwelling A's schedule: 16 of 17 window rows right. The ensuite window was given type A
    where the certificate prints ALM-002-01 A; ensuites aren't cooled.
  - About 17,000 input tokens per sheet; set 16's A1 sheet went as 12 sections and 41,000 tokens.
- **Not done:** window-level matching by size and orientation; rooms take area-weighted values.

## Stored heat by the radiant time series method (13 November)
Until now every heat gain reached the room air in the hour it happened. Real rooms store radiant heat (sun on the floor,
heat from lights and people) and give it back over the following hours, and heavy walls and roofs pass heat hours late.
The draft now uses ASHRAE's Radiant Time Series method (Fundamentals 2009 Ch 18).

- **Where the factors come from:** ASHRAE's tables weren't available (only one row of Table 19 could be found, in a
  2015 journal paper). The AIRAH Technical Handbook 2007 has no stored-heat factors either; searched on 13 November, it
  has U-values, material properties, film resistances and check figures, but no RTS, CTS, CLTD or CLF. So Archie
  calculates them with its own heat balance, `ai/stored_heat.py`, the way ASHRAE's were made:
  - **Walls and roofs:** each layer's exact transmission matrix for a 24-hour periodic day, giving periodic response
    factors for a one-hour pulse. They sum to U; divided by U they are the conduction time series (CTS).
  - **Rooms:** a heat balance of every inside surface. Each surface convects to the air (held steady), exchanges
    long-wave radiation through a mean-radiant node, and conducts into its construction. Behind the construction is
    outdoors, the ground, an identical neighbouring room, or nothing (furnishings). A one-hour radiant pulse gives the
    room's radiant time series (RTS): nonsolar heat lands on every surface by area, solar heat on the floor.
  - **Inputs:** materials, films and emittance are from AIRAH 2007 pp.150–177; every value is cited in
    `config/stored_heat.json`.
- **Checked against published results** (`tests/test_stored_heat.py`):
  - ASHRAE's PRF/RTF generator for a 30 cm thermo-stone wall (Univ. of Baghdad Journal of Engineering, 2015, Table 4):
    every hour within 0.006 of the published fraction, the same peak hour, and the sum within 2 % of the table's.
  - The same paper's concrete roof: within 0.002 (its membrane and tile thicknesses aren't printed; no membrane, 13 mm
    tile assumed).
  - ASHRAE 2009 Table 19, heavyweight / no carpet / 10 % glass nonsolar: within 2 points every hour (ours 20.4, 11.4,
    6.5, 5.2, 4.5 … against 22, 10, 6, 5, 5 …).
  - The zone is ASHRAE's representative zone as we read Table 21. Its furnishings line ("25 mm wood, 50 % of floor
    area") matched as one face of wood over half the floor. Taken as both faces, hour 1 came out 14.7 against 10. Table
    21 itself wasn't available to confirm that reading.
  - These are two references only: light and medium construction, carpet, 50/90 % glass and all solar series are not
    checked yet (TODO.md, "Stored heat").
- **Each room's own series:** `ai/stored_heat_rooms.py` builds the heat balance from the room's traced surfaces (areas,
  and what each faces). Each surface gets a typical construction (`config/stored_heat.json`): one named in its label or
  answered construction, otherwise the lightest typical one, which takes the least stored-heat credit.
  - Defaults: framed wall, metal roof, plasterboard partitions; a ground floor is a 100 mm slab on ground; between
    storeys, concrete with a suspended ceiling (commercial) or timber with plasterboard (house).
  - Insulation is sized to the surface's own U-value, so the series use the job's U.
  - Floor finish by room use: carpet in house bedrooms and living rooms and in offices, tiles elsewhere.
  - Furnishings: 25 mm timber over half the floor.
  - Each room's job report lists the constructions it assumed ("stored heat").
- **Splits** (Nigusse 2007, adopted by ASHRAE 2009): people 0.60 radiant (sensible only); lighting 0.40 (lower end of
  recessed fluorescent with lens); walls and floors 0.46; roofs 0.60; windows 0.33 when SHGC > 0.5, otherwise 0.46;
  sun through glass without blinds 1.0, by the solar series. No split is available for equipment or for glass behind
  blinds, so both reach the air in their own hour (no credit). Outside air, infiltration and latent heat are
  unchanged.
- **The hourly step** (`hourly_loads.calculate_room_hours`): the room's 24 hours of heat gains are worked as before,
  then conduction through outside walls and roofs is spread by its CTS and every gain is split and spread by the RTS.
  Each component keeps its heat gain (`heat_gain_sensible_kw`) beside its cooling load, and a day's cooling load adds up
  to its heat gains. Heating, annual energy and the reviewed (gated) dynamic-mass paths are unchanged.
- **Hand-worked rooms:** the worksheet takes each room's series as declared numbers
  (`tests/reference/draft_reference_series.json`, written by `make_draft_reference_series.py`; a test checks the draft
  still produces them). It works the splits and the hour-by-hour spreading itself. All four cases agree at every check
  hour within 2 %, and the sizing month, hour and total within 1 %.
- **Effect** (AI off, copies of the saved jobs; every total repeats exactly):

  | Job | Before (kW) | After (kW) |
  |---|---|---|
  | Butcher Buffet (26 Oct) | 51.18 | 49.40 (engineer 35–50; not tuned) |
  | Butcher Buffet (20 Oct) | 53.05 | 50.52 |
  | Casa Nova | 31.78 | 30.85 |
  | Happy Cap | 2.98 | 2.88 |
  | Global Exchange | 1.19 | 1.14 |
  | Set 15 | 73.8 | 63.4 |
  | Set 16 | 45.2 | 41.5 |
  | Set 18 | 29.9 | 27.0 |

  Shops drop 2–5 % (people and lights spread over the day). Houses drop 8–14 %: sun through glass is held in the floor
  and comes back in the evening.
- **Not done:** the browser check of the result page. The preview tool reads a launch configuration outside this
  branch, and the server wasn't started any other way. The page only gains a "stored heat" assumption row per room.

## The report to send (13 November)
"Create the report" on the Results page now builds a report someone can quote from
(`backend/contractor_report.py`, laid out by `productization.build_job_report_package`):

- **Label:** every page says "Preliminary estimate from the drawings — not an engineer's design", with a banner on page
  one.
- **Units to consider:**
  - One ducted system sized on the whole building's busiest hour.
  - A wall split in each room, sized at that room's own worst hour of any month, plus fan heat at the report's 3.2%.
  - Each is the smallest nominal size at or above the load, with no margin.
  - A room too big for one wall split is offered a ducted size. A building above 24 kW is offered several units, or a
    packaged unit if it isn't a house.
  - Rooms under 1 kW are flagged as usually served from a neighbouring room.
  - The size steps are Daikin Australia's range (`config/unit_sizes.json`, checked 13 November 2026). The report says
    these are nominal ratings at AS/NZS 3823.1.1 T1, and to check the chosen model at the design temperature and its
    sensible capacity.
- **Charts** (inline SVG, so the HTML and PDF need no scripts or network):
  - the sizing design day hour by hour, stacked by where the load comes from;
  - the peak in each month;
  - the load by group at the sizing hour.

  Colours are the dataviz skill's validated categorical palette in fixed order, and each chart has a table beside it.
- **Values used, room by room:** people, W per person, lights, outside air, every equipment item with where its value
  comes from, and the wall, roof and glass values with their sources.
- **Still to confirm, assumed for this job, not included, sources.** The per-surface notes are grouped by rule (98 lines
  became 10 on set 16), and stored heat is one line for the job.
- **The PDF:** printed with Playwright when installed, otherwise a local headless Chrome or Chromium in a throwaway
  profile (`ARCHIE_CHROME` can name one), otherwise the old one-page text PDF. Python Playwright isn't installed here,
  so until now every PDF was that text fallback.
- **Fixed on the way:** the "still to confirm" list said "generic single glazing (U 5.8, SHGC 0.45)" for houses on the
  NatHERS default window, and "what is above the tenancy isn't answered" for houses with roofs
  (`backend/result_status_service.py`).
- **Checked:**
  - `tests/test_contractor_report.py` covers sizes, worst hour, chart data adding up, grouping and the page. When a
    Chrome is present it also prints a real PDF.
  - `tests/test_result_status.py` covers the glass and roof lines; `tests/test_job_report_package.py` is updated.
  - Reports were built from copies of Butcher Buffet and set 16 (AI off), and every page of each PDF was read.
- **Not done:**
  - The Results page itself doesn't show the unit sizes yet; they are only in the report.
  - The "Create the report" button wasn't clicked in a browser: the preview tool reads another worktree's launch
    configuration. The button's server call is covered by the package test.

## Next
1. Check the stored-heat model against ASHRAE's full Tables 16, 19, 20 and 21 once the chapter is in hand (TODO.md).
2. A listed-equipment case (commercial kitchen, hooded and free-standing) in the worksheet.
3. Partner review of the equipment typical values against ASHRAE Fundamentals Ch. 18.
4. More engineer numbers: Butcher Buffet's room breakdown, the first client's job, design partners.
