# Contractor workflow session — pack (prepared 2026-10-04)

Purpose: find out whether a real HVAC contractor or estimator can go from an
architect's PDF to a labelled **draft** cooling load in Archie **without help**,
how long it takes, and where they get stuck. This is a **workflow test**, not a
test of the number: the draft still leaves out the envelope and air-side loads.

Drawing set: **Butcher Buffet** (`20260226 Butcher Buffet @ Melrose Park
CONSTRUCTION REV B(2).pdf`) — a permitted evaluation case with a known
reference (do **not** tell the participant the reference or the acceptable
range before or during the session).

---

## 1. Before the session — must be fixed first

A dry run on commit `347b031` (2026-10-04, isolated copy) found two problems
that would spoil the session. Fix both, re-run the dry run, then schedule.

| # | Problem found in the dry run | Why it matters for the session | Fix |
|---|---|---|---|
| D3 | **Every room appears twice in the trace room picker** (e.g. "Bar · area unresolved" and "Bar · Unassigned level · area unresolved"). A trace saved on the first entry is calibrated and saved but **never counted**: the area gate and the draft still say the room has no area. | The participant will very likely pick the first entry, trace carefully, and be told to trace again. | Dedupe the trace room list by room identity (label + level, blank level = "Unassigned level"), keeping the room-use entry; key `current_traced_areas` by room identity so any existing trace on the old ID still counts. Files: `backend/reviewer_room_geometry_service.py` (Codex is editing this file for Card I — do it after Card I or inside it). Cause: Card M now emits building-evidence spaces with a blank level. |
| D5 ✅ fixed 2026-10-04 (Claude, uncommitted at time of writing) | **The room confirmation list only showed rooms that already had an area.** Now untraced comfort rooms are listed as "Not traced — not included in the total" with a "Trace this room" button, cannot be included, and a line above the list names them. |
| — | *(original D5 notes below)* | | |
| D5 (original) | **The room confirmation list only shows rooms that already have an area.** Untraced rooms (Kitchen, Shop) are not listed at all. | The participant can trace one room, confirm, and get a small number (dry run: 4.1 kW from Bar alone) without noticing two rooms are missing. | List comfort rooms without an area in the confirmation block as "not traced — not included in the total" with a link to trace them; keep them out of the calculation. Files: `ai/room_scope_confirmation.py`, `frontend/js/app.js`. |

Worth fixing if time allows (not session-blocking):

- **D1** Before confirmation, the summary says "Review required before AI"
  and the next action is "Create ChatGPT packet". The manual ChatGPT route stays
  for now (user decision 2026-10-04: keep ChatGPT on the user's computer for
  testing, convenience and cost; a hosted model comes later), so this wording is
  acceptable — but in the session the participant should not be sent into
  ChatGPT (see section 4).
