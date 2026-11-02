# Conditional tenancy outline — 26 October 2026

## Change

The tenancy outline is requested after page-specific room naming is answered (`applied` or `below_accuracy_bar`), and only while rooms still need the AI outline route. The existing naming/printed-area missing-room calculation is shared with the room-outline builder; current trace methods and pending outline, corner and room-size work are checked as well.

Wall-enclosed, printed-area, room-size and operator traces do not require tenancy clipping. Waiting or blocked tenancy questions that are unnecessary become `not_needed`, with this saved reason and applied note:

> Every room on this plan already has an outline from the walls the drawing encloses (or the operators), so the tenancy outline isn't needed to trim AI-drawn rooms.

A withdrawn task is settled, excluded from waiting/blocked counts and supervisor unanswered-task checks, not sent by the drawing worker, and shown as “Not needed” in Drawings. Captured worker queues recheck current status before sending. New unoutlined rooms allow a new run even when the previous input fingerprint matches. Tenancy corner questions are not generated when every room has an authoritative trace. Existing answered tenancy evidence is preserved.

The unlabelled-floor check (`P0_unnamed_space`) is unchanged.

## Follow-up: plan levels and inference reuse

The tenancy check now reads inferred rooms once and passes that list to `_p0_outline_room_names`. On jobs with multiple main plans, an inferred room is included only when its `source_pages` includes the plan page, or its normalized level matches that plan's level. Unknown levels match only other unknown levels. Single-plan jobs retain the previous all-room fallback. Page-local traces, naming packets and pending tasks retain their existing behaviour.

`_inferred_room_data` uses a bounded eight-entry memory cache keyed by the resolved job-copy directory and the mtime, ctime, size and inode of each of `ai_input.json`, `drawing_coverage.json`, `spatial_ocr.json` and `vector_geometry.json`. Adding, editing, replacing or removing an input invalidates the entry. Returned rows are deep copies, so callers cannot change a cached result. No disk cache or job files are added by this cache.

