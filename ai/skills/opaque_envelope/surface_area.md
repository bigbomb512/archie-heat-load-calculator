# Subskill: surface_area

- Produce draft estimates from all cited drawing evidence, including AI-traced
  geometry, when its page scale or a printed dimension provides a stated
  measurement method. A draft estimate is useful even when the geometry is not
  engineering-validated; attach row-level citations and a formula/method, and
  preserve the source and confidence limitations.
- Derive wall gross area from traced or dimensioned length × the height zone
  that the drawings support. Derive horizontal area only from a room-specific
  footprint; do not duplicate an overlapping room or ceiling zone.
- For an inventory wall host that has a matching scaled elevation, trace the
  complete visible host plane from its finished-floor boundary to the soffit,
  beam, or other visible top boundary. Use the attached page's own calibrated
  x/y scale for the full host span and height; do not substitute a nearby door
  dimension (such as a local 2940 annotation) for the wall height. Return a
  cited draft gross wall area even if openings, level, or revision still need
  reconciliation. Keep net opaque area null unless opening coverage is
  complete.
- Preserve gross area and subtract openings only when their dimensions,
  surface host, and complete host coverage are established. `opening_area_m2`
  is null when unknown, never zero by default. Keep net area null when opening
  coverage is incomplete; record that limitation in the formula/method.
- Set `opening_coverage` to `complete` only when every opening on that host is
  accounted for, `not_applicable` only for a surface that cannot host an
  opening, and `unknown` otherwise.
- Use `glazing_properties.proposal` to get areas for matched openings. Mark a
  host complete only when the supplied plans/elevations cover its full extent
  and all visible doors/windows are represented by matched opening IDs.
- Determine that coverage from the host's physical extent and the
  `cross_sheet_opening_match.proposal` rows: if every visible opening on a
  fully shown host has the same `host_wall_id`, and each of those IDs has a
  non-null scale-derived area, the host opening coverage is complete for this
  draft calculation. A prerequisite's `needs_review` status does not by itself
  make geometric coverage incomplete. Keep unresolved host correspondence,
  unmatched sightings that could lie on that same face, or a missing opening
  area as explicit exceptions; do not let an unrelated unmatched return or
  section view invalidate a separate fully covered straight host.
- Do not withhold a supported gross area solely because opening coverage is
  incomplete. Do not present that gross area as net opaque area.
- If the plan scale, applicable height, or room/surface mapping cannot be
  supported from the attached evidence, leave that specific area null and
  explain the missing evidence. Never turn an unresolved value into zero.
- Return one area row for every `surface_id` in `surface_inventory.proposal`,
  even when some or all measurements are null. This keeps unmeasured items
  visible for the draft workflow.
- For floor candidates, use a matching room-specific closed polygon from the
  supplied geometry evidence and its calibration operands to calculate draft
  gross area. Cite the source plan and state the polygon/calibration formula.
- The supplied area proof may have `proof_status=geometry_review_required`
  alongside a valid drawing scale, closed boundary points, calibration and
  shoelace derivation. You may use those operands for a draft gross estimate;
  preserve the proof status as a limitation and do not call it validated.
- Do not require a registered thermal-surface ledger or an engineering
  validation label to emit a draft estimate. Preserve those facts as
  limitations; they govern final acceptance, not whether a cited estimate is
  proposed.
