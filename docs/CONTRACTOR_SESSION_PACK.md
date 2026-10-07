# Contractor workflow session — pack (updated 2026-10-07 for the job workspace)

Purpose: find out whether a real HVAC contractor or estimator can get from an
architect's PDF to a labelled **draft** cooling load in Archie **without help**,
how long it takes, and where they get stuck. This is a **workflow test**, not a
test of the number (see "What the result will and won't include").

Drawing set: **Butcher Buffet** (`20260226 Butcher Buffet @ Melrose Park
CONSTRUCTION REV B(2).pdf`), a permitted evaluation case with a known
reference. Do **not** tell the participant the reference or the acceptable
range, before or during the session.

The screens changed on 2026-10-06/07 (commits `b2c50c6`, `8ad9e0a`, `e5516c7`,
`20d98f4`). A job now opens in a **workspace with a left rail of tabs**:
Project, Drawings, Rooms, (Walls & roof and Windows once Card W lands),
Results. Calculate and the total are always in the rail. Engineer review is one
click away but is **not** part of this test. Sections 8–10 describe the old
screens and are kept as history.

---

## 1. Before the session

### What the participant will do

1. **Main task (about 30–45 min):** open the prepared Butcher Buffet job and
   get a draft cooling load. The tabs ask them to:
   - **Project:** check the address and say what's above the tenancy;
   - **Rooms:** check the room list and areas, untick rooms that aren't cooled,
     and measure or type any missing area;
   - **Calculate**, then read **Results**.
2. **Short upload task (about 5 min, at the end):** upload the same PDF as a
   new job and say what they think is happening while the pages are prepared.
   Stop once the Drawings tab shows the preparation progress; don't wait for
   it to finish.

Why the job is prepared in advance: the AI step (reading rooms, walls,
windows and north from the drawings) is still done by the Toki team pasting
into ChatGPT. In the final product a hosted model does it automatically. If
the participant waited for a person to paste replies, the session would
measure the facilitator, not the tool.

### What the result will and won't include

- **Walls, roof and glazing.** Unless Card W (Walls & roof, Windows tabs) has
  landed:
  - rooms with only an area have no outline, so their walls and roof are "not
    assessed";
  - measured rooms show "Walls (not classified yet)" and "Roof (not checked
    yet)".

  Results then shows "Walls, roof and glazing 0.0 kW" and lists them under
  "Not included yet". This is expected; don't treat it as a participant error.
- **Never included in this version:**
  - air leakage (infiltration);
  - kitchen exhaust and make-up air;
  - moisture from cooking;
  - air-system design;
  - the coolroom and freezer (refrigeration, sized separately).

  The weather is a generic Australian design day, not the site's (AIRAH DA09
  comes later).
- **For your reference only** (do not tell the participant): engineer's
  whole-building figure about **39.6 kW**, acceptable range **35–50 kW**.
  Recent drafts on the walkthrough copy:
  - 34.4–34.8 kW with typed or measured areas;
  - Kitchen measured on the plan at 104.4 m² (answer key 104.9 m²).

  None of these is a validated number.

### Known rough edges (not session-blocking; note them if they come up)

| # | What | What the participant sees |
|---|---|---|
| W1 | **Calculate takes about 1.5–2 minutes** (the first time on a job up to about 3 minutes) | "Preparing the calculation: room uses, heights, people and equipment…" under the button |
| W2 | **Saving a measured room takes 11–16 s**; adding a room about 15 s | "Saving… (about 15 s)" |
| W3 | Typed and measured areas count straight away, but the total only changes after Calculate | The rail says "Out of date — calculate again" |
| W4 | Measuring: room names are often on a different page from the dimensions | "Kitchen's name is printed on page 21" above the plan |
| W5 | Preparing pages takes 4–7 min on this 38-page set (upload task only) | Progress with the current step; "You can leave this page" |
| W6 | Phones: the plan is small and has no pinch-to-zoom | Use a laptop or desktop for the session |

## 2. Timings measured so far (Claude, real UI, Butcher Buffet copy)

| Stage | Time | Notes |
|---|---|---|
| Upload + analysis (38 pages) | ~50 s | earlier dry runs |
| Prepare pages (server job) | 238–391 s | `20d98f4`; reload-safe |
| AI step, per check (Toki team) | not yet measured with real ChatGPT replies | the Drawings tab records it per check ("about N min each") |
| Open a job → rail and tab | < 2 s | |
| Type a room area | instant | saved per field |
| Measure a room (scripted clicks) | 52–67 s + 11–16 s save | **not a human time**: clicks were computed from a known outline. The session measures the real first-room time (target: under 5 min) |
| Calculate | 77–120 s | |

