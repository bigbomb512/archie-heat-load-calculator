# Handoff: Turramurra room retrace (26 November 2026)

Read this before continuing. Also see docs/TURRAMURRA_ROOM_TRACING_REVIEW_2026-11-25.md (Codex's review).

## Decisions the user has made
- **Room review:** paid AI room-outline step, Turramurra first (`output/web_review/22008---cc---rev-c8--139a-burns-road-turramurra_1_-1794567651`), then decide the other 11 houses.
- **Heating:** stays "not calculated". Don't restore it.
- **Casa Nova:** the 70–85 kW figure was the user's own rough estimate and isn't a reference. Compare Toki's 33.95 kW with the hand calculation's 37.3 kW.
- **Saved jobs:** nothing is applied to a saved job or recalculated until the user approves the outlines and counts.
- **Worktrees:** 19 clean, merged worktrees were removed (branches kept). Kept: `engineer-reports` (holds two job directories) and seven with unmerged work: `claude/silly-lamarr-dcdcd4` (41 uncommitted files), `set17-fixes` (13 uncommitted), `perf/round2-integration` (5 commits), and `perf/input-resolution`, `fix/house-room-checks`, `perf/supervisor-rebuild-2` and `set17-continue` (1 commit each).

## AI provider
OpenRouter (`OPENROUTER_API_KEY`), model `anthropic/claude-haiku-5.5` for drawing steps. The key limit is $21 (raised by the user on 26 Nov), and about $2.80 is left. Each call costs about $0.001, and a whole Turramurra retrace costs $0.03–0.08. Check what's left with `curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $OPENROUTER_API_KEY"`.

## Branch `fix/room-retrace-review` (from `main` 8a33eaa, not yet committed)
- `scripts/retrace_job_copy.py`: copies one job outside `output/web_review` (APFS clone), refreshes room names, and reruns the P0 outline steps, then window placement, on the copy. It defaults to a dry run; `--live --max-calls N` makes AI calls. It fails if the source job changes.
  Example: `python3 -B scripts/retrace_job_copy.py --live --max-calls 60 --job output/web_review/<job> --out /some/new/dir`
- `scripts/room_trace_review.py`: draws outlines over the plan images and flags problems: printed names with no outline, outlines holding two names, overlaps, areas under 2 m² or over 80 m², and outlines outside the floor. It also counts windows. `--baseline` compares a copy against its original.
- `ai/room_outline.py` `validate_outline_reply`: each room is now checked on its own. A bad room goes to `applied_value.not_drawn`, and an unlisted name goes to `applied_value.extra_rooms`. The whole reply is rejected only if no room is usable. Unchanged: use names, names printed on the page, and the second ask when rooms are left out.
- `backend/autonomous_tasks_service.py`: a partial outline answer now replaces only the rooms it drew. Before, it wiped every asked room, so page 9 never settled.
- `scripts/eval_room_tracing.py`: the "implausible area" flag now uses `OPEN_FLOOR_MAX_M2` (80 m²) instead of 120 m².
- Tests: `tests/test_retrace_job_copy.py`, `tests/test_room_trace_review.py`, `tests/test_room_outline_saved_replies.py` (+ `tests/fixtures/turramurra_outline_replies.json`). The full suite passes: `ARCHIE_ASSUMPTION_RANGE=off python3 -B -m unittest discover -s tests -q`, 1,662 tests.

## Results so far (kept in `tmp/room_retrace_2026-11-26/`, about 39 MB; don't commit)
| | Saved job | Run 4 (best) | Run 5 |
|---|---|---|---|
| Ground-floor outlines | 8, diagonal and crossing walls | 15, mostly following walls | 9, worse |
| Ground-floor names with no outline | 10 | 4 (Garage, Alfresco, Linen, Entry) | 9 |
| First-floor outlines | 4 | 15 | 13 |
| Windows placed on walls (of 28) | 0 | 0 | 6 (+6 assigned without a wall) |

- `run4/`, `run5/`: overlay PNGs and `retrace_report.json`. `baseline_all_houses/`: the overlays and `summary.md` for all 12 saved houses.
- There's no Garage on either floor plan; the AI correctly reports that. Run 4's Dining outline (51.9 m²) overlaps its neighbour and needs splitting.
- The full job copies were in the session's scratchpad, which may be gone. Rerun the script to recreate them.

## Update (later on 26 Nov, second account)
- **Windows on walls (fixed):** the retrace script now does the app's room-use refresh before window placement (`_refresh_room_use`: export, room-use resolve, evidence build). Without it, new outlines had no room-use record and were skipped as not cooled.
- **App fix in `backend/room_proposal.py`:** an outline whose saved numbering is out of date ("Living" after "Living (1)/(2)/(3)") was dropped from the room list, so it never got a cooled record. Current outlines now replace old numbered entries that no longer have an outline, and each outline is added under its own `room_id`.
- **App fix in `ai/room_use_resolution.py`:** "Sitting" now counts as a house living room. Before, it was a low-confidence generic room counted as not cooled.
- **Retrace script:** the asked-name list is fixed after a page's first answer (it used to flip forever), and letter-spaced labels ("R O B E") are merged. All three new runs finished on their own.
- **Three new runs** (a, b, c; 41–48 calls each; about $0.14 together; key has about $2.67 left): ground floor 14–15 outlines with 4 names unmatched, first floor 18–23 outlines, 12–14 windows placed on walls (saved job: 0). Run c is the best and is saved in `tmp/room_retrace_2026-11-26/run_c/`.
- **Visual review of run c (not approved):** most rooms are in the right place, but some are wrong:
  - Wrong names: the ground-floor Sitting room is labelled "Living (1)", Media is "Family", and Bedroom 5 is "Bedroom".
  - Wrong shapes: Dining (37.8 m²) and Kitchen are skewed shapes that don't follow the walls. "Alfresco" is a 1.3 m² sliver. WIR and Linen have no outline.
  - First floor: "Balcony shown" (59 m²) covers roof and outdoor space, "Balcony" (13.8 m²) sticks out past the building, and Linen overlaps its neighbour.
- Full suite: 1,663 tests, OK.

## Next steps
1. (Done, see Update.) Find out why run 4 places no windows on walls. 16 windows fail with "no outside wall within 3000 mm of the tag". Suspected cause: `retrace_job_copy.py` patches out `_p3_packets` and `_p3_surroundings_packets`, the steps that mark walls as outside or shared, and the tenancy outline may differ between runs. Check with no AI calls first.
2. Results vary a lot between runs. Run Turramurra about three times and keep each room's outline that best follows the walls, or make the corner-snap step more consistent.
3. Show the user the overlays and counts for every house, including any that get worse, and get approval before changing a saved job or recalculating. Turramurra's saved 9.81 kW is probably too low.
4. Then decide about the other 11 houses (the review flags 156 printed names with no outline across all 12).
5. Ask the user whether to commit or open a PR for this branch. They haven't said yet.
