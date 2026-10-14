# Subskill: room_boundaries_areas

- Use `room_candidates` from the current local room-inference and room-use
  artifacts as the identity list even when `building_evidence.spaces` and
  prior geometry proofs are empty. The first geometry pass is expected to
  turn those identities into boundary proposals.
- Select the correct floor-plan viewport and level. Identify its own printed
  scale; ignore title-block and inset-detail scales. Prefer explicit room-area
  notation, otherwise trace an ordered closed polygon along the room's actual
  inside-face boundary.
- If no complete printed dimension chain is available, use the confirmed
  main-viewport scale and PDF page-size calibration supplied in the coordinate
  frame to calculate a preliminary AI-estimated area from a directly traced,
  closed polygon. This is authorized even when a sheet says "do not scale";
  identify that note in the proposal and label the area as an unverified draft
  estimate. Never call it a measured or construction-accurate area. Printed
  dimensions take precedence and any disagreement must be reported.
- Resolve physical thermal zones, not every functional label as a separate
  area. If a bar, buffet, and customer seating are continuous and not divided
  by a physical partition, return one connected hospitality-zone polygon and
  preserve the functional subareas as annotations; never count overlapping
  subareas twice. Keep a kitchen separate only where walls/doors or a clear
  system boundary supports separate ownership. At a doorway, close the room
  polygon at the threshold and record that area convention instead of leaving
  an artificial open loop.
- When a fitout plan presents one visibly open tenant space with functional
  labels such as counter, kiosk, and food preparation, prefer one whole-tenant
  conditioned zone unless physical partitions or an explicit HVAC boundary
  establish separate zones. Printed finish-schedule areas may corroborate the
  traced total when their extents and schedule entries are legible; do not
  mistake different finishes for separate rooms.
- For a whole-tenant candidate, trace the closed inside-face perimeter shown
  on the primary floor plan, including connected open areas around islands and
  counters. Assume doors, windows, and sliding doors are closed; close the
  boundary at their wall opening/threshold. Do not require an individually
  labelled room outline when the connected tenancy perimeter is clear.
- When the primary plan explicitly shows a confirmed scale and full-page
  coordinate calibration, trace that visible perimeter and return the ordered
  polygon with the exact scale operands. Do not leave geometry empty solely
  because every perimeter segment is not individually dimensioned; retain the
  preliminary estimate and site-verification caveat.
- If the plan clearly shows that labeled floor-finish regions together cover
  the entire connected tenancy, and the finish legend gives an area for each,
  use the sum as the tenancy's preliminary floor area even if the plan does
  not print one room-area label. Return the exact addends, formula, both page
  citations, and the assumption that the schedule covers the whole tenant.
  Treat different finishes as regions of one thermal zone unless physical
  partitions or an HVAC boundary prove otherwise. Keep the area distinct from
  the gross wall-to-wall rectangle and report any available dimension check.
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
