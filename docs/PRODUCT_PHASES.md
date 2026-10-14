# Product calculation phases

The customer workflow has two calculation phases. Phase 1 is an AI-run calculation, not a queue of proposals for the contractor to accept one by one.

## Phase 1: AI base room load

The AI reads the architectural PDF and, for each room in scope:

- identifies the room and its floor area;
- resolves the room's boundary surfaces and their documented construction, including walls, floor, ceiling or roof;
- identifies windows and uses their area and documented material where available;
- treats doors, windows and other openings as closed for the calculation;
- applies the project's fixed design factors and calculates a separate base heat load for each room.

Use drawing evidence first. For room area, prefer a printed room area or complete dimension chain. When these are absent, a high-confidence AI-traced closed boundary may use the confirmed main-plan scale and PDF page-size calibration for a draft estimate, even if the sheet says not to scale. The result must retain that note as a caveat and cannot be described as a measured construction area. The scale conversion is recomputed from the PDF and printed scale. When the drawing omits another value, use an approved, controlled default only where the method allows it. Label every default and its effect in the result. Missing or conflicting information should become a concise exception for the contractor; it should not turn all otherwise resolved inputs into a manual acceptance queue or prevent independent rooms from being calculated. If the north reference is missing, the preliminary method uses its documented west-facing solar assumption and identifies it in the report.

Phase 1's output is the room envelope/base load. It excludes people, lighting, plug loads and other equipment, ventilation, infiltration and operational airflow. Those inputs remain available as evidence for Phase 2. The hourly method calculates evidenced externally exposed walls, roofs/ceilings, glazing, and resolved adjacent-space boundaries. A floor without evidence for its construction or boundary temperature is listed as unresolved and excluded from the subtotal; it is never represented as a zero contribution. A drawing-supported internal floor or a ground-contact floor may use its explicitly labelled preliminary method, with the assumption and source shown in the audit. A room with missing boundaries still receives a subtotal from resolved surfaces, while the report marks its coverage incomplete and lists the missing contribution as an exception.

## Phase 2: Operating loads and complete room load

Starting from the Phase 1 room results, the AI resolves and adds the room's operating gains and air loads:

- people, using room type, size and documented seating or occupancy where available;
- lights and equipment, using project evidence first and the maintained generic-value database for supported fallbacks;
- ventilation and airflow, using drawing evidence, approved rules and source-backed research when enabled.

The complete result is calculated separately for each room and includes the Phase 1 base load plus the resolved Phase 2 loads. Each fallback and unresolved item is labelled with its source and effect. A missing optional detail must not stop calculation of the rest of the job.

## Implemented workflow

Phase 1 now starts automatically when the AI has read the drawing and the current evidence is ready. It has its own background calculation and saved report. It rebuilds the AI preliminary model from current drawing evidence, automatically includes candidates with a resolved comfort-HVAC use and area, and calculates them with the existing hourly envelope and glazing method. It does not wait for the contractor to accept individual findings or confirm the room list. Supported controlled defaults allow the base subtotal to proceed when details are missing; unsupported or unresolved candidates appear as exceptions and keep the coverage status incomplete. Phase 2 keeps the full-load calculation and adds the available operating-gain and airflow inputs.

Both reports remain draft estimates. A room the AI cannot resolve to a supported use and area cannot be given a defensible base load; the job reports that exception rather than fabricating geometry. The base report carries the source input fingerprint, assumptions, exclusions and room-level results so it can be compared with the complete Phase 2 result.
