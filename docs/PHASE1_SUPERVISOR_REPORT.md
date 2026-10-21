# Phase 1 model supervisor implementation report

The supervisor now checks the model used by the empty-room calculation. It no longer expands the skill review into envelope investigations. Software and offline checks are complete; live-provider extraction accuracy and engineering validation were not exercised.

## File-by-file changes

| File | Change |
| --- | --- |
| [backend/phase1_supervisor_service.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/phase1_supervisor_service.py) | New deterministic model audit, priority queue, dependency waves, two attempts per stable gap, no-progress stop, three-cycle backstop, durable recovery state and spend/status. |
| [backend/skill_workflow_service.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/skill_workflow_service.py) | Restored the six-task Phase 1 skill scope; removed the proposal supervisor/new agentic manifests; retained old manifest reading, autonomous cited address selection and cut-off retry; added supervised room-use usage labels. |
| [backend/autonomous_tasks_service.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/autonomous_tasks_service.py) | Builds targeted packets through existing builders; fingerprints the note; preserves completed answers and rejection feedback when refreshing; supports targeted room outline/size and ceiling checks. |
| [backend/drawing_tasks_service.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/drawing_tasks_service.py) | Cached targeted transport, serial checked application, and failed-reply archival using the existing drawing-task archive and validators. |
| [backend/ai_provider.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/ai_provider.py) | Shared ARCHIE_AI_PARALLEL_CALLS setting (default 8), Retry-After/exponential-jitter rate-limit recovery within the request deadline; removed web-search adapter. Cut-off retry kept. |
| [backend/page_inventory_service.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/page_inventory_service.py) | Reads the shared concurrency setting at execution time. |
| [backend/page_extraction_service.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/page_extraction_service.py) | Equipment reading uses the same shared concurrency setting. |
| [backend/job_progress.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/job_progress.py) | Adds the separate model-check step after the room/site skill review. |
| [backend/job_runner.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/job_runner.py) | Adds the finished-supervisor handoff to the existing calculation startup/recovery mechanism. |
| [backend/calculation_service.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/calculation_service.py) | Waits for a current settled supervisor, starts it automatically, and keys recovery/handoff markers to its result fingerprint. |
| [backend/ai_preliminary_service.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/ai_preliminary_service.py) | Local calculation previews can return without publishing a report; published Phase 1 reports retain supervisor exceptions, spend and freshness metadata. |
| [backend/web_app.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/backend/web_app.py) | Adds authenticated status and editor-only recovery endpoints at /api/phase1-supervisor. |
| [frontend/js/job/drawings.js](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/frontend/js/job/drawings.js) | Separate recovery-enabled model-check stage; human progress, cycle, found/fixed counts, exceptions and spend; confirmed polling state is retained. |
| [frontend/js/job/rooms.js](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/frontend/js/job/rooms.js) | Shows unresolved model checks alongside the empty-room result. |
| [ai/skills/project_location_weather/address_confirmation.md](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/ai/skills/project_location_weather/address_confirmation.md) | Clarifies that site and weather selection belong to the drawing site task and existing weather picker, without web research. |
| [config/archie_subskills_v1.json](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/config/archie_subskills_v1.json) | Matches the address skill constraint to the retained site/weather ownership. |
| [docs/WORKFLOW_RECOVERY.md](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/docs/WORKFLOW_RECOVERY.md) | Documents supervisor recovery, readiness, durable state, attempts, costs and rate-limit behavior. |
| [docs/AI_PROPOSAL_PROMPTS.md](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/docs/AI_PROPOSAL_PROMPTS.md) | Documents the actual model supervisor and corrects the outdated roof fallback table to the existing most-likely-case decision. |
| [tests/test_phase1_supervisor.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/tests/test_phase1_supervisor.py) | Audit/ranking, targeted fingerprints, cache reuse, correction feedback, bounds, dependency refresh, empty models, cancellation/restart/orphans and parallelism. |
| [tests/test_ai_parallelism.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/tests/test_ai_parallelism.py) | Shared setting and rate-limit behavior using only a local HTTP server. |
| [tests/test_skill_workflow_service.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/tests/test_skill_workflow_service.py) | Restores trimmed-scope expectations and removes tests of the removed proposal supervisor. |
| [tests/test_skill_case_file.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/tests/test_skill_case_file.py) | Restores trimmed Phase 1 provider-call expectations. |
| [tests/test_ai_provider.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/tests/test_ai_provider.py) | Removes web-search tests, retains provider/cut-off tests and updates bounded rate-limit expectation. |
| [tests/test_workflow_recovery.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/tests/test_workflow_recovery.py) | Includes the settled supervisor prerequisite and removes obsolete extra proposal-supervisor call counts. |
| [tests/test_phase1_after_review.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/tests/test_phase1_after_review.py) | Checks that model supervision precedes calculation, then existing automatic calculation behavior is retained. |
| [tests/test_operator_copy.py](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/tests/test_operator_copy.py) | Checks that displayed supervisor copy does not expose internal identifiers. |
| [frontend/tests/job-preview.spec.mjs](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/frontend/tests/job-preview.spec.mjs) | Checks progress, spend, exceptions, restart/resume, failed polling and report exception display; saves the browser screenshot. |

