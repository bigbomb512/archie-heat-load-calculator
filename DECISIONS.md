# Decisions made during autonomous builds

Each entry: date, task, the choice made, why, and how to undo it. Entries marked **[needs engineer]** are calculation
questions the build skipped. A person must decide them.

<!-- Newest first. -->

## 2026-11-28 — Task 9: self-check limits (provisional)
- **Choice:** a result stays Preliminary while any room on the room list isn't counted; for houses, also while fewer
  than 75% of the scheduled windows are on a wall, or while the load is outside 39–108 W per cooled m². The checked
  set 17 placed 86% of its windows and the PDF-only runs 54%. The checked houses are 49, 83 and 90 W/m², and the band
  runs 20% beyond the lowest and highest. Zero-load rooms aren't cooled floor. Labels only; no load value changes.
- **Why:** only three houses have checked answers, so these limits are a first cut. Revisit them when more checked
  houses exist.
- **Undo:** change `WINDOWS_PLACED_SHARE` / `HOUSE_W_PER_M2` in `backend/result_self_checks.py`, or drop the call in
  `result_label_service`.

## 2026-11-28 — Tasks 8 and 10 re-scoped from what the data showed
- **Task 8:** windows placed only by a label are 0–4 per job, and those rooms have no outline to take a facing from.
  The real loss was rotated tags (printed up the page) that were never read. That was fixed instead (PR #39). It moves
  the Turramurra reference from 14.48 to 17.20 kW once its windows are re-placed: 7 real windows, none placed twice.
- **Task 10:** the safety-factor, site-location and design-weather editors no longer exist. All three inputs are in the
  result's input fingerprint. The one stale path, a weather pick made from an earlier address, is now labelled
  (PR #41); the location is kept so no load moves silently.
- **Undo:** close the PRs; the original task text is unchanged in the Build queue.

## 2026-11-28 — Task 11 blocked
- **Choice:** not tested locally, because installing the new versions needs a download. Posting the findings as PR
  comments was refused by the session's permission settings. The findings are in the session summary in `TODO.md`.
- **Undo:** n/a. A person can post them, or allow `gh pr comment`.

## 2026-11-27 — PR #33 now targets main and carries #31
- **Choice:** #31 (quick-fix save test and its two fixes) was closed without merging, while #33 was stacked on its
  branch. #33 was pointed at main and main merged into it (no conflicts; both checks exit 0, 117 Playwright passed), so
  merging #33 lands both.
- **Undo:** point #33 back at `auto/quick-fix-save-test` and reopen #31.

## 2026-11-27 — Decision (person): next Build queue, tasks 6–11
- **Decision:** scoreboard baseline, building-feature coverage map, windows on a wall, self-checks, stale-value checks,
  dependency PR checks, in that order. The coverage map was added because houses vary a lot (split levels, voids,
  granny flats, alterations); the rule stays that work must help every job, so it maps which features the permitted
  cases cover and where the scoreboard fails, instead of adding handling for one building.
- **Undo:** reorder or remove the items in the Build queue.

## 2026-11-27 — Decision (person): wall-legend notes stay for review
- **Decision:** keep the notes for review until an engineer approves a method. The reader finds labels such as "Dincel
  200", "brick cavity" and "stud wall" and records the page. It identifies neither a U-value nor which walls use the
  type (`ai/construction_u_values.py`). Setting a number from the label would turn an unresolved drawing note into a
  calculation input. PR #32 shows the notes and keeps them out of the calculation.
- **[needs engineer]:** (a) what evidence is enough to match a legend type to each wall; (b) the cited U-value for that
  wall. Until both are settled, nothing uses the notes. Answers go through the existing review and cited-value route.

## 2026-11-27 — Decision (person): Quick-fix Cancel asks first (PR #33)
- **Decision:** with unsaved fixes, Cancel asks "Discard unsaved fixes?" (Keep editing / Discard fixes); with none, it
  leaves at once. Unsaved means the form differs from what was last saved, not that any input happened.
- **Choices made building it:** the confirm is inline in the sticky save bar (`role="alertdialog"`, Escape keeps
  editing), because the frontend has no modal pattern yet. "Unsaved" reuses `collectChanges`, the same comparison the
  save sends, so the guard and the save can't disagree. The PR is stacked on #31 (`auto/fix-cancel-guard` from
  `auto/quick-fix-save-test`) because both change the same lines of `fix.js`. **Undo:** revert PR #33.

## 2026-11-27 — Task 4, same room name twice on a level (no change)
- **Choice:** no change made; marked done. **Why:** the queue item copied a set 17 note from 2026-11-14. Since then
  `84e129d` (2026-11-15) numbers same-named house bedrooms with a wall between them as Bedroom 1, 2, … in every outline
  step and the naming step. `1b2a40a` (2026-11-18) numbers every other repeated name "Ensuite (1)", "(2)". Both have
  passing tests (`tests/test_autonomous_tasks.py`, `tests/test_repeated_room_names.py`).
- **Choice:** bedrooms keep "Bedroom 1" rather than the queue's "Bedroom (1)". **Why:** "Bedroom N" is the existing
  convention and matches printed house numbering. Changing it would rename saved jobs' rooms and their identities
  (`room-use:<level>:bedroom-1`), and so their answers. **Undo:** n/a. If "(N)" is wanted, it needs a saved-job
  migration like the one in `1b2a40a`.
- Shops and other non-dwellings are deliberately not numbered (existing behaviour). Unchanged.

## 2026-11-27 — Task 3, wall-legend notes (PR #32)
- **Choice:** the notes go in the Operating loads questions panel. **Why:** there is no separate "What we need to find"
  page any more; `job_service.open_information_needs` says that list is the one Operating loads asks, and the data
  already reaches that screen through `/api/skill-workflow` `answer_options`. No backend change was needed. **Undo:**
  remove `wallLegendMarkup` and its call in `frontend/js/job/operating.js`.
- **Choice:** one line per wall type with all its pages, instead of one line per (page, type) row. **Why:** the same
  legend is printed on many sheets. **Undo:** render the rows as they come.
- **[needs engineer]** (unchanged): the U-value for each printed wall type (Dincel, brick cavity, stud) and which walls
  it applies to. Nothing in this PR uses them in the calculation.

## 2026-11-27 — Task 2, quick-fix save flow (PR #31)
- **Choice:** when the save works but the load update fails, the message now says "Saved, but the load didn't update: …
  Save again to retry the update." Pressing Save again re-sends the same fixes (the server's save is idempotent for
  them) and runs the update again. **Why:** the old "Not saved or not updated" made a saved fix look lost. A separate
  "Retry the update" button would be cleaner but adds UI. **Undo:** revert the `catch` block in `frontend/js/job/fix.js`.
