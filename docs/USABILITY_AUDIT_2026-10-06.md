# Usability audit — getting a heat load as a first-time contractor (2026-10-06)

Method: fresh upload of the Butcher Buffet set on the current code (`46abdbd` plus the fix below), walked
through as a contractor with no prior knowledge, in the browser; counts measured on the live page. Upload
went through `/api/upload` (the browser pane cannot attach files); everything else through the UI.

## What it takes today to reach a number

**With the AI step** (the intended path):

1. Upload, then wait for analysis (~1 min).
2. Wait for "Preparing your drawings" (~3 min).
3. The drawing check waits for someone to answer about 20 AI tasks in the operator panel. For each task:
   - copy the prompt;
   - download 1–3 images;
   - paste both into ChatGPT;
   - paste the JSON reply back.
   Tasks unlock in sequence (dimensions → wall styles → room names → walls → windows → kitchen).
4. Answer the roof question.
5. Wait for room measuring (~2 min).
6. Enter a name, calculate (~20 s), read the result.

**Without the AI step:** "Continue with what's ready" leads to a dead end that sends the contractor to the
engineer page to trace every room by hand.

## Findings, worst first

### Blocking — no heat load without outside help
1. **The AI step is invisible and manual.**
   - The contractor screen sits on "Checking your drawings (1 of 11 checks done)" indefinitely.
   - Nothing says the checks need someone to paste replies, or where to do it: the operator panel is only
     reachable by typing `?operator=1` into the address and reopening the job.
   - That panel is a long list of every task's prompt, images and reply box, not one task at a time.
2. **Without AI room outlines there is a dead end.** The message is an engineer instruction: "trace and
   calibrate each room, accept its area in the calculator draft, then rebuild model inputs". "Try again"
   cannot help, and the only way on is "Open engineer tools".
3. **The engineer page is unusable as a contractor path.**
   - Size: about 130,000 px tall (~169 screens), with 1,605 visible inputs, 92 buttons and ~37,000 words.
   - The room tracing tool is ~78,700 px down (~100 screens of scrolling).
   - Above it, "Geometry & Evidence Review" renders ~80 detected drawing items, each with a full review form
     ("Reviewer" ×165, "Engineer review source" ×84, "Review citation reference" ×84, "AI display name" ×80,
     "Confidence (0–1)" ×80, "Lock name" ×80…).
   - Tracing one room takes several steps (choose room, page, click corners, pick a printed dimension, save);
     the first room took ~19 min in the 2026-10-02 walkthrough.
4. ✅ **Fixed in this audit: the contractor view skipped ahead.**
   - The labels view lists only finished results. After the first refresh, unfinished checks vanished, so the
     view thought the drawing check was done, skipped room measuring, and showed "No rooms were found…".
   - Fix: the labels view now returns a progress summary over every check (total, waiting, blocked, change
     marker; no prompts).
   - A regression test fails on the old code and passes now.

### High — slow, confusing or wordy
5. **Too much waiting with little guidance.**
   - About 1 + 3 + 2 min of waits plus the AI step. There is no overall time estimate, and three different
     waiting screens (analysis, preparing drawings, checking drawings).
   - The analysis checklist does not tick as it progresses.
6. **Engineer language in contractor-facing text.**
   - Error and status messages: "calibrate", "calculator draft", "rebuild model inputs", "resolver",
     "coverage", "remediation", "assumption pack au-preliminary-v3".
   - "Trace rooms" appears as plain text inside the message, not as a button.
7. **The home screen does not say what to do.**
   - Marketing headline ("Engineering comfort. With less groundwork.").
   - A 3-step strip (Upload / Review inputs / Calculate) that does not match the job's 3 steps.
   - "Heating requires cited inputs and approval, and stays separate from cooling."
   - Internal codes in the project list ("ref overage_v7", "ref 1791263590"); "3 of 38 pages" unexplained.
   - "Current project" button with no clear purpose; "Your workspace / Drawings to decisions" footer.
8. **Nowhere to give the basics up front** (job name, site address, building type, what is above the roof).
   The contractor is asked later, or the app guesses.
9. **A name is required before calculating** ("Enter your name first").

### Medium
10. **Result wording:** "Generic Australian cooling design day (assumption pack au-preliminary-v3)"; a
    "Walls, roof and glazing 0.0 kW" line with no explanation (all walls face the mall or neighbours).
11. **Calculate takes ~20 s** with only "Calculating…".
12. **A wrong room area cannot be corrected on the room list**; the only route is tracing in engineer tools.
13. **"Start over"** sits top-right on every job screen; what it does to the job is unclear.
14. **From the earlier merge:** "Resolve model inputs" appears twice on the engineer page; "No AI evidence
    yet" shows even when AI results exist; the guided-step text column is very narrow.

## Recommended fixes, in order
1. **Operator step from the job.** A link on the waiting screen opens the operator panel for this job, and
   the panel becomes one task at a time (prompt + images + reply box, then the next task appears). This cuts
   the manual work until the hosted model.
2. **Replace the dead end with a focused "measure this room" screen inside the contractor view.** Only the
   tracing tool, full width, opened on the right plan page, guided: click corners, then the printed
   dimension, then save. Also allow typing a known area.
3. **Engineer page:** collapse "Geometry & Evidence Review" to a summary table with a per-item "Review"
   action; keep every section closed by default with a contents list at the top.
4. **A plain-language pass** on every contractor-visible message, the home screen and the result.
5. **A short job setup on upload** (name, address, building type, roof above?) feeding the questions early.
6. **Make the name optional** (remember it once); show a time estimate for the whole job; one waiting screen.
