# Reviewed envelope and boundary calculation slice

This slice adds per-project reviewed envelope artifacts for cooling calculations.
It does not implement detailed glazing, dynamic conduction, geometric shading,
heating, AHU, plant, or annual analysis.

## Artifacts

Each project review directory may contain:

- `envelope_library.json` — versioned construction, window, and shading records.
- `envelope_model.json` — room/zone-owned boundary surfaces and their calculation
  state.

Construction records use `record_id`, `revision`, `review_status`, `source`, and
`citations`. Surface records use `surface_id`, `owner_zone_id`, `kind`,
`orientation`, `area_m2`, `construction_id`, `boundary_method`, and the same
review evidence. IDs are lowercase stable IDs.

`external` and `fixed_adjacent_temperature` are the only boundary methods that
can contribute. Fixed-adjacent surfaces require `adjacent_temperature_c`.
`outdoor_offset` and `proportional_ambient_difference` are retained as explicit
stored methods and are excluded until their method is reviewed and implemented.

## Calculation readiness

The reviewed model is inactive by default. Legacy `design_requirements.json`
surfaces continue unchanged until a user saves a reviewed model with
`active_for_calculation: true`.

When active, a surface is eligible only when its opaque construction, surface,
and enabled manual-solar inputs are confirmed. The calculation is:

`area × U-value × (surface boundary temperature − indoor dry-bulb) ÷ 1000`

For external surfaces, the boundary temperature is the active scenario outdoor
dry-bulb. For fixed adjacent-temperature surfaces, it is the reviewed entered
adjacent temperature. Manual solar retains the existing explicit designer-input
formula and needs its own confirmed source.

The active model blocks cooling reports when it contains provisional,
unsupported, or stored-not-calculated surfaces. This avoids silently omitting
glazing, frames, internal shades, overhangs, fins, reveals, or adjacent
obstruction geometry.

## Migration and API

`POST /api/envelope-model` with `action: "migrate_legacy"` copies flat legacy
surfaces into provisional, inactive records. It never rewrites the legacy input
artifact or marks imported values confirmed.

Use `GET`/`POST /api/envelope-library` and `GET`/`POST /api/envelope-model` for
the reviewed artifacts. Saving either artifact invalidates quick and hourly
cooling reports through their input fingerprints. The report records which
envelope source was used and lists included, blocked, and stored-only surfaces.

PDF/thermal-model/AI outputs remain proposals and evidence only. They cannot
automatically approve or populate calculation inputs.
