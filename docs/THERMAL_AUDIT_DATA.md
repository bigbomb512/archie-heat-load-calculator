# Thermal audit data

`GET /api/thermal-audit?project_id=<id>&month=1&hour=15` reads the current preliminary model, report and `thermal_audit_elements.json` sidecar. Month is 1–12; hour is 0–23. The route uses the normal project API access check. It never writes or calculates. A missing artifact or mismatched input fingerprint returns `thermal_audit_unavailable`.

The response has:

- `pages[]`: physical page, cited north record, traced room polygons, edge class, calibrated length and outward true bearing, plus windows with edge, offset, width, height, mark and position basis. If a legacy trace has no offset, the read-only response centres it and marks `assumed_centred`.
- `rooms[]`: room identity and provenance-tagged area, height, occupancy and design temperatures; this hour's component totals and internal loads; and `elements[]`.
- `rooms[].elements[]`: stable surface ID, component, recorded heat gain and cooling load (kW), wall/window linkage, formula with available operands substituted, per-value provenance, stored-heat method and share released from earlier hours of the repeated design day.
- `sun.hours[]`: precomputed altitude, azimuth and irradiance for all 24 hours of the requested month, or `null` with a reason.
- `summary`: cooled area by provenance, placed versus scheduled windows, north source, ceiling-height sources, three biggest element load groups and high-impact preliminary assumptions at the sizing hour, and explicit exclusions or partial scope exceptions.

`thermal_audit_elements.json` is saved before the main report is compacted. It copies Archie's per-element engine rows for every calculated design day. The stored-heat earlier-hour share is recorded by the existing time-series calculation; no load is recalculated by the API. Any value without an engine row is `null` with a reason. Component and element values are rounded by the calculation; reconciliation allows **0.002 kW** per component per room-hour. Audit results remain draft data, not engineering-reviewed results.

## Provenance flags

Flags come from structured origin and basis fields, never words in a description. If an older record lacks an origin, the flag is `null` with a reason instead of an inferred claim.

| Flag | Basis recorded by the job |
|---|---|
| `measured_from_drawing` | Printed room area or height, calibrated person-drawn trace, or a page bearing agreeing with the saved survey axis. |
| `read_from_schedule` | A selected schedule or certificate record, including certificate glazing. |
| `ai_estimate` | AI-drawn outline or AI-selected evidence without a measured/survey basis. |
| `standard_value` | Explicit cited standard or handbook selection: site design weather, NatHERS default glass, or lighting density with a recorded NCC source row. |
| `assumed_default` | Controlled preliminary profile value without a cited table, including generic shading and construction defaults. |
| `edited_by_user` | Typed or overridden area, height, north, glazing, shading, or internal gain; identity and time are included when recorded. |

The preliminary assembler records `area_value_origin` for openings, `glazing_value_origin`, `shading_value_origin`, `lighting_value_origin` with its cited table, and weather `value_origin` when it chooses those values. The audit reader uses room `materialized_fields.origin`, trace `declaration_source`, north `declaration_source` plus survey axis, domain-resolution origins, and saved overrides. `summary.north_by_page` retains the flag for each plan when the plans have different origins. A wall orientation is taken from its traced edge's outward bearing, with that page north's provenance. Missing edge or north geometry leaves orientation unknown.