Removed: `backend/phase1_research.py`, `config/phase1_research_allowlist.json`, `tests/test_phase1_research.py`, and `OpenRouterProvider.propose_with_web_search`. No database was added.

## Saved-job audit

The most recent saved model for each job was copied to a temporary directory before audit. Reads, local rebuilding and local calculation previews were performed on those copies. Provider creation was patched to raise if requested. Both snapshots were current, and both local previews succeeded. No AI/provider requests were sent.

### Butcher Buffet

Job: `20260226-butcher-buffet-_-melrose-park-construction-rev-b_2_-1792493211`.

| Rank | Room | Gap | Drawing task to re-ask | Pages |
| --- | --- | --- | --- | --- |
| 2 | Bar | Finish the unanswered drawing check for Bar: finding which wall is the shopfront. | `P4_storefront_wall` | 20 |
| 2 | Kitchen | Finish the unanswered drawing check for Kitchen: finding which wall is the shopfront. | `P4_storefront_wall` | 20 |
| 2 | Shop | Finish the unanswered drawing check for Shop: finding which wall is the shopfront. | `P4_storefront_wall` | 20 |
| 3 | Shop | Check Shop's shopfront glass against the elevation; no glass reached the empty-room model. | `P4_storefront_wall` | 26 |
| 7 | Bar | Check Bar's envelope: its calculated load is zero, so verify every boundary and opening. | `P3_boundaries` | 20, 21, 22 |
| 7 | Kitchen | Check Kitchen's envelope: its calculated load is zero, so verify every boundary and opening. | `P3_boundaries` | 20, 21, 22 |

The six gaps combine into fewer independent packets: the three unanswered shopfront placements and missing glass refer to the same plan packet. Bar and Kitchen require boundary checks. Validated repairs are re-audited before dependent opening tasks run.

The latest saved job differs from the older expected failing result. Its cooled rooms already have drawing-derived heights: Bar 3,025 mm, Kitchen 2,600 mm and Shop 3,665 mm. Coolroom and Freezer each still have six unknown wall edges and a 2,700 mm fallback, but both are classified `refrigeration_process`, outside the cooled Phase 1 scope. The supervisor does not silently reclassify them or spend calls on excluded loads. Their inclusion would require a separate scope decision.

### Casa Nova

Job: `260320---interior-life---casa-nova-gosford---rev-d-3-1792318359`.