Expected session length: **35–50 min** for the main task, plus 5 min upload
task, plus 10 min of questions. Stop at 60 min of task time regardless.

## 3. Setting up (for you)

1. **Code.** Use commit `20d98f4` or later; also include Card W if it has been
   merged. Start the app from the repo folder:
   ```bash
   ./start_web --port 8000
   ```
2. **Prepare the job (the day before; about 30–40 min of your time):**
   1. Open `http://localhost:8000/?operator=1` and upload the Butcher Buffet PDF.
   2. Let the Drawings tab prepare the pages (4–7 min; you can leave the page
      open).
   3. Answer every check in the **AI step** panel (copy the prompt, attach the
      images, paste the reply, Check and apply) until it says "No checks are
      waiting for a reply". Use real ChatGPT replies, not stand-ins.
   4. Note the panel's time line ("N min spent on N checks · about N min
      each"); this is the first real per-check timing.
   5. Don't fill the Project tab, don't type areas, and don't press Calculate:
      those are the participant's job.
3. **The participant's browser.** Use a **separate browser profile** (or a
   private window) that has never opened the app with `?operator=1`. The
   workspace remembers the AI-step switch and your name per browser, and the
   participant must see neither. Open `http://localhost:8000` (no `?operator=1`)
   at the projects list, full screen.
4. Put the Butcher Buffet PDF on the desktop for the upload task.
5. If the participant agrees, start a screen recording (with audio) before
   reading the task.
6. Have the observation sheet (section 5) open, or print it.

## 4. Script (read this out, then stay quiet)

> "Thanks for helping. This is an early tool that estimates cooling loads from
> architect drawings. We're testing the tool, not you. If something is
> confusing, that's exactly what we want to find.
>
> I've already uploaded the drawings for a restaurant tenancy, **Butcher
> Buffet**. The tool has read them. Your task: **get a draft cooling load for
> this tenancy using the tool.** Please think out loud as you go: say what
> you're looking for and what you expect to happen.
>
> I can't help or answer questions about the tool while you work, because we
> want to see where it's unclear. If you'd normally give up or call someone,
> just say so and we'll stop there."

At the end of the main task, read:

> "One more short thing: the drawings are on the desktop. Please start a new
> job with them, as if this were a new tenancy, and tell me what you think is
> happening. You don't need to wait for it to finish."

Facilitator rules:

- Don't point, hint or explain. If asked, reply "What would you try?" or "What
  do you think it wants you to do?"
- If they are completely stuck for **3 minutes**, note it, then give the
  smallest possible hint and record exactly what you said.
- Don't name tabs or features (Measure on the plan, Calculate, Engineer review)
  before they find them.
- If they open **Engineer review**, note it and let them continue. If they're
  still there after 2 minutes, say: "Please stay in the main job screen for
  this test."
- Don't mention the reference load or the acceptable range.

## 5. Observation sheet

Record the clock time when each stage starts and ends, and every hesitation,
question, wrong click or error message (with the exact wording).

| Stage | Start | End | Hesitations / questions / errors (exact words) |
|---|---|---|---|
| Find and open the job | | | |
| First look at the job screen: did they understand the rail and the tab states (✓ / ● / ! / ◌)? | | | |
| Project tab: address ("Use this"?), building type, what's above | | | |
| Rooms: did they check the areas? Did they notice which were "Measured by AI" and which were "Printed on the drawings"? | | | |
| Rooms: unticking rooms that aren't cooled (coolroom, freezer?) | | | |
| Rooms: typing an area or ceiling height (which rooms, why) | | | |
| Measure on the plan, room 1: which room, which page, which dimension? Time to the first corner; time to save | | | |
| Measure on the plan, room 2 (did they reuse the scale?) | | | |
| Walls & roof / Windows (only if Card W has landed) | | | |
| Calculate: did the 1.5–2 min wait worry them? | | | |
| Results: did they read "Not included yet" and the draft warning? Print or CSV? | | | |
| Upload task: what did they think the page preparation was doing? | | | |

Also note:

- Hints given (exact words and time):
- Point where they would have given up, if any:
- Final number shown, and the per-room numbers:
- Rooms included; areas typed vs measured vs AI:
- Did they open Engineer review? When and why?

## 6. After the session — questions

Ask these in order, after the participant has seen the result:

