"""Read-only loading of the versioned runtime-skill catalog and guidance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_CATALOG_PATH = _ROOT / "config" / "archie_skills_v1.json"
_SUBSKILL_REGISTRY_PATH = _ROOT / "config" / "archie_subskills_v1.json"


def load_catalog():
    try:
        return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("The Archie runtime skills catalog is unavailable or invalid.") from error


def load_subskill_registry():
    try:
        return json.loads(_SUBSKILL_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("The Archie runtime subskills registry is unavailable or invalid.") from error


def catalog_fingerprint():
    catalog = load_catalog()
    instruction = _instruction_text(catalog)
    subskills = load_subskill_registry()
    payload = json.dumps({"catalog": catalog, "instructions": instruction, "subskills": subskills}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _instruction_text(catalog):
    relative = Path(catalog.get("instruction_file", ""))
    path = (_ROOT / relative).resolve()
    if _ROOT not in path.parents or not path.is_file():
        raise ValueError("The runtime skills instruction file is invalid or missing.")
    return path.read_text(encoding="utf-8")


def _section(text, heading, *, stop_at=None):
    """Read one exact Markdown heading section, stopping at the next peer."""
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        raise ValueError("A runtime skill instruction section is missing.")
    level = stop_at or len(heading) - len(heading.lstrip("#"))
    body = []
    for line in lines[start + 1:]:
        if line.startswith("#") and len(line) - len(line.lstrip("#")) <= level:
            break
        body.append(line)
    return "\n".join(body).strip()


def compose_subskill_instructions(parent_id, subskill):
    """Compose canonical safeguards with the parent and bounded task method."""
    catalog = load_catalog()
    text = _instruction_text(catalog)
    shared = _section(text, "## Shared control policy")
    parent = _section(text, f"## Parent playbook: {parent_id}", stop_at=3)
    task = _section(text, f"### Subskill: {subskill['id']}")
    return "\n\n".join((shared, parent, task))


def vision_guidance():
    """Return shared extraction guidance; this performs no provider or network call."""
    catalog = load_catalog()
    registry = load_subskill_registry()
    active = set(catalog.get("enabled_skill_ids", catalog.get("pilot_skill_ids", [])))
    skills = [
        {key: skill.get(key) for key in ("id", "version", "purpose", "subskills", "allowed_evidence", "output_contract")}
        for skill in catalog.get("skills", []) if skill.get("id") in active
    ]
    # The PDF vision pass is shared once. Focused subskill procedures are
    # composed later by the dependency-aware workers, avoiding a giant,
    # duplicated prompt on every page group.
    active_subskills = [{key: row.get(key) for key in ("id", "parent", "task", "proposal_fields")}
        for row in registry.get("subskills", []) if row.get("parent") in active]
    text = _instruction_text(catalog)
    return {
        "catalog_id": catalog.get("catalog_id", ""),
        "catalog_version": catalog.get("schema_version"),
        "skills": skills,
        "subskills": active_subskills,
        "proposal_envelope": registry.get("proposal_envelope", {}),
        "instructions": _section(text, "## Shared control policy"),
    }
