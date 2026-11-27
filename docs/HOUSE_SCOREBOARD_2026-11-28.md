# House scoreboard baseline (28 November 2026)

Build queue task 6. Each PDF-only house result is scored against a checked answer for the same PDF using
`tools/score_house_pdf_only.py`. Both jobs are recalculated with this checkout's code (main at `cf0c8f3`) on copies,
with no AI calls; no saved job was changed. **A checked answer is a careful hand check of the drawings, not
engineering validation.** These numbers are the baseline that later tasks must beat.

## Results

| House | PDF-only kW | Checked kW | Load error | Said complete? | Checked floor covered | PDF-only floor outside it | Glass found (m²) | Checked glass (m²) |
|---|---|---|---|---|---|---|---|---|
| house16 | 19.6 | 41.3 | −52% | yes (false) | 63% | 32 m² | 10.3 | 117.6 |
| house17 | 11.2 | 14.5 | −23% | yes (false) | 81% | 86 m² | 15.5 | 45.3 |
| house18 | 21.5 | 27.3 | −22% | yes (false) | 75% | 99 m² | 38.6 | 93.8 |

- All three PDF-only results are low, by 22–52%.
- All three called themselves complete when they should not have. They do carry a partial label ("3 / 8 / 4 rooms not
  counted"), but the complete flag is still set. That contradiction is what task 9 (self-checks) is for.
- Glass is the biggest single gap: PDF-only finds 9–41% of the checked glass. On house16 it finds no east or west
  glass at all (checked: 45 m² east, 47 m² west), likely the largest part of its 22 kW shortfall (not separated out here).
- Floor: PDF-only outlines cover 63–81% of the checked cooled floor, and 32–99 m² of PDF-only outline lies outside
  it (rooms drawn in the wrong place, or uncooled spaces counted).
- North: house16's north matches (43.85° vs 43.84°). On house18, PDF-only north is 311.8° against the checked
  47.25°, 95° out, so every facing on that house is wrong. house17's checked job has no north declared, so its
  glass facings can't be compared.

## Which jobs were scored

| House | Checked job | PDF-only job | Notes |
|---|---|---|---|
| house16 | operator check, 7 Nov (`…1794038079`) | 20 Nov (`…1795159765`) | The checked job's PDF upload was in a removed worktree; an identical copy (same SHA-256 as its outlines) was used. |
| house17 | outlines retraced and reviewed 26 Nov (`…1794567651`, the 14.48 kW calculation-check reference) | 21 Nov (`…1795251018`) | Checked by retrace review, not an operator check like 16 and 18. |
| house18 | operator check, 8 Nov (`…1794102196`) | 22 Nov (`…1795317033`) | Same stand-in PDF rule as house16. The newer 22 Nov run (`…1795345309`) stopped before its weather and load model were built and can't be calculated. |

Not scored:

- **Set 15:** its checked job isn't on this machine (not in `output/` or any worktree). The 15 Nov
  commit `b3cb07c` recorded 63.4 kW checked for it, on older code.
- **Sets 19 and 20:** no checked job exists yet.

## Tool changes made for this run

- `checked_pdf` / `ai_pdf` in the cases file: an identical copy of the drawing PDF for a job whose upload is gone.
  It's used only in the copy, and refused unless its SHA-256 matches the fingerprint the job's outlines were drawn on.
- A job that can't be calculated is recorded as "not scored" with the reason, and the other cases still run.
- A room is cooled floor only when it has a load. Before this, house17's checked job counted a zero-load 986 m²
  driveway and a 291 m² fence as cooled (1,578 m² against the reference 296 m²).

The cases file is kept in the session scratchpad, not in the repository, because job folder names carry addresses.
