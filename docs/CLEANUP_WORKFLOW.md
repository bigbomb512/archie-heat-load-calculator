# Cleanup Workflow

Use this file when the user asks for a code cleanup, efficiency review, refactor pass, or project organisation pass.

This workflow does not replace `RULES.md`. Always read and follow `RULES.md` first, then apply this file.

## Goal

Find messy code, inefficient code, duplicated logic, confusing files, dead code, risky assumptions, and poor project organisation.

Do not jump straight into edits. The first output must be a cleanup report and plan.

## Step 1: Inspect

Read the relevant files before judging them.

Check for:

- files that no longer serve the current HVAC project goal
- duplicated logic across modules
- stale provider names, commands, or docs
- functions that do too many unrelated things
- hard-coded assumptions that will fail on different PDFs
- inefficient loops, repeated PDF/image processing, or unnecessary file reads
- unclear names, confusing responsibilities, or misplaced files
- unused imports, unreachable code, dead scripts, and old experiment code
- missing tests for risky behavior
- output folders, generated files, or local-only files that should not be committed

## Step 2: Report Before Editing

Write a concise report before making code changes.

The report should include:

- what is inefficient, messy, or risky
- why it matters for this project
- the specific files involved
- whether it is worth fixing now or later
- a proposed cleanup plan in priority order
- tests or practice cases that should be run after the cleanup

Be brutally honest. If the project is already clean enough, say so and do not invent problems.

## Step 3: Wait For Approval

Do not implement the cleanup until the user clearly says to proceed.

Acceptable approval examples:

- "do it"
- "go ahead"
- "implement the cleanup"
- "fix the code now"

If the user only asks for the report, stop after the report.

## Step 4: Implement

After approval, make the smallest useful cleanup.

Rules:

- preserve current behavior unless the user asked for behavior changes
- keep file moves and renames understandable
- update imports, commands, docs, and tests when files move
- delete dead code only when it is clearly unused or obsolete
- avoid broad rewrites that make bugs harder to spot
- keep code concise and professional

## Step 5: Verify

After edits:

- run syntax checks
- run the project tests
- run at least one realistic dry-run or sample PDF check when the cleanup touches PDF, AI packet, frontend/backend, or vision code
- explain what passed and what could not be tested

## Final Response

Keep the final response short.

Include:

- the cleanup completed
- the files changed
- the tests/checks run
- any remaining risks or follow-up cleanup worth doing later