1. "Before we look at it together, what cooling load would you have expected
   for this tenancy, roughly?"
2. "What do you think this number includes, and what does it leave out?"
3. "Would you send this number to a client or use it to select equipment? Why
   or why not?"
4. "What would you need to see to trust it?"
5. "When the tool showed room areas it had worked out, did you trust them?
   What made you check (or not check) them?"
6. "Which step was the most frustrating? Which was easiest?"
7. "How long would this job normally take you, and how?"
8. "Anything you expected the tool to do that it didn't?"

## 7. What to send back to Claude

- The completed observation sheet (photo or typed).
- The screen recording, if made (or your notes of key moments with times).
- The job name from the projects list, so the saved job can be inspected.
- The AI-step time line from the job prep (section 3, step 2.4).

Claude turns these into a friction log in the same format as
`docs/WALKTHROUGH_2026-10-02_butcher_buffet.md`, ranks the fixes and writes the
next cards.

---

# History: dry runs on the old screens (before the job workspace)

## 8. Dry-run project (for reference)

- Isolated copy, commit `347b031`, project
  `dryrun-butcher-buffet-_not-for-design_-1791038318` (in the scratchpad
  worktree, not in your `output/`).
- Result: 4.08 kW AI preliminary estimate from **Bar only** (30.78 m², reviewer
  trace) — illustrates D5. Bar was traced twice to demonstrate D3: on
  `347b031` the trace on the building-evidence entry (`spaces-21-2`) was saved
  and calibrated but not counted. With the D3 fix (`dcca237`) both Bar traces
  map to Bar and count; the same project, re-resolved, confirmed and
  calculated in the real UI, again gave 4.08 kW from Bar only, with Kitchen and
  Shop listed as not traced.
- Next: a fresh dry run on `dcca237` or later with Bar, Kitchen and Shop all
  traced (pending).

## 9. AI-only dry run (Claude, 2026-10-06, commit `a6f0fc3`)

Fresh upload of Butcher Buffet in an isolated copy of `a6f0fc3` (own server
and `output/`). No room was traced. Every AI reply was written by Claude from
the task images and pasted through the operator panel with the stand-in box
ticked; these are **stand-ins, not model replies**, so they say nothing about
model accuracy. The roof question was answered in the contractor view
("Floor/tenancy above", as confirmed by the project contact).

### Timings

| Stage | Time | Notes |
|---|---|---|
| Upload + analysis (38 pages) | ~50 s | upload via `/api/upload` (the browser pane cannot attach files) |
| Confirm selected drawings | ~2 min 40 s | similar to before |
| Operator: Run all tasks | 26 s | builds dimensions, site, north, kitchen (blocked until rooms exist) |
| Operator: each reply validated | 0.5–28 s | room naming 28 s (writes the AI outlines); most 8–15 s |
| Contractor: roof answer | ~15 s per room | three rooms |
| Guided "Resolve model inputs" | ~100 s | |
| Confirm room list | 12 s | |
| Calculate draft load | 12 s | |

Operator time (reading images, pasting replies) was ~12 min for 14 tasks;
with a hosted model this step disappears.

### Result

**33.76 kW** AI preliminary estimate (Bar 32.55 m², Kitchen 103.82 m², Shop
215.1 m², all "AI-determined · below accuracy bar"), with no tracing. For
comparison: AI-only 33.79 kW on 2026-10-05; manually traced 35.20 kW; the
engineer's reference is ~39.6 kW (do not tell the participant). Kitchen
equipment heat, infiltration, kitchen exhaust and site design weather are
still excluded (they wait for AIRAH DA09), which explains most of the gap.

Stand-in answers against the answer key (caseA): room areas 5/5 within 5 %
(Kitchen +4.9 %, the closest); north 0° on p. 20/21; site text correct;
kitchen equipment 5/5 keyed items. Again: stand-ins, not accuracy evidence.

### Problems found