- **Choice:** empty typed U-value, SHGC and shade-depth boxes are sent as `""`, not `0`. **Why:** the server's existing
  `_number` check then says "Enter the … as a number" instead of a misleading range error. No validation was loosened.
  **Undo:** replace `typedNumber(...)` with `Number(...)`.
- **Not changed in #31:** the form's own Cancel link left without the unsaved-fixes prompt. A person has since decided
  Cancel must ask (see the Cancel entry above, PR #33).
- **Note:** `frontend/tests/job-preview.spec.mjs:296` failed once in a full run and passed 3/3 alone and in the re-run:
  flaky, unrelated.

## 2026-11-27 — Task 1, Results page unit sizes (PR #30)
- **Choice:** the unit sizes come from a new read-only `/api/unit-sizes` endpoint, loaded after the page shows, rather than
  being added to `/api/job-plan`'s `final_report`. **Why:** `room_sizing` needs the full hourly report
  (`scenario_results`), which the job plan deliberately leaves out to stay small. The Results page then calls
  `job_plan_service.get` a second time. **Undo:** remove `api_unit_sizes` and the `data-unit-sizes-panel` section.
- **Choice:** no new design-day chart. **Why:** Results already draws one from `design_day_profile` (same groups as the
  report); the new spec checks it renders. **Undo:** n/a.
- **Choice:** the panel is hidden when the result is out of date, as the report button is. **Undo:** drop the `stale`
  condition around the panel.
- **Note:** `frontend/tests/room-joins.spec.mjs:68` failed once in a full run and passed alone and in the re-run: flaky,
  not related to this change. Not touched.

## 2026-11-27 — Session housekeeping
- **Choice:** during the build, the Build queue ticks in `TODO.md` and this file stayed uncommitted in the main checkout
  instead of going into each task's PR. **Why:** `CLAUDE.md`, `DECISIONS.md` and the Build queue section were never
  committed to main, so committing them on every `auto/*` branch would make every PR carry them and conflict with each
  other.
- **Decision (person, 2026-11-27):** commit all three files in one docs PR, merged before the next autonomous run. Each
  task PR can then commit its own tick and decisions. Concurrent branches that edit the same queue lines or append
  decisions can still conflict, so merge or update those entries in sequence. Only these three files are staged; the
  checkout's other untracked files are left out.
