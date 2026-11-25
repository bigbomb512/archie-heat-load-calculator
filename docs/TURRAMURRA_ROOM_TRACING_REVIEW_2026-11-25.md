# Turramurra room tracing review (25 November 2026)

## Evidence and current state

Source: saved CC08 ground-floor plan, PDF page 8 in job `…1794567651`; the
rendered page is `output/web_review/…1794567651/chatgpt_packet/screenshots/page_008_floor_plan.png`.
The printed **236.2 m²** is labelled *Ground Floor GFA*. It describes the
whole level, not one room. The drawing separately labels the garage, scullery,
laundry, kitchen, sitting, ensuite, WIR, dining, linen, entry, media, living,
bedroom 5 and study. Alfresco and the external entertainment area are outside
the house's enclosed floor.

`python3 -B scripts/eval_room_tracing.py <saved job>` reports 15 located
ground-floor labels, 8 saved ground-floor trace parts and 10 labels outside
those saved traces. For the first floor it reports 16 located labels, 4 trace
parts and 13 labels outside saved traces. These are **saved-state** counts;
the improved label reader has not been applied to this job.

The existing wall-derived candidates cover only 4 of 15 located ground-floor
labels in the prior read-only replay. The plan's linework mixes wall faces,
joinery, floor tiling and annotations, so a candidate polygon cannot be treated
as a room solely because it contains a name. The raster-wall fallback at its
default settings found no enclosed areas on this plan in a read-only check.

The evaluator was run read-only over all 12 saved house jobs on 25 November:
310 saved trace parts, 156 located printed labels outside traces, 6 traces
containing different printed room names, 38 trace/name mismatches and 30 trace
parts below 2 m² or above 120 m². These counts are diagnostic flags, not
independent judgments that every flagged room is wrong.

Claude's earlier projection added uniquely named wall candidates to copies:
Turramurra `…1794567651` went from 15 to 21 trace parts, 23 to 18 untraced
printed labels, and 9 to 13 placeable windows of 28. A different Turramurra
copy gained a newly merged trace. These are projections, not validated traces,
saved-job updates or a recalculated load.

## Safe recovery route

1. Keep `OPEN_FLOOR_MAX_M2 = 80`. The 236.2 m² GFA does not justify raising
   a per-room limit.
2. Use the existing room-by-room P0 outline and corner-validation path on a
   disposable copy for labels that lack a trustworthy wall-derived candidate.
   Keep each room's printed label, page, floor, outline and area evidence linked.
3. Reject candidates that contain different room labels, overlap another
   accepted room, cross exterior space or fail the existing minimum-area and
   sketch checks. Preserve uncertain rooms as needing review.
4. Compare every accepted outline against the plan image and the other floors.
   Only then rerun window placement on the copy. Report trace coverage,
   merged/mismatched traces, plausible areas and placed windows for every saved
   house, including any regressions.
5. Present the reviewed outlines and counts before applying them to saved jobs
   or recalculating cooling loads. A changed window count alone does not
   validate a room outline or engineering result.

The existing read-only evaluator is `scripts/eval_room_tracing.py`. The
recovery and validation code is in `backend/autonomous_tasks_service.py` and
`ai/room_outline.py`; the 80 m² limit is `OPEN_FLOOR_MAX_M2` there.
