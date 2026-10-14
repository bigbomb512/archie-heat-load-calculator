# Subskill: glazing_properties

- Resolve opening dimensions/quantity, opening area, glass area, whole-window
  U-value, and exactly one of SHGC or solar transmission.
- Use matched drawing dimensions or scale-calibrated visible geometry to
  propose opening area for the draft. A missing opening register, schedule,
  or engineering registration is not a reason to leave visible geometry
  unmeasured: use the supplied image-to-page frame and the scale printed on
  that specific drawing to trace the visible opening bounds. For a rectangular
  opening, calculate width_px × height_px × scale_x × scale_y; for an irregular
  opening, use the traced polygon area and the same axis-specific scale. Cite
  the drawing page and give the pixel operands, scale, formula and a draft
  confidence. Do not transfer a scale from another sheet or use a page whose
  scale conflicts with its applicable view.
- Return an area estimate for every matched opening ID whose complete gross
  opening outline is visible. Treat a continuous glazed run between two
  openings or a corner return as one composite opening when individual mullion
  bays cannot be matched; use its whole traced span and height, retain the
  composite ID, and lower confidence. For curved glazing, estimate developed
  plan length from the traced segments and multiply by the corresponding
  elevation height. Do not leave these areas null solely because individual
  bays, transoms, or product schedules are not registered.
- If part of an outline is hidden, the scale is not applicable, or the
  matched sheet does not show its full extent, leave only that opening's area
  null and state the specific missing geometry.
- Keep each opening linked to its matched room and host surface when the
  cross-sheet proposal supplies those IDs. Split a continuous frontage at
  visible door, pier and assembly boundaries; retain a combined run only when
  individual boundaries cannot be distinguished. Draft dimensions and areas
  remain useful even when their host or product match still needs review.
- If the traced glass boundary is visible, estimate glass area separately.
  Leave it null when frames or transoms cannot be separated reliably; do not
  silently equate glass area with the whole opening area.
- If no reliable material rating is shown, leave U-value and solar property
  null so the draft resolver can use its explicitly controlled generic
  preliminary glazing fallback.
- Source each property independently from a matched schedule, supplier
  record, accepted research candidate, released scoped pack, or explicitly
  allowed visual class; cite source and applicability for each.
- Do not equate glass-center U-value with overall-window U-value. Visual-only
  property estimates are provisional and cannot support reviewed activation.
