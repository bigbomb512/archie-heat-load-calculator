# Synthetic Hourly Cooling Baseline

Run `PYTHONPATH=. python3 tools/create_synthetic_hourly_cooling_baseline.py` from the repository root to create the local project named **SYNTHETIC — Hourly Cooling Baseline (NOT FOR DESIGN)**.

The fixture exercises the actual backend workflow:

1. save provisional site design conditions;
2. save five cited-but-synthetic weekday schedules;
3. save one provisional 24-hour January cooling design-day scenario;
4. seed then review a one-room/one-zone hourly model;
5. calculate a provisional hourly cooling report.

Every numerical input is synthetic and every artifact is deliberately provisional. The case also writes an unresolved `synthetic-no-drawings` coverage exception, so a final calculation is blocked. It is not a real project, benchmark case, code-compliance example or basis for equipment selection.

The generated artifacts are local runtime output under `output/web_review/synthetic-hourly-cooling-baseline/` and are intentionally ignored by Git. The reproducible generator is [`tools/create_synthetic_hourly_cooling_baseline.py`](../tools/create_synthetic_hourly_cooling_baseline.py).
