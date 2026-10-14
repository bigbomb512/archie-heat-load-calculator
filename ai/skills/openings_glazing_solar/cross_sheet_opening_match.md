# Subskill: cross_sheet_opening_match

- If no opening register or pre-extracted sightings exist, create draft
  sightings from visible doors, windows, sliding doors and shopfront glazing
  on the attached plan/elevation/section pages. The lack of a register is not
  a reason to return an empty list.
- Match plan openings to elevations/sections/schedules using tags, level,
  host wall, order, mullions, adjacent landmarks, revision, and dimensions.
- Give each visible opening a stable ID anchored to its plan page, host edge,
  and position. Cite the pages that establish its geometry and cross-sheet
  match. Keep room or host IDs null when the drawing does not support them.
- Compare plan edges with `surface_inventory.proposal`; use its exact
  `surface_id` as `host_wall_id` when the attached plan establishes the match.
  Use the surface's `owner_ids` for `room_id` when the opening lies within that
  traced owner boundary. Do not leave these IDs null merely because there is
  no released ledger.
- Keep repeated tags, mirrored facades, obsolete revisions, and competing
  room/wall owners as alternatives. Stable identity follows physical opening
  anchor, not row order.
- Unmatched sightings remain visible. Do not claim complete host-wall
  coverage until every opening is accounted for.
- Treat dimensioned opening width/height as usable draft geometry even when
  the product or glass properties are absent. Leave those properties to the
  glazing-properties task; do not omit the opening itself.
