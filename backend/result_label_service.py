"""Read-only maturity label for the saved full cooling result."""

from backend import assumption_range_service


def label(project, *, status=None):
    paths = assumption_range_service.ai_preliminary_service._paths(project)
    report = assumption_range_service._read(paths["report"])
    if assumption_range_service._total(report) is None:
        return {"status": "not_calculated", "count": 0, "open": [], "text": "Not calculated",
                "reason": "No final cooling load has been calculated yet."}

    if status is None:
        from backend import job_service, web_app
        status = job_service.status(web_app, project)
    stale = bool(status.get("result_stale") or not status.get("results_ready", True))
    unknowns = [row for row in assumption_range_service.open_unknowns(paths["root"], report) if row["open"]]
    rng = assumption_range_service.get(project, start=False)
    calculated = rng.get("status") == "calculated"
    ranged = {row["key"]: row for row in rng.get("unknowns", [])} if calculated else {}
    open_rows = [{"key": row["key"], "label": row["label"],
                  "changes_unit": (bool(ranged[row["key"]].get("changes_unit"))
                                   if calculated and row["key"] in ranged and ranged[row["key"]].get("swing_kw") is not None else None),
                  "swing_kw": ranged.get(row["key"], {}).get("swing_kw") if calculated else None}
                 for row in unknowns]
    # An incomplete case cannot prove that an open input leaves the unit unchanged.
    unresolved = [row for row in open_rows if row["changes_unit"] is not False]
    count = len(unresolved)
    if stale:
        state, text, reason = "preliminary", "Preliminary (out of date)", "The saved cooling load is out of date; update it before using it for design."
    elif count:
        state = "preliminary"
        text = (f"Preliminary ({count} {'answer' if count == 1 else 'answers'} still open; range isn't worked out yet)"
                if not calculated else f"Preliminary ({count} {'answer' if count == 1 else 'answers'} that could change the unit still open)")
        reason = ("The range isn't worked out yet; every open unknown is counted until its effect on the unit is known."
                  if not calculated else "Open answers could change the suggested unit.")
    else:
        state, text = "design", "Design"
        reason = (f"{len(open_rows)} {'input is' if len(open_rows) == 1 else 'inputs are'} still assumed, but the calculated range does not change the unit."
                  if open_rows else "No unit-changing inputs remain open.")
    return {"status": state, "count": count, "open": open_rows, "text": text, "reason": reason}
