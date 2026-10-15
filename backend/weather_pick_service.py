"""The weather location Phase 1 uses when nobody has chosen one (user decision 2026-10-15: the AI picks, labelled).

The summer design days come from the nearest listed location in the AIRAH Technical Handbook table (ai.design_weather).
The operators' choice on the Project tab always wins. Without it, the location is picked from every clue about the
site: the job's address, the site text the AI read from the drawings (e.g. "Tenancy MZ01, M38, Melrose Central"), the
project's name and file name, and title-block lines. A listed town or a state named in those clues is used directly;
otherwise one small text-only AI call reads the clues (it knows, for instance, which suburb a centre is in) and picks
a listed location, which is checked against the table. The pick and the reason are saved in weather_location_ai.json and
shown in the result as AI-picked, so it is never mistaken for a confirmed site.
"""

import hashlib
import json
import re
from pathlib import Path

from ai import design_weather
from backend import ai_provider

PICK_FILE = "weather_location_ai.json"
PURPOSE = "skill:weather_location"


def _read(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def clues(root, project):
    """The site clues, as [{"kind", "text"}]; consultants' office addresses are marked, since they are not the site."""
    root = Path(root)
    setup = _read(root / "job_setup.json", {})
    site = _read(root / "site_location_ai_determination.json", {})
    rows = []
    consultants = [" ".join(str(row["text"]).split())[:160] for row in site.get("consultant_addresses") or []
                   if isinstance(row, dict) and row.get("text")]
    for kind, text in (("job address", setup.get("address")), ("site on the drawings", site.get("site_text")),
                       ("job name", setup.get("name") or (project or {}).get("name"))):
        if str(text or "").strip():
            rows.append({"kind": kind, "text": " ".join(str(text).split())[:200]})
    # Only an explicitly labelled project or site address from the title block; a consultant's office never counts.
    ai_input = _read(root / "ai_input.json", {})
    lines = []
    for page in (ai_input.get("drawing_set") or {}).get("pages", [])[:40]:
        text = " ".join(str((page.get("structured_content") or {}).get("markdown", "")).split()) if isinstance(page, dict) else ""
        for match in re.finditer(r"\b(?:PROJECT|SITE)\s+ADDRESS\s*:?\s*(.{10,90})", text, re.I):
            line = match.group(0)[:120]
            office = any(_words(office) & _words(line) >= _words(office) - {"nsw", "vic", "qld", "sa", "wa", "tas", "nt", "act"}
                         for office in consultants)
            if not office and line.casefold() not in {row.casefold() for row in lines}:
                lines.append(line)
        if len(lines) >= 2:
            break
    rows.extend({"kind": "title block", "text": line} for line in lines[:2])
    rows.extend({"kind": "consultant's office (not the site)", "text": text} for text in consultants)
    return rows


def _words(text):
    return set(re.findall(r"[a-z0-9]+", str(text).casefold()))


def _fingerprint(rows, names):
    return hashlib.sha256(json.dumps([rows, sorted(names)], sort_keys=True).encode("utf-8")).hexdigest()


def prompt(rows, names):
    return ("Which of these listed weather locations is nearest the project's site? Read the site clues; a "
            "consultant's office address is not the site, so use it only if nothing else points to a state. "
            f"Listed locations: {', '.join(sorted(names))}.\nSite clues:\n"
            + "\n".join(f"- {row['kind']}: {row['text']}" for row in rows)
            + '\nReply with JSON only: {"location": one listed name exactly, or "" if the clues don\'t place the site, '
              '"site": "your reading of the site\'s suburb and state", "why": "one short sentence"}')


def pick(web, project, provider=None):
    """Pick and save the location when none is chosen; returns the saved pick or {}. Never raises for a failed call."""
    root = Path(project["review_dir"])
    if _read(root / "job_setup.json", {}).get("weather_location"):
        return {}
    table = design_weather.load_table()
    names = set((table or {}).get("locations") or {})
    if not names:
        return {}
    rows = clues(root, project)
    if not rows:
        return {}
    fingerprint = _fingerprint(rows, names)
    saved = _read(root / PICK_FILE, {})
    if saved.get("clues_fingerprint") == fingerprint:
        return saved
    # A listed town or a state named in the site's own details (never a consultant's office) needs no AI call.
    site_rows = [row for row in rows if row["kind"] in {"job address", "site on the drawings", "job name"}]
    direct = design_weather.suggested_location(" ".join(row["text"] for row in site_rows), table=table)
    if direct:
        result = {"location": direct, "site": "", "origin": "named_in_clues", "model": "",
                  "why": "A listed location or the state is named in the site details."}
    else:
        try:
            chosen = provider or ai_provider.recorded(ai_provider.get(PURPOSE), root, PURPOSE)
            reply, _raw = chosen.propose(prompt(rows, names))
        except ai_provider.UsageLimitReached:
            return {}
        except Exception:
            return {}
        location = design_weather.canonical((reply or {}).get("location")) if isinstance(reply, dict) else ""
        if location not in names:
            location = ""
        result = {"location": location, "site": str((reply or {}).get("site") or "")[:120], "origin": "ai_picked",
                  "model": getattr(chosen, "model", "") or "",
                  "why": str((reply or {}).get("why") or "")[:240]}
    result.update({"schema_version": 1, "clues_fingerprint": fingerprint, "clues": rows})
    (root / PICK_FILE).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def effective(root):
    """(location, note): the operators' choice (note ""), else the saved pick with a note for the result's label, else
    ("", "") and the labelled generic design day is used."""
    root = Path(root)
    chosen = str(_read(root / "job_setup.json", {}).get("weather_location") or "")
    if chosen:
        return chosen, ""
    saved = _read(root / PICK_FILE, {})
    location = design_weather.canonical(saved.get("location")) if saved.get("location") else ""
    if not location:
        return "", ""
    if saved.get("origin") == "named_in_clues":
        return location, "named in the site details; change it on the Project tab"
    site = f" (site read as {saved['site']})" if saved.get("site") else ""
    return location, f"picked by the AI from the site details{site}; change it on the Project tab"
