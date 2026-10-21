# Drawing workflow recovery

Unfinished preparation, page reading, equipment reading, room tracing, room/site review and the empty-room model supervisor expose a recovery control. The status endpoint supplies `recovery: {action, label, reason}`; clients do not decide recovery availability from a generic failure flag.

| State | Control | Request |
| --- | --- | --- |
| Running | Restart this step | `action: "restart"` |
| Stopping | Restarting… (disabled) | Poll existing request |
| Failed, interrupted or stale | Try again | `action: "resume"` |
| Waiting | Check and resume | `action: "resume"` |
| Completed | None | None |

The existing POST endpoints accept resume/restart in addition to their existing actions. Restart requests include an optional `expected_run_id`; a late duplicate targeting an older run joins the current state instead of restarting its replacement. Envelope recovery explicitly sends `scope: "phase1_envelope"`. Authentication and existing consent/provider checks remain in force. An unmet prerequisite is returned in `recovery.reason`; clicking recovery does not approve consent or bypass the prerequisite.

## Cancellation and reuse

Restart is cooperative. A request is persisted against the existing run, which stops scheduling AI calls and publishing domain results at cancellation checkpoints. In-flight calls may finish or time out; validated page/section replies and raw skill/drawing replies are cached independently of publication. A replacement waits for the worker to exit, reuses matching evidence fingerprints, and validates cached proposals before applying them. A refused reply is removed from the recovery cache, so deliberate retries can obtain a corrected answer. Each drawing-task run retains its bounded attempt limit.

Run creation and restart requests use thread and filesystem locks. Repeated requests deduplicate, including requests from another local server process. Publication checks run ownership and pending cancellation. If a request exceeds the expected timeout, status reports stalled recovery and retains the restart request without launching another worker. Orphaned requests after a server restart expose Try again.

A submitted provider call can still incur its normal charge. Restart cannot revoke a request already submitted to an external provider.

## Empty-room calculation

`GET /api/job-calculation` adds:

- `readiness: {ready, reasons, usable_rooms, exceptions}`.
- `offer_start`: true only when ready and automatic worker startup failed for this review.
- `automatic_start_problem`: the startup failure when the recovery action is available.

The same readiness function gates manual and automatic Phase 1 starts. Relevant drawing jobs must be settled, with no pending restart, and a current envelope review must exist. At least one resolved room must have a positive finite area and assembled envelope/glazing inputs, or an explicit envelope-not-applicable finding. Unsupported rooms remain exceptions; the existing report retains detailed contribution exclusions and assumptions.

A completed review or the last settling prerequisite triggers the server handoff. On server startup a background reconciliation recovers missed handoffs for settled reviews; it does not resume failed/interrupted AI or calculation jobs. Set `ARCHIE_RECOVER_HANDOFFS=off` to disable that startup reconciliation (the browser test server does this to isolate saved user projects). Stale/missing calculation inputs are rebuilt locally before readiness is checked. No browser timer authorizes a calculation. The normal path starts automatically; Calculate now is reserved for failed automatic startup, and an actual failed calculation offers Try again when its prerequisites are ready.

Changed input fingerprints continue to mark saved results stale. Recovery does not change heat-load equations or claim engineering validation.

## Verification

Where each step is comes from one model, `backend/job_progress.py`: the step table, the prerequisite check (`stage_blocker`) and the job's stage (`milestones`), with `job_runner.step_state` turning every step's own status word into waiting, active, stopping, done or failed. Every step reply carries that `step_state`, and the Drawings screen reads it. `tests/test_job_progress.py` covers the vocabulary, the stage rule and the jobs list agreeing with the job screen. `tests/test_workflow_recovery.py` covers the reported early-button state, partial evidence, explicit internal rooms, pending restarts, startup failures, worker cancellation, cached reply reuse, repeated requests, another server process, orphaned jobs, and skill scope preservation. Existing service tests cover provider failures, partial page retries, calculation/report behaviour and evidence reuse.

`frontend/tests/job-preview.spec.mjs` checks automatic startup, gated manual recovery, running/stopping/resumed controls, protected status fetches, failed polling, and page-reading progress. Screenshot artifacts are written to `frontend/test-results/recovery-pages-reading.png` and `frontend/test-results/recovery-restarting.png` during that suite.

## Empty-room model supervisor

The room/site skill review is limited to sheet identity, revision scope, page relationships, site clues,
address selection and room identity/use. Drawing tasks supply geometry and envelope inputs. A separate
`phase1_supervisor_job.json` BackgroundJob follows the review in `job_progress.STEPS`; its status and recovery
endpoint is `/api/phase1-supervisor`. Calculation readiness requires this job to have settled with a current
source fingerprint. Old skill manifests containing an `agentic` object remain readable, but that object no
longer schedules skill investigations.

The supervisor rebuilds current calculation inputs, audits them deterministically and locally previews the
empty-room calculation. It queues every material gap in priority order, with up to `ARCHIE_AI_PARALLEL_CALLS`
provider requests simultaneously (default 8, shared with page reading, equipment reading and skills). Network
answers are cached before cancellation checks; publication uses the owning job's guarded writes and the task's
current run identity. Task replies, including rejected and failed replies, stay in their existing run archives.
After applying a fix, dependent drawing packets and the assembled model are refreshed before re-auditing.

There are at most two targeted attempts per stable gap, an early stop when neither model inputs nor the gap
set changed, and a three-cycle backstop. There is no spend cap. `ai_usage.jsonl` records `supervisor:<task>`
calls and reported cost, including failed replies; the screen also says when prices were not reported.
HTTP 429 / too-many-requests responses retry inside the provider's whole-request deadline, honouring
Retry-After or using exponential backoff with jitter. They are not logged as additional paid answers.

Remaining material gaps are retained as supervisor exceptions in the Phase 1 report and Rooms screen.
A completed supervisor means its bounded investigation finished; it does not certify complete engineering evidence.
The normal calculation job publishes the result after the supervisor settles. Operating loads still start only
when the user continues to that stage. No web research is performed by this supervisor.
