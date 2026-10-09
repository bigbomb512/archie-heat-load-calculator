# Subskill: room_boundaries_areas

- Use `room_candidates` from the current local room-inference and room-use
  artifacts as the identity list even when `building_evidence.spaces` and
  prior geometry proofs are empty. The first geometry pass is expected to
  turn those identities into boundary proposals.
- Select the correct floor-plan viewport and level. Identify its own printed
  scale; ignore title-block and inset-detail scales. Prefer explicit room-area
  notation, otherwise trace an ordered closed polygon along the room's actual
  inside-face boundary.
- Resolve physical thermal zones, not every functional label as a separate
  area. If a bar, buffet, and customer seating are continuous and not divided
  by a physical partition, return one connected hospitality-zone polygon and
  preserve the functional subareas as annotations; never count overlapping
  subareas twice. Keep a kitchen separate only where walls/doors or a clear
  system boundary supports separate ownership. At a doorway, close the room
  polygon at the threshold and record that area convention instead of leaving
  an artificial open loop.
- Calibrate image/vector coordinates only with the main viewport scale or
  dimensions whose extension lines, witness marks, or explicit references
  measure the traced wall segment. Do not use unrelated dimensions, another
  room's area, or visual proportions.
- For a dimension `D` linked to pixel/vector length `L`, preserve `D`, units,
  `L`, conversion, and derived scale. Cross-check independent dimensions;
  report disagreement beyond resolver tolerance. Compute polygon area only
  after scale calibration, retain unrounded vertices/operands, and cite the
  page/crop and each dimension link.
- Reject open, self-intersecting, branched, competing, wrong-level, or
  ambiguous-owner boundaries. Return the candidate and exact missing proof;
  one failed room must not suppress other rooms.
- Use `vector_geometry_pages`, `dimension_evidence`, and `spatial_room_evidence`
  as indexed references to the shared PDF extraction. Select the physical
  wall vectors that bound each room; do not assume every vector tagged
  `possible_wall_or_dimension` is a wall. Use the attached primary-plan image
  to distinguish wall lines from dimension strings, furniture, and details.
  The primary page's `line_candidates` are tuples in this order:
  `[candidate_id, x1, y1, x2, y2, role]`, where `[x1, y1]` and `[x2, y2]` are
  the line's start and end points in whole image pixels and `role` is `W`
  (possible wall or dimension), `C` (neutral vector context) or the
  extractor's role text; use the supplied IDs and endpoints verbatim in
  `walls`, never invent wall IDs.
  Their coordinates refer to the full-page `image_px` coordinate frame.
  Attached pages may be higher-resolution renders; use the per-page
  `attached_image_coordinate_frames` supplied in the prompt to convert points
  from the displayed image into the canonical vector frame. Never reject a
  candidate only because the render and vector screenshots have different
  pixel dimensions. Convert each displayed-image point using
  `canonical_crop_bbox_px` plus the respective `attached_to_canonical_scale`
  values. `boundary_points_px`
  and wall `start_px`/`end_px` must all use canonical top-left-origin
  `image_px`; use vector candidate IDs and endpoints verbatim when linking
  walls. Do not mix bottom-left `plan_px` points into that polygon.
  When the frame metadata supplies `confirmed_main_viewport_scale` and
  `scale_mm_per_canonical_px`, use that scale only for geometry on that
  confirmed main viewport; retain the scale and its PDF-page-size operands in
  `calibration`. Do not substitute an inset/detail scale or infer a scale from
  image appearance. A directly traced closed polygon plus this confirmed scale
  is sufficient calibration; dimension-wall links are still required when the
  scale is not confirmed or when the scale itself is being derived.
  If room labels are on an aligned finish/RCP sheet, map that sheet to the
  dimension plan using shared wall intersections/columns and preserve both
  page references; never transfer coordinates between sheets without stating
  the alignment evidence.
- Return each independently supportable room candidate even if another room
  has unresolved identity, level, or boundary ownership. An empty
  `building_evidence.spaces` list is not evidence that no rooms exist.
- For each candidate, return `room_id`, displayed `label`, physical `page`,
  `level`, `boundary_points_px` or an ordered `wall_ids` loop, and the cited
  wall records (`wall_id`, `line_start_px`, `line_end_px`). Return each linked
  dimension as a record with `dimension_id`, `value_mm`, measured pixel span,
  `target_wall_id`, and a short extension-line/reference reason. Include
  `scale_mm_per_px`, its calibration source and operands, any reported `area_m2`
  for a consistency check, source crop, confidence, and independent witnesses.
  These are proposals only: the geometry resolver recomputes area from the
  closed boundary and calibration and may block the candidate.
