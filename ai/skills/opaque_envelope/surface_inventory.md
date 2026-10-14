# Subskill: surface_inventory

- Inspect primary layout and setout plans, sections, elevations, and details for external walls, roofs,
  floors, ceilings, partitions, voids, and adjacent-space relationships.
- Reconcile the primary layout sheet with the supplied closed room/tenancy
  boundary candidates and the setout plan before assigning a surface owner.
  A functional label such as Bar, Kitchen, or Staff does not itself establish
  a separate room polygon; preserve identity, level, and process-zone conflicts.
- Initialize the proposed inventory when no ledger exists. Generate stable
  proposed IDs from the cited drawing page, owner room ID and physical edge
  reference; use any existing canonical IDs when a ledger is present.
- A cited closed room boundary is evidence for proposed boundary edges. Keep
  each edge's geometry reference and source citation, and distinguish an AI
  traced edge from a vector-confirmed edge. Do not report its area here.
- When the packet supplies `surface_geometry_pages` and attached image
  coordinate frames, register each traced edge to the closest cited vector
  candidate in canonical full-page pixels. Copy the vector candidate ID
  verbatim; if no candidate matches, retain the visual anchor and mark its
  coordinate registration unresolved. Do not compare attached-render pixels
  directly with canonical coordinates without applying the supplied transform.
- Assign room/zone/level owners and one canonical identity to shared
  partitions. Distinguish physical type from thermal role and exposure.
- For a continuous external shopfront or other fenestrated wall, inventory
  the full physical wall plane as a wall host whose gross extent includes its
  doors and glazing. The opening matcher registers each opening against this
  host, and the area task subtracts them to obtain net opaque area. Do not
  replace this host with an `opaque-components` row for only the piers, head,
  or sill bands; those components do not represent the wall's gross extent.
  Split a curved return from a straight elevation only when the plan/elevation
  evidence establishes separate physical planes, and keep a stable host ID
  for each plane.
- Keep visible surfaces in the inventory even when exposure, adjacency, or
  construction is unresolved. Use `unresolved` thermal role and null
  adjacency when necessary; later tasks resolve these fields or attach a
  controlled Phase 1 assumption.
- Prefer the primary layout, setout plan, dimension plan, ceiling plan,
  shopfront elevation, and sections as a linked evidence set. The primary
  layout is required for mapping physical boundaries to the supplied room
  geometry; the setout and elevation sheets alone are insufficient.
- Return expected-but-unseen surfaces as coverage issues; never fabricate a
  wall from an open or unverified outline, or duplicate reciprocal
  partitions.