| Rank | Room | Gap | Drawing task to re-ask | Pages |
| --- | --- | --- | --- | --- |
| 1 | Staff | Check how Staff is used so its empty-room calculation includes the right space. | `room_identity_use` | 32, 33 |
| 2 | Bathroom | Finish the unanswered drawing check for Bathroom: working out what each wall faces. | `P3_boundaries` | 32 |
| 5 | Bar | Check Bar's perimeter: its walls all face other rooms or tenancies although a shopfront elevation exists. | `P3_boundaries` | 32, 33, 40 |
| 5 | Bathroom | Check Bathroom's perimeter: its walls all face other rooms or tenancies although a shopfront elevation exists. | `P3_boundaries` | 40, 46 |
| 5 | Kitchen | Check Kitchen's perimeter: its walls all face other rooms or tenancies although a shopfront elevation exists. | `P3_boundaries` | 33, 40 |
| 5 | Staff | Check Staff's perimeter: its walls all face other rooms or tenancies although a shopfront elevation exists. | `P3_boundaries` | 32, 33, 40 |
| 6 | Bar | Check what is above Bar; its roof or ceiling exposure is unknown or assumed. | `P5_roof` | 32, 33 |
| 6 | Bathroom | Check what is above Bathroom; its roof or ceiling exposure is unknown or assumed. | `P5_roof` | 46 |
| 6 | Kitchen | Check what is above Kitchen; its roof or ceiling exposure is unknown or assumed. | `P5_roof` | 33 |
| 6 | Staff | Check what is above Staff; its roof or ceiling exposure is unknown or assumed. | `P5_roof` | 32, 33 |
| 7 | Bar | Check Bar's envelope: its calculated load is zero, so verify every boundary and opening. | `P3_boundaries` | 32, 33 |
| 7 | Bathroom | Check Bathroom's envelope: its calculated load is zero, so verify every boundary and opening. | `P3_boundaries` | 46 |
| 7 | Kitchen | Check Kitchen's envelope: its calculated load is zero, so verify every boundary and opening. | `P3_boundaries` | 33 |
| 7 | Staff | Check Staff's envelope: its calculated load is zero, so verify every boundary and opening. | `P3_boundaries` | 32, 33 |

Casa Nova has fourteen ranked gaps. They include unresolved Staff room use, an unanswered boundary task, the all-internal tenancy perimeter, fallback roof exposure and zero-load room results. Its saved text/spatial evidence did not yield labelled-height pages through `autonomous_tasks.height_labels`, so this audit does not claim it has a matched P7 height label. The labelled-height condition is exercised separately by a regression fixture.

The original saved job files had no modification timestamps newer than the temporary-copy creation time. The original jobs were not repaired or rewritten during these checks.

## Verification

- `python3 -m unittest discover -s tests`: **941 tests passed in 20.326 s**. Provider key environment variables were cleared for this command.
- `cd frontend && npm test`: **exit 0**, including its Python script checks and **48 Playwright tests passed in 8.5 s**.
- Python compilation and `git diff --check`: passed.
- Browser screenshot inspected: `frontend/test-results/supervisor-progress.png`. It shows the separate model-check stage, current human-readable investigation, cycle, found/fixed counts, spend, exception and recovery control.
- Local copied-job audit and preview elapsed time: Butcher Buffet **30.38 s**, Casa Nova **54.31 s**; no paid calls or API cost.

## Limits and decisions

No requested product decisions were reversed. Phase 1 skill scope is trimmed; site/weather ownership and the Phase 2 value ladder remain as specified. The cut-off retry remains. The concurrency setting is shared, there is no spending cap, and operating loads do not start automatically.

The differences from the expected Butcher Buffet audit are evidence differences in its newest saved model, described above. The audit follows the actual cooled-room scope rather than changing it to match an older expected result.

The user server was not started, stopped or restarted, and no commit was made. Existing unrelated workspace changes were preserved. Loading the new backend requires the normal application restart when the operator is ready.

Live-provider repair success, a rerun of the original user jobs, and engineering accuracy remain unvalidated by this task: real provider calls and saved-job mutation were explicitly prohibited. The next live verification is to resume the model-check step after loading the new backend and inspect the resulting evidence and exceptions.
