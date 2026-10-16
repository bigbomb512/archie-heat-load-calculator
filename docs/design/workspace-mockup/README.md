# Approved workspace mockup (2026-10-16)

The look the job workspace is rebuilt to, approved by the user. Three screens, with Butcher Buffet's real data:

| File | Screen |
|---|---|
| `1-drawings.dc.html` | Step 1, Drawings: the AI part-way through a job |
| `2-rooms-phase1.dc.html` | Step 2, Rooms (Phase 1): floor plan, rooms table, the Shop open |
| `4-results.dc.html` | Step 4, Results after Phase 2 |

These are reference markup from the design canvas (`.dc.html`: the page is between `<x-dc>` and `</x-dc>`; every
style is inline). They do not run on their own. Copy the layout, sizes, colours and wording; build the real screens
in the app's own code.

Asset addresses in the files point at the canvas; use the repo copies instead:

| In the mockup | In the repo |
|---|---|
| `/_blob/0440e6c0af413802d606d9044695c388` | `frontend/assets/fonts/AlteHaasGroteskRegular.ttf` |
| `/_blob/844ee5da0326ac58c0c9dacaa1f3f1fa` | `frontend/assets/fonts/AlteHaasGroteskBold.ttf` |
| `/_blob/f58fcda2fc0de1fc135f161c51b2b190` | `frontend/assets/images/toki-wordmark-exact.png` |

Data in the mockup: the room outlines and Phase 1 figures come from the 15 Oct run; the 33.8 kW result and its
per-room breakdown from the 7 Oct walkthrough run. The "Still assumed" lines on Results, the Shop's height note and
"about 15 minutes to go" are example wording, not real data: the app shows its own.
