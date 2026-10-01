# Project task completion rules

Apply these rules to every task in this repository. They guide the work; they cannot guarantee that every defect will be found.

## Before changing anything

- Read the relevant code, tests, and project documentation, including any existing method gates or exclusions.
- Turn the user's request into a concrete checklist of outcomes. Preserve the full requested scope; do not quietly replace it with a smaller foundation, prototype, or partial implementation.
- For research or engineering work, identify authoritative sources and record which requirement or calculation each source supports. Do not infer that an input field means the factor is implemented.
- If scope or a consequential engineering decision is genuinely ambiguous, ask before choosing. Otherwise proceed using existing project conventions.

## Implement and verify

- Trace every requested outcome to code, configuration, UI, or documentation as applicable. Mark each as implemented, tested, blocked, or explicitly out of scope, with a reason. A method gate or TODO is not implementation.
- Implement the complete authorized scope. If external data, an approved method, or a decision prevents completion, finish independent work, name the exact blocker, and do not report the task as complete.
- Add or update tests that exercise meaningful behavior, boundary cases, and failure states. Run the relevant tests and checks after the final code change; report commands and results accurately.
- For browser/UI work, use `docs/BROWSER_TEST_LOOP.md` where applicable and repeat the browser check after fixes. For calculation changes, use independent reference cases and stated tolerances where available; synthetic sanity tests alone do not establish engineering validation.
- Re-read the final diff and check that no requested item was omitted, no unrelated user changes were overwritten, and documentation matches actual behavior.

## Completion report

- State what was completed and how it was verified.
- List any requested outcomes that remain incomplete, gated, unvalidated, or blocked, with the reason and next concrete step.
- Never claim “all fixed,” “zero issues,” production readiness, or engineering accuracy unless the evidence and checks actually support that claim. Do not disguise a partial result as completion.
