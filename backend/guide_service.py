"""The job's steps in plain words, in order, with the one to do now: the workspace's "Next step" card.

Each step says what to do, why it matters in one line, how many things it covers, and where to go (a tab and
sub-tab) or what the button does (calculate). The step to do now is the first one not done whose inputs exist: the
steps that check the AI's work wait while the AI is still reading the drawings, and the project steps can be done
meanwhile. Read-only: it gathers from the job status and the review's own status files and changes nothing.
"""

from backend import drawing_tasks_service, job_service, page_extraction_service, page_inventory_service, skill_workflow_service

BUSY = {"queued", "running"}
STOPPED = {"failed", "blocked", "interrupted"}


def _step(step_id, title, why, state, tab="", subtab="", label="", count=None, kind="go", needs_ai=False):
    return {"id": step_id, "title": title, "why": why, "state": state, "count": count, "needs_ai": needs_ai,
            "action": {"kind": kind, "tab": tab, "subtab": subtab, "label": label}}


def _plural(count, word, plural=None):
    return f"{count} {word if count == 1 else plural or word + 's'}"


def steps(web, project):
    """{"steps": [...], "now": step id or "", "done": count, "total": count}."""
    status = job_service.status(web, project)
    tabs, subtabs = status.get("tabs", {}), status.get("subtabs", {})
    reading = page_inventory_service.status(web, project)
    equipment = page_extraction_service.status(web, project, "equipment_appliances")
    drawing = drawing_tasks_service.status(web, project)
    try:
        review = skill_workflow_service.get(web, project)
    except Exception:
        review = {}
    findings = review.get("findings") or []
    answers = review.get("answers") or {}
    needs = [row for row in findings if row.get("subskill_id") == "information_needs" and row.get("field") == "needs"]
    open_needs = sum(1 for row in needs if row.get("id") not in answers)
    needs_listed = (bool(needs) or any(row.get("subskill_id") == "information_needs" for row in findings)
                    or review.get("status") in {"needs_review", "completed", "complete", "done"})
    open_findings = sum(1 for row in findings if row.get("subskill_id") != "information_needs"
                        and row.get("status") not in {"accepted", "rejected"})
    open_equipment = equipment.get("open") or 0

    result = []
    # 1. The AI reads the drawings (pages, equipment, rooms, then the skills).
    jobs = [("reading every page", reading), ("reading the equipment", equipment), ("tracing the rooms", drawing),
            ("working through the drawings", review)]
    working = next((label for label, job in jobs if job.get("status") in BUSY), "")
    # The skills review counts as stopped only when it failed or produced nothing; "blocked" with findings means one
    # step waits for an answer (e.g. the old weather-source step), and everything else it found is ready.
    stopped = next(((label, job) for label, job in jobs if job.get("status") in STOPPED
                    and not (job is review and job.get("status") == "blocked" and findings)), None)
    if tabs.get("drawings", {}).get("state") == "needed":
        result.append(_step("ai", "Let the AI read your drawings", "Open the Drawings tab: Toki prepares the pages and the AI "
                            "reads every one (about 10–20 minutes). You can do the next steps meanwhile.", "needed",
                            "drawings", "overview", "Start"))
    elif working:
        result.append(_step("ai", "The AI is reading your drawings", f"It is {working} now. Nothing to do here: carry on with "
                            "the steps below and come back when it finishes.", "working", "drawings", "overview", "Show progress"))
    elif stopped:
        label, job = stopped
        issue = next((row.get("reason") for row in (job.get("issues") or []) if row.get("reason")), "")
        reason = job.get("problem") or job.get("error") or job.get("blocked_reason") or issue
        result.append(_step("ai", "The AI review stopped before it finished", f"It stopped while {label}"
                            + (f": {reason}" if reason else ".") + " Press Retry on the Drawings overview; what it already "
                            "read is kept.", "error", "drawings", "overview", "Go to retry"))
    else:
        result.append(_step("ai", "The AI has read your drawings", "Pages, equipment and rooms are read.", "done",
                            "drawings", "overview", "Show"))
    ai_ready = result[0]["state"] == "done"

    # 2. The project: the address (for the weather) and what is above the shop (for the roof).
    site = subtabs.get("project", {}).get("job_site", {})
    found = status.get("found_site") or ""
    weather_ready = bool(status.get("weather_location")) or not status.get("weather_locations")
    if not status.get("address"):
        why = (f"The drawings give it as “{found}”. Check it, choose its weather location and save, so the right summer "
               "weather is used." if found else "Type the job's address and choose its weather location, so the right "
               "summer weather is used for the load.")
        result.append(_step("address", "Confirm the site address", why, "needed", "project", "job-site", "Check the address"))
    elif not weather_ready:
        suggestion = status.get("weather_suggestion") or ""
        result.append(_step("address", "Choose the weather location", (f"Suggested from the address: {suggestion}. " if suggestion
                            else "") + "The summer design weather comes from the nearest listed location in the AIRAH "
                            "handbook; until you choose, a generic Australian day is used.", "needed", "project", "job-site",
                            "Choose the weather location"))
    else:
        result.append(_step("address", "Confirm the site address", f"Saved: {status['address']}"
                            + (f"; weather from {status['weather_location']}." if status.get("weather_location") else "."),
                            "done", "project", "job-site", "Show"))
    tenancy = subtabs.get("project", {}).get("tenancy_context", {})
    result.append(_step("above", "Say what is above the shop", "The roof, or another floor or tenancy? It decides whether "
                        "heat comes in through the roof.", "done" if tenancy.get("state") == "done" else "needed",
                        "project", "tenancy-context", "Answer"))

    # 3. Rooms, walls, roof and windows: check what the AI found.
    rooms = subtabs.get("rooms", {})
    room_details, measurements = rooms.get("room_details", {}), rooms.get("measurements", {})
    if room_details.get("state") in {"needed", "check"}:
        result.append(_step("rooms", "Check the rooms", f"{room_details.get('detail')} Each room's use sets its people, "
                            "lights and equipment.", "needed", "rooms", "room-details", "Check the rooms",
                            room_details.get("count"), needs_ai=True))
    elif measurements.get("state") in {"needed", "check"}:
        result.append(_step("rooms", "Check the room sizes", f"{measurements.get('detail')} Areas come from the AI's "
                            "outlines or printed figures.", "needed", "rooms", "measurements", "Check the sizes",
                            measurements.get("count"), needs_ai=True))
    else:
        result.append(_step("rooms", "Check the rooms", room_details.get("detail") or "Rooms are ready.",
                            "done" if tabs.get("rooms", {}).get("state") == "done" else "later", "rooms", "room-details",
                            "Show", needs_ai=True))
    walls = subtabs.get("walls", {})
    for step_id, key, title, why in (
            ("walls", "walls", "Check the walls", "What each wall faces (outside, a mall, a neighbour) decides the heat "
                                                  "coming through it."),
            ("roof", "roof", "Check the roof", "Whether the roof is above each room decides the heat through the ceiling.")):
        row = walls.get(key, {})
        state = {"needed": "needed", "check": "needed", "done": "done"}.get(row.get("state"), "later")
        result.append(_step(step_id, title, f"{row.get('detail') or ''} {why}".strip(), state, "walls", key,
                            title, row.get("count"), needs_ai=True))
    windows = tabs.get("windows", {})
    result.append(_step("windows", "Check the windows", f"{windows.get('detail') or ''} Glass lets in sun and heat."
                        .strip(), {"check": "needed", "needed": "needed", "done": "done"}.get(windows.get("state"), "later"),
                        "windows", "", "Check the windows", needs_ai=True))

    # 4. What the drawings don't give, and the AI's readings to confirm.
    result.append(_step("questions", "Answer what the drawings don't say",
                        "Each question says why it matters and where the answer is usually found (spec sheet, client, "
                        "site visit). Answer what you can; the rest stay as labelled assumptions." if needs_listed else
                        "The list of questions appears when the AI has worked through the drawings.",
                        "needed" if open_needs else "done" if needs_listed else "later", "drawings", "to-find",
                        f"Answer {_plural(open_needs, 'question')}" if open_needs else "Show", open_needs, needs_ai=True))
    result.append(_step("equipment", "Check the equipment the AI found",
                        "Accept, correct or reject each item; add the heat from its data sheet where you have it.",
                        "needed" if open_equipment else "done", "drawings", "equipment",
                        f"Check {_plural(open_equipment, 'item')}" if open_equipment else "Show", open_equipment, needs_ai=True))

    # 5. Calculate, then read the result; reviewing every finding is optional.
    results_tab = tabs.get("results", {})
    if results_tab.get("state") == "done":
        result.append(_step("calculate", "Calculate the cooling load", "Calculated with the latest inputs.", "done", "results", "", "Show"))
    else:
        result.append(_step("calculate", "Calculate the cooling load", "Press Calculate: it takes about a minute and uses "
                            "everything checked so far." if results_tab.get("state") == "todo" else
                            "Something changed since the last calculation: calculate again.", "needed", "results", "",
                            "Calculate now", kind="calculate", needs_ai=True))
    result.append(_step("findings", "Review the AI's other findings (optional)",
                        "Everything else the AI read or worked out, by topic. Accepting one can set an input (e.g. seats → "
                        "people).", "optional" if open_findings else "done", "drawings", "findings",
                        f"Review {_plural(open_findings, 'finding')}" if open_findings else "Show", open_findings, needs_ai=True))
    result.append(_step("result", "Read the result", "The total, each room, what's in it and what is still assumed.",
                        "done" if results_tab.get("state") == "done" else "later", "results", "", "Show the result"))

    # The step to do now: the first one needing you whose inputs exist; else wait for the AI; else read the result.
    now = next((row["id"] for row in result if row["state"] in {"needed", "error"} and (ai_ready or not row["needs_ai"]
                                                                                       or row["id"] == "ai")), "")
    if not now and result[0]["state"] == "working":
        now = "ai"
    if not now:
        now = next((row["id"] for row in result if row["state"] == "optional"), "result")
    done = sum(1 for row in result if row["state"] == "done")
    return {"steps": result, "now": now, "done": done, "total": len(result)}
