"""Wall and roof constructions with their U-values, from an imported table (the AIRAH Technical Handbook, 4th ed. 2007,
"Overall heat transfer coefficients (U)", pp. 152-161).

The table is copyright AIRAH and is never stored in this repository: tools/import_airah_handbook_u_values.py reads
the operators' own copy of the handbook and writes it to a file outside the repo (DEFAULT_TABLE_PATH, or the path in
ARCHIE_U_VALUE_TABLE). Roofs use the handbook's summer value (heat flow down); walls have one value.

An operator picks the construction for a job's external walls or exposed roof (or one room's) as an answer on the
What-we-need-to-find list, or types a U-value from another source; until then the calculation uses the assumption
pack's preliminary U-value and the Results tab lists it as typical. No I/O except load_table.
"""

import json
import os
from pathlib import Path

DEFAULT_TABLE_PATH = Path.home() / ".archie" / "airah_handbook_2007_u_values.json"
TABLE_ENV = "ARCHIE_U_VALUE_TABLE"
SOURCE = "AIRAH Technical Handbook, 4th edition (2007), Overall heat transfer coefficients (U)"
# The handbook's tables that hold each kind of surface.
SECTIONS = {"wall": ("Masonry walls", "Frame walls", "Sandwich panel walls"), "roof": ("Flat roofs", "Pitched roofs")}
U_RANGE = (0.1, 7.0)       # W/m²K


def table_path():
    return Path(os.environ.get(TABLE_ENV, "").strip() or DEFAULT_TABLE_PATH)


def load_table(path=None):
    """The imported table ({"source", "constructions": [{"id", "surface", "section", "name", "u_value_w_m2k"}]}),
    keeping only wall and roof rows with a U-value in range; None when absent, unreadable or empty."""
    path = Path(path) if path else table_path()
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = table.get("constructions") if isinstance(table, dict) else None
    clean, seen = [], set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        surface = next((kind for kind, sections in SECTIONS.items() if row.get("section") in sections), "")
        u_value = row.get("u_value_w_m2k")
        if (not surface or not str(row.get("id") or "") or row["id"] in seen or not str(row.get("name") or "").strip()
                or not isinstance(u_value, (int, float)) or isinstance(u_value, bool) or not U_RANGE[0] <= u_value <= U_RANGE[1]):
            continue
        seen.add(row["id"])
        clean.append({"id": row["id"], "surface": surface, "section": row["section"], "name": " ".join(str(row["name"]).split()),
                      "u_value_w_m2k": float(u_value)})
    return {"source": str(table.get("source") or SOURCE), "constructions": clean} if clean else None


def choices(table, surface):
    """The table's constructions for one kind of surface ("wall" or "roof"), as answer options."""
    return [{"id": row["id"], "label": f"{row['name']} — U {row['u_value_w_m2k']:g} W/m²K ({row['section'].lower()})",
             "u_value_w_m2k": row["u_value_w_m2k"]}
            for row in (table or {}).get("constructions", []) if row["surface"] == surface]


def find(table, construction_id):
    return next((row for row in (table or {}).get("constructions", []) if row["id"] == construction_id), None)
