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


def _instruction_root(catalog):
    """The folder of skill instructions: shared_policy.md, and per parent skill <parent>/playbook.md and
    <parent>/<subskill>.md (one file each, so an edit to one skill changes only that skill's instructions)."""
    root = _ROOT.resolve()
    path = (root / Path(catalog.get("instruction_dir", ""))).resolve()
    if root not in path.parents or not path.is_dir():
        raise ValueError("The runtime skills instruction folder is invalid or missing.")
    return path


def instruction_path(catalog, parent_id=None, subskill_id=None):
    """Where one instruction file lives: the shared policy, a parent's playbook, or one sub-skill's procedure."""
    root = _instruction_root(catalog)
    if parent_id is None:
        return root / "shared_policy.md"
    return root / parent_id / f"{subskill_id or 'playbook'}.md"


def _read_instruction(path):
    """A file's text without its title line (the title names the file; the worker gets the body)."""
    if not path.is_file():
        raise ValueError("A runtime skill instruction file is missing: " + "/".join(path.parts[-2:]))
    text = path.read_text(encoding="utf-8")
    if text.startswith("# "):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    return text.strip()


def catalog_fingerprint():
    """Every skill's instructions and definitions together (the vision pass's guidance identity)."""
    catalog = load_catalog()
    root = _instruction_root(catalog)
    instructions = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.md"))}
    subskills = load_subskill_registry()
    payload = json.dumps({"catalog": catalog, "instructions": instructions, "subskills": subskills}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def subskill_instructions_fingerprint(subskill):
    """The identity of exactly what one sub-skill is told: an edit elsewhere doesn't make it run again."""
    text = compose_subskill_instructions(subskill["parent"], subskill)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compose_subskill_instructions(parent_id, subskill):
    """Compose canonical safeguards with the parent and bounded task method."""
    catalog = load_catalog()
    shared = _read_instruction(instruction_path(catalog))
    parent = _read_instruction(instruction_path(catalog, parent_id))
    task = _read_instruction(instruction_path(catalog, parent_id, subskill["id"]))
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
    return {
        "catalog_id": catalog.get("catalog_id", ""),
        "catalog_version": catalog.get("schema_version"),
        "skills": skills,
        "subskills": active_subskills,
        "proposal_envelope": registry.get("proposal_envelope", {}),
        "instructions": _read_instruction(instruction_path(catalog)),
    }