- **D4** After saving a trace, the room picker still says "area unresolved"
  (walkthrough finding #15).
- **D7** Opening a project takes ~45 s before the room confirmation block
  appears: `GET /api/ai-preliminary-model` returns ~29 MB (the whole draft
  model and input set) for Butcher Buffet. The participant may scroll past
  before it renders. Fix: return only what the UI draws (status, report, room
  scope) and fetch the rest on demand. File: `backend/ai_preliminary_service.py`
  (Codex is editing it for Card I — do after).
- **D6** When the guided resolver stops because rooms need tracing, the
  instruction ("For Bar, Kitchen, Shop: trace and calibrate each room…") only
  appears in a toast; the status line stays generic.

## 2. Dry-run timings (Claude, 2026-10-04)

| Stage | Time | Notes |
|---|---|---|
| Upload + analysis (38 pages) | 45 s | |
| Confirm selected drawings | 136 s | Long wait — watch whether the participant thinks it has stalled |
| Guided "Resolve model inputs" (no traces yet) | 60 s | Stops with "trace and calibrate each room" (toast only) |
| Trace + calibrate one room | not representative | Claude placed corners by keyboard; budget **4–8 min per room** for a first-time user with a mouse (walkthrough: first room took ~19 min) |
| Guided resolve after tracing | 90 s | |
| Confirm rooms | < 10 s | |
| Calculate draft load | ~25 s | |

Expected session length: **35–50 min** for three comfort rooms (Bar, Kitchen,
Shop) plus reading the result. Stop at 60 min regardless.

## 3. Setting up (for you)

1. Use a machine with this repo on the commit you want to test (after the D3
   and D5 fixes). Start the app from the repo folder:
   ```bash
   ./start_web --port 8000
   ```
2. Open `http://localhost:8000` in a normal browser window, full screen, with
   no other projects open in the sidebar if possible.
3. Put the Butcher Buffet PDF on the desktop. The participant uploads it
   themselves — uploading is part of the test.
4. If the participant agrees, start a screen recording (with audio) before
   reading the task.
5. Have this page's observation sheet (section 5) open to take notes, or print
   it.

## 4. Script (read this out, then stay quiet)

> "Thanks for helping. This is an early tool that estimates cooling loads from
> architect drawings. We're testing the tool, not you — if something is
> confusing, that's exactly what we want to find.
>
> Here's the task: **get a draft cooling load for this tenancy using the tool.
> The drawings are on the desktop.** Please think out loud as you go — say what
> you're looking for and what you expect to happen.
>
> I won't be able to help or answer questions about the tool while you work,
> because we want to see where it's unclear. If you'd normally give up or call
> someone, just say so and we'll stop there."

AI step in this session: **skip it.** The manual ChatGPT copy/paste is an
interim development route that won't exist in the final product (which will
call a hosted model automatically), so the participant should not do it. If
they open the ChatGPT packet or ask about it, say: "That step is done
separately in this test — please carry on without it." Run the ChatGPT step
yourself afterwards on the same project if you want to compare.

Facilitator rules:

- Don't point, hint or explain. If asked, reply: "What would you try?" or "What
  do you think it wants you to do?"
- If they are completely stuck for **3 minutes**, note it, then give the
  smallest possible hint and record exactly what you said.
- Don't mention ChatGPT, tracing, room confirmation or any feature name before
  they find it.
- Don't mention the reference load or the acceptable range.

## 5. Observation sheet

Record the clock time when each stage starts and ends, and every hesitation,
question, wrong click or error message (with the exact wording).

| Stage | Start | End | Hesitations / questions / errors (exact words) |
|---|---|---|---|
| Upload the PDF | | | |
| Analysis → first screen (did they understand what to do next?) | | | |
| Confirm drawings (did the long wait worry them?) | | | |
| Find where to start (guided action vs. other buttons) | | | |
| Find the room tracing tool | | | |
| Trace + calibrate room 1 (which room? which plan page? which dimension?) | | | |
| Trace + calibrate room 2 | | | |
| Trace + calibrate room 3 | | | |
| Resolve model inputs | | | |
| Confirm the room list (did they notice missing rooms?) | | | |
| Calculate draft load | | | |
| Read the result (did they read what is excluded?) | | | |

Also note:

- Hints given (exact words and time): 
- Point where they would have given up, if any: 
- Final number shown: 
- Rooms included in the total: 

## 6. After the session — questions

Ask these in order, after the participant has seen the result:

1. "Before we look at it together — what cooling load would you have expected
   for this tenancy, roughly?"
2. "What do you think this number includes, and what does it leave out?"
3. "Would you send this number to a client or use it to select equipment? Why
   or why not?"
4. "What would you need to see to trust it?"
5. "Which step was the most frustrating? Which was easiest?"
6. "How long would this job normally take you, and how?"
7. "Anything you expected the tool to do that it didn't?"

## 7. What to send back to Claude

- The completed observation sheet (photo or typed).
- The screen recording, if made (or your notes of key moments with times).
- The project name or ID from the sidebar, so the saved project can be
  inspected.

Claude turns these into a friction log in the same format as
`docs/WALKTHROUGH_2026-10-02_butcher_buffet.md`, ranks the fixes and writes the
next cards.

## 8. Dry-run project (for reference)

- Isolated copy, commit `347b031`, project
  `dryrun-butcher-buffet-_not-for-design_-1791038318` (in the scratchpad
  worktree, not in your `output/`).
- Result: 4.08 kW AI preliminary estimate from **Bar only** (30.78 m², reviewer
  trace) — illustrates D5. Bar was traced twice to demonstrate D3: the trace on
  the building-evidence entry (`spaces-21-2`) is saved and calibrated but not
  counted; the trace on the room-use entry is counted.
