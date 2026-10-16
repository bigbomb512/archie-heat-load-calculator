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

### Every room's area, without anyone tracing (user decision 2026-10-16)

The system finds every room's area itself; a person is never asked to draw. Each step runs only when the one before
found nothing, and the result says which was used:
1. an area printed on the drawings (room tags, area schedules);
2. the room's outline (its enclosed walls, or the AI tracing it), scaled by a printed dimension that agrees with the
   sheet's scale; if no printed dimension can be read, by the sheet's printed scale alone ("scaled from the sheet's
   printed scale 1:50; no printed dimension could be read to check it"), corrected when the scale names a larger paper
   size than the PDF, and refused if it implies an implausible plan size;
3. no outline: one AI step asks for each remaining room's printed area, printed dimensions, or its length and width
   measured on the plan at the sheet's scale;
4. one room left over: the tenancy's printed total area less every other room ("tenancy area remainder").
A printed area beats an AI outline; an outline beats an estimate, printed dimensions or a remainder; an area typed by
a person beats everything. A room with an area but no outline gets its floor and ceiling or roof; only its walls stay
unassessed. While a step is still running the job shows "Finding the area of n room(s)"; only if every step finished
without an area is the person asked to type it.

### Every surface of every room (user spec 2026-10-16)

Phase 1 is each room's empty-room load, itemised. Every traced room gets every wall (one per outline side: its length
x the ceiling height), its ceiling or roof and its floor, each with its area, U-value, the temperature beyond it and
its kW, shown room by room on the Results tab. A surface next to conditioned space (another room, a neighbouring
tenancy, an air-conditioned mall, a floor above) is listed at the room's own temperature, so it shows 0 kW with the
reason instead of disappearing. Walls between two rooms use the partition U-value. Doors and windows are closed;
equipment is listed for Phase 2 but adds no heat here.

What the AI could not settle never removes a surface; it gets a labelled fallback, said once per room:
- a wall the AI couldn't classify: along another traced room it is a wall to that room, otherwise a wall to a
  neighbouring tenancy;
- a roof with no answer: the roof rule below;
- no ceiling height: the 2.7 m preliminary default;
- a wall or floor to an unconditioned space with no answered temperature: outdoor temperature minus 3 K (ASHRAE
  buffer-space estimate).

Known gaps: the sun on an exposed roof is not counted yet (no cited horizontal solar profile; conduction only), and
wall and floor materials read by the skills are not yet applied to the traced surfaces, which use the assumption pack's
U-values unless a construction is answered.

### Phase 1 defaults that keep it running without a person (user decisions 2026-10-15)

- **Roof:** the AI decides from the ceiling plans, sections and notes; when they don't settle it, the most likely case
  is used and labelled (a slab over the ceiling or a building above → not exposed; otherwise exposed). No question is
  sent to the contractor; the operators can correct it on Walls & roof.
- **Walls to a mall or another tenancy** count as zero (conditioned neighbours), stated in the result.
- **Weather location:** the operators' choice on the Project tab wins. Without it, the AI picks the nearest listed
  AIRAH location from the site details (address, site text on the drawings, job name, title block; a consultant's
  office is not the site), and the result labels the design days as picked by the AI.
- **Floor:** a slab on the ground, shown or assumed as the most likely case for a shop, adds no cooling load and is
  listed as an assumption (ASHRAE preliminary ground-contact rule). A floor over a car park, void, basement or
  suspended slab is counted against the buffer-space estimate until that space's temperature is answered.
- **Coverage:** the result lists the rooms included and left out. When no wall, roof or window of the included rooms
  is resolved, Phase 1 is blocked with that reason rather than showing 0 kW. A room whose every wall and ceiling face
  conditioned space (a floor above, neighbours, a mall) is resolved: its envelope load is a real 0 kW and the result
  says why.

## Phase 2: Operating loads and complete room load

Starting from the Phase 1 room results, the AI resolves and adds the room's operating gains and air loads:

- people, using room type, size and documented seating or occupancy where available;
- lights and equipment, using project evidence first and the maintained generic-value database for supported fallbacks;
- ventilation and airflow, using drawing evidence, approved rules and source-backed research when enabled.

The complete result is calculated separately for each room and includes the Phase 1 base load plus the resolved Phase 2 loads. Each fallback and unresolved item is labelled with its source and effect. A missing optional detail must not stop calculation of the rest of the job.

## Implemented workflow

Phase 1 now starts automatically when the AI has read the drawing and the current evidence is ready. It has its own background calculation and saved report. It rebuilds the AI preliminary model from current drawing evidence, automatically includes candidates with a resolved comfort-HVAC use and area, and calculates them with the existing hourly envelope and glazing method. It does not wait for the contractor to accept individual findings or confirm the room list. Supported controlled defaults allow the base subtotal to proceed when details are missing; unsupported or unresolved candidates appear as exceptions and keep the coverage status incomplete. Phase 2 keeps the full-load calculation and adds the available operating-gain and airflow inputs.

Both reports remain draft estimates. A room the AI cannot resolve to a supported use and area cannot be given a defensible base load; the job reports that exception rather than fabricating geometry. The base report carries the source input fingerprint, assumptions, exclusions and room-level results so it can be compared with the complete Phase 2 result.
