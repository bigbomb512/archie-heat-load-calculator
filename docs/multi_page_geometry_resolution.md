# Multi-page geometry resolution

The evidence pipeline indexes the complete architect packet and assigns each
sheet an evidence capability. A plan can witness room geometry, an elevation or
opening schedule can witness opening dimensions, a reflected-ceiling/service
sheet can witness ceiling and lighting context, and sections can witness
vertical relationships. Renders and 3D views are retained as visual
cross-checks only; they never supply scale, primary dimensions, room areas, or
thermal properties.

Geometry records use stable source-based identities and retain every page,
drawing number, excerpt/coordinate, extraction method, and witness. Room
geometry is eligible for activation only when a closed, calibrated boundary is
linked to a room label and supported by the required independent witnesses.
Conflicting or single-witness records remain proposals or blocked review items.

The normalized graph is written into `architect_evidence_fusion.json` and is
consumed by `building_evidence.json` and `calculator_draft.json`. This slice
does not activate the envelope model and does not change cooling totals.
