# Note-only rooms, refused questions and services-only sets (2026-11-02)

Running two new drawing sets (Happy Cap, an 11-page architectural set, and Global Exchange's mechanical set) showed
three gaps that would affect any job. The user's decisions on 2026-11-02 are in brackets.

## 1. Rooms named only in a note
- **What happened:** Happy Cap's general notes copy a line from another project: "ALL NEW WALLS IN DRINK BAR AREA TO BE OF SOLID MASONRY CONSTRUCTION".
  - Room inference took "BAR" as a room.
  - The room questions told the AI the tenancy has a Bar.
  - The outline step drew a 0.58 m² Bar.
- **Why notes can't simply be ignored:** they are real evidence elsewhere. Butcher Buffet's Kitchen and Shop appear only in its finish legend, and Casa Nova's Kitchen only in a note.
- **Evidence kind.** `ai/room_inference.py` records it for each label:
  - a word in a line of four or more words is `note`;
  - otherwise it is `plan_label`;
  - a room printed anywhere on the plan counts as printed.
- **What the AI is told.** The naming and outline questions list note-only rooms with their note, and say such a room may not be in the tenancy.
- **Removal** (`autonomous_tasks_service.rooms_not_on_plan`). A note-only room is removed, labelled, when the outline step was asked to draw it and no answer gives it 2 m² or more of floor.
  - It is worked out from the current answers each time, so a later answer that finds the room brings it back.
  - It is left out of the room list (`room_inference_service`) and gets no corner question.
  - The rooms screen lists it under "Not counted as rooms".
- **Effect on saved jobs:**
  - Happy Cap: the Bar is removed.
  - Butcher Buffet and Casa Nova: nothing is removed.
  - Room labels on all 17 saved jobs are unchanged; only the kinds are new.
  - The prompt change means jobs with note-only rooms are asked their room names once more on their next run (Happy Cap, Butcher Buffet's Shop, Casa Nova's Kitchen).

## 2. A question refused twice held the job at the drawings stage
- **What happened:** Happy Cap's corner questions for Bar and Shop were each refused twice by the checker. The drawing step correctly stopped sending them, but they stayed "waiting for reply", so the job never left the drawings stage.
- **Change:**
  - A waiting question with two refused replies counts as **not answered**, not waiting.
  - It is shown as "Not answered" with the checker's reason, and isn't resent by a run or a resume.
  - The operator's deliberate "Try again" still asks it again, and so do changed drawings.
  - The record keeps its status, so the Phase 1 supervisor can still try one targeted check.
- **Questions added after the last run** (Butcher Buffet's was added by a program update) are offered as "Check and resume" in Drawings. Nothing is sent without that.
- **Change from the plan:** the plan proposed skipping corner questions for closed outlines. That was dropped, because corner questions deliberately snap every AI sketch to the drawn corners.

## 3. Services-only sets
- **What happened:** Global Exchange's mechanical set has no architectural plan, so no room question was ever asked. The trace screen still said to upload the architectural drawings, under the 2026-10-04 rule that the 2026-10-16 decision superseded.
- **Change:**
  - With no floor plan in the set, `_p0_page_selection` uses the scaled services plans (`ai/vector_geometry.fallback_services_plan_pages`), choosing per level the page with the most printed dimensions. For Global Exchange that is p5, the fitout ductwork plan, which shows the kiosk.
  - The scale comes from printed areas or dimensions, else from the sheet scale alone, labelled unchecked (`_p0_calibration_ready`).
  - A hand trace still needs a printed dimension; its message now says the AI traces at the sheet scale.
- **Tasks with nothing to ask are now "not needed", not blocked** (`NotNeeded` reasons):
  - no printed dimension line on the plan;
  - a plan page with no image to check for a north arrow;
  - the kitchen task, once the outlines are finished and no room is a kitchen.
- **Site:** a title-block line naming a place, level and venue ("International Kiosk, Level 2, Western Sydney Airport") is now a site clue. Global Exchange had no street address or "tenancy" word, so nothing was found before.

## Verification
- `tests/test_note_rooms_and_services_sets.py`, plus updated drawing-task, fallback-trace and browser tests.
- Full suite: 1,086 Python tests and 55 browser tests pass.
- Offline rebuilds on copies of both jobs, with no AI calls:
  - Happy Cap: the Bar is removed; the two corner questions are not answered; room naming is asked again.
  - Global Exchange: the site and wall-style questions are ready to send on p5; the missing dimension and the image-less north pages are not needed.
- **Not yet run live.** A live run of each needs the user's OK. Global Exchange's area at the sheet scale is unchecked, because the set prints no building dimension.
