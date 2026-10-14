# Parent playbook: opaque_envelope

Use `surface_inventory.proposal` as the canonical draft inventory, including
when no thermal-surface ledger exists yet. Keep stable candidate IDs and shared
boundaries once. Draft estimates may use cited, scale-calibrated AI geometry
and drawing dimensions; preserve their preliminary status and uncertainty.
Only validated geometry and complete opening coverage can establish final net
opaque areas. Unknown opening area stays null. Construction and boundary state
are independent decisions. Handoff to the draft resolver without changing the
reviewed envelope model.

When opening sightings have not yet been extracted, run the opening matcher
from the drawing pages and pass its matched glazing-area proposal to area
derivation. Do not assume that missing register data means zero openings.

Never use missing registration or engineering-review status to suppress a
cited preliminary estimate. Use each candidate surface ID from the inventory,
calculate the measurements supported by the supplied drawing evidence, and
leave unsupported values null with a specific limitation. The draft resolver
is allowed to show such estimates with their provisional status; this does not
make them validated or accepted ledger values.