Regression tests cover two different plan levels (withdraw level 1's unnecessary question while still asking on level 2 after naming), source-page and level matches, unknown-level fallback, single-plan compatibility, invalidation of every input, protection against caller mutation, and one inference execution across all packet builders in a refresh.

### Before/after timings

Measured on disposable copies, with `backend.ai_provider.get` patched to raise. Each timed operation started with an empty inference cache; the copies were restored to the same saved task/input state before full refresh. Existing PDF caches were retained. These are single wall-clock measurements, not guaranteed performance bounds. This turn's fresh after measurements ran before the full test suites, with no server or worker launched by the check.

| Job / operation | Before | After | Inference executions before → after |
|---|---:|---:|---:|
| Butcher Buffet: `_tenancy_rooms_needing_outline(root, 20)` | 2.1810 s | 1.7286 s | 2 → 1 |
| Butcher Buffet: full `_refresh_geometry_tasks(root)` | 17.3820 s | 12.0052 s | 6 → 1 |
| Casa Nova: `_tenancy_rooms_needing_outline(root, 33)` | 3.7959 s | 3.1667 s | 2 → 1 |
| Casa Nova: full `_refresh_geometry_tasks(root)` | 34.5931 s | 12.4435 s | 11 → 1 |

The cached room lists exactly matched direct uncached inference on both copies. In this fresh timing rerun, inference executed once in each full refresh. The same rooms needed outlines before and after: none for Butcher Buffet, Bathroom and Shop for Casa Nova. The fresh behavior rerun on separate copies again changed Butcher Buffet's tenancy task from `waiting_for_reply` to `not_needed` with waiting count 0 before full refresh; refresh then created only the separate `P0_unnamed_space page-20` waiting task. Casa Nova's existing `below_accuracy_bar` tenancy answer remained unchanged with waiting count 0. The floor-check exception below remains unchanged.

The repeat check confirmed Butcher Buffet's `not_needed` tenancy status and waiting **0** before the unlabelled-floor question was created; full refresh then had waiting **1**, solely `P0_unnamed_space page-20`. Casa Nova retained its `below_accuracy_bar` tenancy answer and waiting **0**. Provider attempts were **0** throughout. SHA-256 and file-list comparisons confirmed both original directories remained unchanged.

The timing rerun used a temporary inline Python harness from the repository root: it copied each source directory with `shutil.copytree` into a `TemporaryDirectory`, cleared the inference cache before each measurement, wrapped `room_inference.infer` to count calls, and patched `backend.ai_provider.get` to raise. The harness was not persisted. The saved-job behavioral checks used separate fresh copies with the same provider patch. The before measurements above are the recorded pre-fix baseline; this turn reran the after measurements.

The requested suite commands (from the repository root, with credentials explicitly unset) were:

```sh
OPENROUTER_API_KEY= OPENAI_API_KEY= ANTHROPIC_API_KEY= DEEPSEEK_API_KEY= python3 -m unittest discover -s tests
(cd frontend && OPENROUTER_API_KEY= OPENAI_API_KEY= ANTHROPIC_API_KEY= DEEPSEEK_API_KEY= npm test)
```

## Saved-job checks: copies only

Both full job directories were copied to a temporary audit directory before checking them. `backend.ai_provider.get` was patched to raise throughout the checks. Only the copies were refreshed; neither a drawing worker nor a paid provider request was started.

| Check | Butcher Buffet …1792650630 | Casa Nova …1792837361 |
|---|---|---|
| Main plan | Page 20 | Page 33 |
| Rooms needing tenancy clipping | None; all six current traces use enclosed walls | Bathroom and Shop use AI-drawn outlines |
| Tenancy status | `waiting_for_reply` → `not_needed` | Existing `below_accuracy_bar` answer kept |
| Immediately after tenancy decision | 24 tasks; waiting 0, blocked 0, not needed 1 | 43 tasks; waiting 0, blocked 0 |
| After full geometry-task refresh | 25 tasks; waiting 1, blocked 0, not needed 1 | 43 tasks; waiting 0, blocked 0 |
| Remaining waiting task | `P0_unnamed_space`, `page-20` | None |
| Actual stage after full refresh | `drawings` | `phase1` |
| Saved Phase 1 complete | Yes | No |
| Saved final result current | Yes | No final result available |
| Original directory contents and SHA-256 hashes | Unchanged | Unchanged |
| Attempted provider calls | 0 | 0 |

### Acceptance discrepancy: Butcher Buffet stage

With the tenancy task withdrawn, the actual waiting count is zero. Feeding that progress and the saved current Phase 1/2 result facts into `job_progress.milestones` gives `results`.

However, full refresh creates the previously absent `P0_unnamed_space` page-20 question. The actual resulting job status therefore remains `drawings`, with exactly one waiting task. Requiring both a full refresh with zero waiting tasks and preservation of the existing unlabelled-floor check is incompatible with this saved job's state. The check was not suppressed or falsely marked answered to satisfy the expected stage.

Next action on a live job, when authorised: the drawing worker answers this one floor-coverage question. No tenancy-outline or tenancy-corner call is needed for Butcher Buffet. Further work would depend on what that answer finds; this audit did not run it.

## Verification

- `OPENROUTER_API_KEY= OPENAI_API_KEY= ANTHROPIC_API_KEY= DEEPSEEK_API_KEY= python3 -m unittest discover -s tests`: **1,070 tests passed** (24.334 s in this verification run).
- `cd frontend && npm test`: **passed**, including its Python test-file loop and **54 Playwright tests** (9.9 s for the browser suite).
- New coverage also includes the level and cache tests listed above. Existing coverage includes authoritative trace methods, waiting/blocked withdrawal, exact saved explanation, delayed naming, AI outlines, new-room reopening with unchanged fingerprints, captured worker queue withdrawal, status reconciliation, and supervisor handling of withdrawn tasks.
- Existing tenancy corner/clipping tests pass. Browser regression checks show “Not needed” without a retry button. Desktop and 390px mobile screenshots were visually inspected; the explanation wraps within its stage.
- `git diff --check`: passed.

No API keys were printed, no live server or live job was stopped, and no commit was made. Software checks do not establish engineering accuracy of the saved room traces.
