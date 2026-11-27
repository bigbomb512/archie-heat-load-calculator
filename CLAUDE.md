# Autonomous Build Rules

These rules apply when a session is told to build autonomously (for example "work the build queue" or a `/loop`). During
those runs they override the "ask before choosing" line in AGENTS.md. Every other rule in AGENTS.md still applies.

## Loop
- Work through the **Build queue** section of TODO.md from top to bottom, one task at a time. Each task gets its own branch, `auto/<short-name>`, created from main.
- Don't stop to ask. Record every non-obvious choice in DECISIONS.md: date, task, choice, reason, and how to undo it.
- A task is done only when both checks exit 0 after the final change:
  - `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 tools/mandatory_smoke_gate.py`
  - `cd frontend && npx playwright test` (only if frontend files changed; headless only)
- Then mark the task [x] in the Build queue with its PR link, commit, push the branch (`git push -u origin auto/<name>`), and open a PR. Never merge.
- If a task still fails after 3 fix attempts, mark it [blocked] with the exact error, revert the branch to its last passing commit, and move on.

## Never, even when it seems to make things faster
- Delete, skip or loosen a test, or change what a test expects, to make it pass.
- Make paid AI calls (OpenRouter, Anthropic, OpenAI, DeepSeek) or run `scripts/retrace_job_copy.py --live`. API keys are blanked for these sessions; don't look for them.
- Modify anything in `output/` (saved jobs and backups). Work on copies in a scratch folder.
- Merge, force-push, push to main, or delete branches, worktrees or backups.
- Use a browser, computer use, or the web: no Chrome or Claude browser tools, no `open`, no `curl`/`wget`, no headed Playwright. Headless `npx playwright test` is the only browser allowed.
- Change calculation values or methods (U-values, weather data, load factors, standards) without a cited source. If a task needs one, mark it [needs engineer] in DECISIONS.md and the Build queue, and move on.
- Add frameworks (React, Tailwind, etc.). Use the existing CSS variables and patterns in `frontend/DESIGN.md`.

## Calculation check
- If a change touches ai/, backend/ calculation code, or config/, re-run the Turramurra recalculation on a copy of `output/web_review/*-1794567651` and record the kW in the PR. The current reference is 14.48 kW (16 rooms, 296 m²). Explain any change over 1%.

## Codex helpers
- Run Codex only like this, so it loads no plugins or MCP servers (no browser, computer use or web search) and its
  sandbox has no network (checked 27 Nov 2026):
  `env -u OPENROUTER_API_KEY -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u DEEPSEEK_API_KEY codex exec --ignore-user-config -m gpt-6-sol -s workspace-write -c web_search='"disabled"' --disable browser_use --disable computer_use --disable in_app_browser --disable apps -C <worktree> - < prompt.md`
- Never run Codex with `--dangerously-bypass-approvals-and-sandbox`, `-s danger-full-access`, or without `--ignore-user-config`.
- Codex's sandbox can't run git rebase/commit in worktrees or bind localhost. It writes code; git and the smoke gate run outside it.

## End of session
- Add a dated summary under the Build queue in TODO.md: what was done, the PRs opened, what's blocked, and what needs a decision.
