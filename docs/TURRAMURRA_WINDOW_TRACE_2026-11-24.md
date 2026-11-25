# Turramurra saved window trace (24 November 2026)

Scope: read-only inspection of `output/web_review` and PDF text. No saved job was modified, no input set was reassembled, and no load calculation was run for a saved job. Counts below describe saved artifacts, not a newly calculated result.

## Where the openings were lost

| Stage | Turramurra `…1794567651` | Vaucluse `…1795317033` | Interpretation |
|---|---:|---:|---|
| `building_evidence.json` `openings` | 61 | 91 | These are source-linked candidates; Turramurra's sample W17/W20 records have `dimensions: null` and `performance: null`. They are not placed glazing. |
| `page_inventory.json` | Page 17 is `Window and door schedule` | Page 20 is `Window & Door Schedule` | Both drawing sets identify schedule pages. |
| `house_windows.json` `version` / `windows_in_schedule` / `placed` | `house-windows-text-v1` / 0 / 0 | `house-windows-text-v13` / 26 / 19 | Turramurra's saved v1 scan says `No window schedule could be read as text; windows need the drawing step.` The current parser reads **28** dimensioned windows from its PDF page 17 (read-only check). |
| `ai_tasks/P4_openings` and `P4_storefront_wall` | No records | No records | The storefront elevation tasks did not supply house openings in either job. |
| `reviewer_room_geometry.json` `records[*].openings` | 0 across 17 traces | 19 across 37 traces | The schedule/tag placement did not enter Turramurra's room traces. |
| `ai_preliminary_input_set.json` `proposal.openings` | 0 | 18 | Assembly builds proposals from trace edge openings (`backend/ai_preliminary_service.py`, `_prepare_preliminary_proposal`). One Vaucluse trace opening is not in the saved proposal; no opening was present to propose for Turramurra. |
| `ai_preliminary_input_set.json` `surface_summary.openings_discovered` / `openings_included` | 0 / 0 | 18 / 18 | The missing input persists through assembly. |
| `ai_preliminary_input_set.json` `material.hourly_load_model.rooms[*].cooling_load.glazing_surfaces` | 0 across 12 rooms | 18 across 16 rooms | The saved model reflects the proposal; this is not a downstream load calculation issue. |

The current path is `house_windows_service.apply` → `ai.house_windows.read_schedules` and plan-tag placement → room trace `openings` → `_prepare_preliminary_proposal` opening loop → `_with_certificate_glazing` → `ai_preliminary.assemble`. `P8_glazing_certificate` only supplies U-value/SHGC to *existing* proposal openings; neither compared job has a saved P8 task. The old Turramurra `house_windows.json` is the first zero in the placement path. Later saves of the same Turramurra CC set record 28 schedule windows and 4–12 trace openings, so the current parser can read this set. No current-code defect was established from the affected saved job.

**Operator recovery:** In a writable copy or the live job, rerun the drawing-task/window placement step with the current code, inspect `house_windows.json` for 28 schedule entries and its `placed`/`unplaced` reasons, review windows that could not be matched to traced exterior walls, then reassemble the preliminary input set and rerun the normal job workflow. The old saved result must be treated as incomplete until that is done. A certificate or schedule value alone does not establish a room/host-wall opening.

## All saved dwelling jobs

These are every directory under `output/web_review` whose `room_use_resolution.json` has `dwelling_context.is_dwelling: true`. Glazing count is the sum of saved `material.hourly_load_model.rooms[*].cooling_load.glazing_surfaces`. A missing input set is shown separately.

| Job directory | Rooms in input set | Glazing surfaces | Window scan / placement |
|---|---:|---:|---|
| `2026.06.26-architectural-drawings-1793599162` | 18 | 0 | v1: 28 schedule windows, 0 placed; unplaced reasons include unmatched marks and no outside wall within 900 mm. Review and rerun placement. |
| `2026.06.26-architectural-drawings-1795082136` | 12 | 8 | v13: 28 / 8. |
| `2026.06.26-architectural-drawings-1795159765` | 15 | 6 | v13: 28 / 6. |
| `2026.08.06-architectural-plans-1793621938` | 16 | 9 | v1: 30 / 9. |
| `2026.08.06-architectural-plans-1794999739` | 5 | 0 | v13: 30 / 0; `unplaced` reports no outside wall within 3000 mm for plan tags. Review room traces, wall classification, and tag placement. |
| `22008---cc---rev-c8--139a-burns-road-turramurra_1_-1794567651` | 12 | 0 | v1: 0 / 0; stale schedule scan described above. |
| `22008---cc---rev-c8--139a-burns-road-turramurra_1_-1794692047` | 17 | 4 | v7: 28 / 4. |
| `22008---cc---rev-c8--139a-burns-road-turramurra_1_-1794981374` | 15 | 14 | v13: 28 / 12; proposal has 14 openings, including windows assigned without a traced wall. |
| `22008---cc---rev-c8--139a-burns-road-turramurra_1_-1795157091` | 13 | 7 | v13: 28 / 7. |
| `22008---cc---rev-c8--139a-burns-road-turramurra_1_-1795251018` | 27 | 6 | v13: 28 / 6. |
| `architectural-plan---132-old-south-head-road_-vaucluse---1h-20251124-1795317033` | 16 | 18 | v13: 26 / 19. |
| `architectural-plan---132-old-south-head-road_-vaucluse---1h-20251124-1795345309` | — | — | No `ai_preliminary_input_set.json`; v13 scan has 26 / 9. Assemble an input set to assess modeled glazing. |

The three existing input sets with zero glazing are the first, fifth, and sixth rows. This audit does not establish whether every nonzero job has complete window coverage.

## Verification

Read-only call to the current `ai.house_windows.read_schedules` on PDF words from Turramurra's saved schedule page 17: 28 windows, 0 unread rows. The same check on Vaucluse page 20: 26 windows. No new regression test or code change is warranted for this saved-state failure.

Requested command: `ARCHIE_ASSUMPTION_RANGE=off python3 -m unittest discover -s tests -q` — 1,622 tests, 1 failure and 11 errors. Reported failures outside this trace include denied local socket binding (`PermissionError`) in provider/video tests and the Chrome PDF test receiving the minimal PDF fallback instead of Chrome. The suite therefore did not fully pass in this sandbox.
