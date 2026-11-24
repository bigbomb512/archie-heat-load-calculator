# Saved dwelling window placement replay (24 November 2026)

`python3 scripts/eval_window_placement.py` read every saved dwelling job with `house_windows.json` under `output/web_review`. It read the saved traces, page data and source PDF, then ran schedule parsing, tag finding and wall placement in memory. It made no writes to those jobs and ran no assembly or load calculation. The counts below are **candidate traced-wall placements**, before ceiling-height checks or trace-save validation; they are deliberately different from the previously saved `house_windows.json` counts.

| Job directory | Schedule | Baseline candidates | v14 candidates |
|---|---:|---:|---:|
| `2026.06.26-architectural-drawings-1793599162` | 28 | 7 | 0 |
| `2026.06.26-architectural-drawings-1795082136` | 28 | 12 | 8 |
| `2026.06.26-architectural-drawings-1795159765` | 28 | 11 | 6 |
| `2026.08.06-architectural-plans-1793621938` | 30 | 23 | 12 |
| `2026.08.06-architectural-plans-1794999739` | 30 | 7 | 0 |
| `22008---cc---rev-c8--139a-burns-road-turramurra_1_-1794567651` | 28 | 9 | 0 |
| `22008---cc---rev-c8--139a-burns-road-turramurra_1_-1794692047` | 28 | 14 | 7 |
| `22008---cc---rev-c8--139a-burns-road-turramurra_1_-1794981374` | 28 | 12 | 12 |
| `22008---cc---rev-c8--139a-burns-road-turramurra_1_-1795157091` | 28 | 9 | 9 |
| `22008---cc---rev-c8--139a-burns-road-turramurra_1_-1795251018` | 28 | 14 | 10 |
| `architectural-plan---132-old-south-head-road_-vaucluse---1h-20251124-1795317033` | 26 | 24 | 19 |
| `architectural-plan---132-old-south-head-road_-vaucluse---1h-20251124-1795345309` | 26 | 23 | 9 |
| **Total** | **336** | **165** | **92** |

The replay found 288 plan tags. Reason counts are per tag or unmatched schedule mark, so they do not sum to 336 windows. Before → after: no eligible wall within 3 m **97 → 171**; no matching schedule mark **9 → 5**; schedule mark without a matched traced wall **82 → 82**; same mark with different schedule sizes **3 → 3**; missing plan scale **13 → 13**; wall full **1 → 2**; plan/schedule level conflict **0 → 2**. The 74 extra wall failures mainly arise because v14 no longer treats an unclassified edge as exterior solely because no second room trace runs along it. No saved job was updated to reclassify those walls.

Evidence checked on the source PDFs: the June drawing PDF's plan page 6 prints `WG_01`, `WG_02`, and `WG_04` as separate tags. Its schedule page 11 lists `WG_01` with widths 1,025, 6,000 and 600 mm, all at height 2,700 mm, so that mark cannot select one size without more evidence. The saved scans also show `WG_02`/`WG_03` rejected by the trace writer for offsets just beyond the edge; retaining full offset precision addresses that case. Turramurra's saved v1 scan has no schedule rows, while the current parser reads 28 from its PDF. The older scan becomes stale under v14, but it was not rerun into the saved job.

Remaining limits: 82 schedule marks still lack a matched traced wall, 13 tags have no established plan scale, 3 tags have ambiguous sizes, and 2 have conflicting printed levels. Tags farther than 3 m from a known external or mall edge remain unplaced. A wider reach, a guessed size, or treating an unknown wall as exterior would be unsupported by these drawings. Ceiling-height checks and trace-save failures are outside this read-only replay; the next live update must inspect those results separately.


## Review note (merged version)

The stricter rule above (only walls already classified external or mall may take a window) was not merged: a house's
walls are not classified by the AI, so on houses every outside wall is "unknown" and the rule placed fewer windows
(165 to 92; Turramurra 9 to 0). The merged code keeps the house rule (an unknown edge not along another traced room is
outside; internal, adjacent and wrong-level edges never take a window) and the loose level fallback, with this branch's
mark normalisation, full offset precision, placement evidence and the old-version re-scan. Replay after the merge:
166 of 336 scheduled windows placed (165 before). The remaining failures are mostly tags with no traced room wall
within 3 m and schedule windows whose room isn't traced: improving room tracing is what would raise this.