| # | Problem | Effect |
|---|---|---|
| C1 (high) | The wall-boundary task for the main Shop part is **blocked** ("prompt exceeds 1,500 characters"). The AI outline has 44 edges, including thin slivers around free-standing partitions inside the dining area, giving 30 wall runs ≥ 1 m (9 with the reviewer's trace). | Shop walls stay unclassified; the result lists "Walls — boundary not classified — Shop" and "Envelope — Shop". |
| C2 (high) | The shopfront is split across the two AI parts of Shop (9.3 m + 2.5 m). The window task was built for the 2.52 m part only, so a faithful reply (11,825 mm elevation) can never match it within 2 %. | Shopfront glazing cannot be applied. |
| C3 (high) | The roof question is only built by "Run all tasks"; it did not appear until the operator pressed it a second time after the rooms existed. The contractor's answer was then applied to the first Shop part only. | The result lists "Roof — not checked — Shop". |
| C4 | Boundary exclusions in the model from AI wall answers are labelled "Answered by the contractor". | Wrong provenance shown. |
| C5 | Stand-in replies are labelled "AI-determined" in the panel; the panel status says "Reply validated" even when the card shows a validation error. | Operator confusion. |
| C6 | Rooms with no shopfront (Bar, Kitchen) get a red "blocked" window task. | Noise in the operator list. |
| C7 | After reopening the project, the next-action button is "Confirm selected drawings" again although they were confirmed. | A participant may re-run a 2½-minute step. |
| C8 | The kitchen task's blocked message says "trace and calibrate the kitchen first" before the AI outlines exist. | Misleading in the AI-only flow. |
| C9 ✅ | The kitchen check refused "3 DOOR" (a fridge label split over two lines) as "gives no heat". | Fixed 2026-10-06 in `ai/kitchen_equipment.py`, with a test. |

Recommended fix for C1–C2: ask the wall-boundary question once per tenancy
perimeter (the outer boundary of all room outlines on the plan) instead of
once per room part; edges not on the perimeter are internal automatically,
and the shopfront is matched against the perimeter run.

Dry-run project: `dryrun2-butcher-buffet-_not-for-design_-1791243530` in the
scratchpad copy (not in your `output/`).

## 10. AI-only dry run 3 (Claude, 2026-10-06, commit `13a05b1`)

Fresh upload in an isolated copy of `13a05b1`. No room was traced. Operator
replies were written by Claude from the task images and sent through the same
API the operator panel uses (`/api/autonomous-tasks`), all marked stand-in;
the contractor steps (roof question, resolve, confirm, calculate) ran in the
browser.

| Stage | Time |
|---|---|
| Upload + analysis | 58 s |
| Confirm selected drawings | 2 min 44 s |
| Run all tasks | 43 s |
| Dimensions, wall styles, site, north, kitchen (each) | 8–14 s |
| Room naming (writes the AI outlines) | 30 s |
| Wall boundaries (one task for the page, 13 runs) | 79 s |
| Shopfront window | 46 s |
| Roof answer (contractor) | 15–29 s per room |
| Guided "Resolve model inputs" | ~100 s |
| Confirm room list / calculate | ~13 s / 13 s |

**Result: 34.14 kW** (Bar 32.55, Kitchen 104.10, Shop 217.33 m², all
"AI-determined · below accuracy bar"). Walls and roof are now classified, so
the result no longer lists "Walls — boundary not classified" or "Roof — not
checked". The envelope still adds 0 kW: every wall faces the mall, another
tenancy or another room, and the roof is not exposed (user decision 2026-10-04).

Scored against caseA (stand-ins, so this measures the pipeline, not a model):
rooms 5/5, site 1/1, walls 2/2, shopfront window 1/1, roof 3/3, kitchen 5/5,
north 2/6.

Checked fixed: C1–C2 (one wall task per perimeter with a numbered image; the
11.44 m shopfront is one run across both Shop parts), C3 (roof questions appear
as soon as rooms exist; one answer covers both Shop parts), C5, C6, C7
("Drawings confirmed" after reopening), C8, C9 (the quote "3 DOOR" accepted),
and the shopfront window (2,025 mm, sill 1,100, head capped at 2,700) applied
for the first time.

| # | Problem | Effect |
|---|---|---|
| D3-1 (high) | North is asked on p. 22–25, but `reviewer_room_geometry_service` only accepts north for its own plan-page list, which excludes p. 25 ("Choose a supported plan page"). The cross-page agreement waits for every page, so p. 22–24 never apply either. | North applies on p. 20–21 only (2/6 keyed pages). |
| D3-2 (medium) | Roof results answered by the contractor are exported and scored like AI results. | Would inflate P5 accuracy in the recorded check. |
| D3-3 (low) | The shopfront window faces the enclosed mall, so it adds no load, but the result says "No resolved reviewed/AI opening geometry…" instead. | Misleading reason. |
| D3-4 (low) | The wall task took 79 s to validate and apply, the slowest step. | Operator wait. |

Dry-run project: `dryrun3-butcher-buffet-_not-for-design_-1791252951` in the
scratchpad copy.
