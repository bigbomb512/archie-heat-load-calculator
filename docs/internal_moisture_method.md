# Internal room moisture gains

The hourly cooling model supports an explicitly sourced, direct-to-room latent
gain for `vapour_gain`, `process_latent_load`, and `steam_gain` records. It does
not infer a moisture source from room use, equipment names, or drawings.

## Calculation basis

- An input in `W` is an engineer-supplied latent heat rate and is divided by
  1000 to produce kW.
- `vapour_gain` and `process_latent_load` may instead use `kg/h` or `g/h`.
  The V1 reference convention uses the zone latent-load definition in
  ANSI/ASHRAE Standard 140-2014 Addendum a: water is vaporised from a 0 °C
  reference and the resulting vapor is brought to zone temperature. The
  psychrometric enthalpy approximation is `h = 2501 + 1.86 × Tzone` kJ/kg, so
  `Qlatent = mdot × h`.
- `steam_gain` accepts `W` only. Steam mass flow needs a source-state enthalpy
  method; V1 does not infer one or add sensible heat from steam.
- All calculated moisture sources must have `air_path: "direct_to_room"`, a
  source, citations, `method_id: "room_internal_moisture_v1"`, and a dedicated
  24-hour schedule assigned at
  `schedule_assignments.moisture[component_id]`.

Do not count a source as a room moisture gain if it is fully exhausted before
mixing with room air. Its replacement-air and recirculation effects belong in
the air-side calculation. V1 does not yet calculate moisture diffusion through
opaque fabric or source-state sensible gains for process steam.

## Review gate and report status

The project method gate is stored as `internal_moisture_method_gate.json` and
is available through:

- `GET /api/internal-moisture-method-gate?project_id=<id>`
- `POST /api/internal-moisture-method-gate`

The default gate is `placeholder`. The latent contribution can appear in an
hourly draft result with full input trace, but an active moisture contribution
keeps project scope incomplete and suppresses the review-ready project peak
until a qualified HVAC engineer records a real approval and supporting
citations. Never enter invented reviewer details.

The gate does not validate an engineer's credential or replace a real
comparison case. Synthetic code tests prove implementation behavior only.

## References

- AIRAH, [DA09 Air Conditioning Load Estimation and Psychrometrics](https://www.airah.org.au/site/site/resources/da-manuals/da-09/Default.aspx), 4th ed. (2022), public scope includes internal/system gains, infiltration/ventilation, heat and water-vapour transfer, and applied psychrometrics. The calculation text is not reproduced here.
- AIRAH, [HVAC&R Skills Workshop Module 29](https://www.airah.org.au/Common/Uploaded%20files/Resources/SkillsWorkshop/sw029.pdf), discusses miscellaneous heat/moisture sources including escaping steam and stored radiant gains.
- ANSI/ASHRAE, [Standard 140-2014 Addendum a](https://www.ashrae.org/File%20Library/Technical%20Resources/Standards%20and%20Guidelines/Standards%20Addenda/140_2014_a_20170516.pdf), zone latent-load definition (water mass rate multiplied by vapor enthalpy at zone temperature).
- ASHRAE Handbook—Fundamentals, [Chapter 1: Psychrometrics](https://handbook.ashrae.org/Handbooks/F17/SI/f17_ch01/f17_ch01_si.aspx), space moisture-gain energy balance and source-vapor enthalpy treatment.
