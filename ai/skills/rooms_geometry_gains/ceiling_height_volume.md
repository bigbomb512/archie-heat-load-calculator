# Subskill: ceiling_height_volume

- Search room-specific RCP/section/elevation notes first, then explicitly
  scoped zone/level notes. Determine floor-to-finished-ceiling height, not
  door/joinery height or ceiling-void height.
- Use `indexed_fact_evidence` as a locator for explicit height annotations
  already read from the cited pages; check the attached page and supplied
  room outline before assigning each height to a room or zone.
- Respect suspended ceilings, double-height rooms, raked ceilings, and datum
  differences. Never spread a shared height beyond its named/outlined scope.
- Calculate volume only from current resolved area × height in metres; retain
  formula and operands. Missing applicability or conflicting heights remain
  unresolved for the ceiling resolver.
