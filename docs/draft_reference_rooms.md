# Reference rooms for the AI draft path

Three small rooms are worked by hand in an independent worksheet, then run through Archie's real draft path
(`ai/ai_preliminary.py` `assemble` → `calculate`, no AI) and compared part by part: every load component, at named
months and hours, plus the month and hour that size the system.

| Case | What it checks |
|---|---|
| **A. West shop**: 80 m² retail, Sydney, west wall 10 × 3 m with a 6 × 2.4 m shopfront, a 2-storey building 15 m opposite | wall sun (sol-air) and conduction, glass conduction and sun with the building's shadow, people, lighting, equipment allowance, outside air, opening hours |
| **B. Roof office**: 120 m² single storey, exposed roof, north window with a 1.5 m awning | roof conduction and flat-roof sun, north glass in summer vs winter under the awning, the sizing month |
| **C. Mall restaurant**: 150 m² dining with 100 printed seats, 40 m² kitchen with 600 L/s exhaust, shopfront to the mall | occupancy from seats, mall walls at no load, outside air = max(ventilation, kitchen exhaust) |

- Worksheet: `tests/reference/draft_reference_rooms.py`. It has no Archie imports and uses plain textbook equations:
  - sun position: Spencer/Iqbal;
  - sunlight on each surface: beam × cos(incidence), isotropic sky, ground reflectance 0.2;
  - awning and building shadows from the profile angle;
  - sol-air U·(α/h_o)·I with 0.052 m²K/W;
  - glass: A·(1 − frame)·SHGC·I;
  - outside air: ASHRAE Fundamentals Ch. 1 psychrometrics on standard air;
  - fan heat 3.2%.
- Declared inputs:
  - the rooms;
  - the assumption pack's product choices (U-values, SHGC, densities, W/m², opening hours, activity gains);
  - synthetic design and monthly temperatures;
  - Sydney's clear-sky sunlight (`tests/reference/sydney_clear_sky_2025.json`, generated once with pvlib's Ineichen model, which is not re-derived).
- Expected values: `tests/fixtures/draft_reference_rooms_v1.json`. Regenerate with
  `python3 tests/reference/draft_reference_rooms.py --write`, and never by calling Archie.
- Test: `tests/test_draft_reference_rooms.py`, with tolerances: each component ±2% or ±0.02 kW, latent ±3%, total ±1%, and the peak month and hour exact.
- `tests/test_pack_plausibility.py` keeps the pack's numbers in physical ranges, each with its source or marked as a sanity range.

## What passing means
The draft path does the arithmetic and physics as the worksheet writes them out, for these rooms. It is **not**
engineering validation:
- the worksheet and the code come from the same team, so a shared misunderstanding of a method could pass both;
- the clear-sky model isn't re-derived;
- this is not DA09 or Camel parity.

The Camel handout (`docs/reference_rooms_for_camel.md`) is the guard against shared misunderstandings: the same rooms entered in Camel by the contractor, compared in the parity harness (`docs/da09_camel_benchmark.md`).

## Findings when it was first run (2026-10-26)
- **Matched first time:**
  - wall and roof conduction;
  - wall and roof sun;
  - glass conduction and sun with the building's shadow and the awning;
  - people, lighting, equipment;
  - the sizing month and hour in all three rooms;
  - the worksheet's sun position, within 0.39° of pvlib over every sunlit hour of the year.
- **Outside air was about 8% lower in sensible heat and 5% lower in total.** The draft:
  - converted L/s at the outdoor state (about 1.14 kg/m³);
  - used dry-air heat capacity for sensible;
  - computed the indoor moisture with the legacy psychrometer approximation.
- **User decision:** use standard air (1.2 kg/m³) with moist-air sensible heat, the AIRAH/ASHRAE simplified method. The draft now does, and its indoor wet bulb uses the same thermodynamic equations as outdoors. All components now agree.
- **Fault check:** putting back the old 25% wall sun, or dropping outside air's per-m² part, fails 10 and 24 component checks respectively, each naming the room, month, hour and component.

## Adding a case
Add a room to `CASES` in the worksheet (its walls, glass, shading, seats or exhaust, and the months and hours to
check), run `--write`, and make sure `proposal()` in the test can express it. A disagreement is a finding: find which
side is wrong and fix that, never the expected number.
