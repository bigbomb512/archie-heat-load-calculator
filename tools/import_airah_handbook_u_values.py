#!/usr/bin/env python3
"""Import the wall and roof U-values from the operators' own copy of the AIRAH Technical Handbook.

    python3 tools/import_airah_handbook_u_values.py ~/Downloads/AIRAH_Handbook.pdf [output.json]

Reads the "Overall heat transfer coefficients (U)" tables (each construction, its variants such as "without plaster",
and U = 1/RT; for roofs, floors and windows the summer value) and writes them to
ai.construction_u_values.DEFAULT_TABLE_PATH (~/.archie/…) or the given path. The tables are AIRAH's copyright, so
the output is refused inside this repository; the calculation offers the wall and roof rows as construction answers.
"""

import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import construction_u_values  # noqa: E402

SECTION_NAMES = ("Masonry walls", "Frame walls", "Sandwich panel walls", "Partitions", "Conditioned floors with a room below",
                 "Conditioned floors above ventilated space", "Flat roofs", "Pitched roofs", "Windows")
FIRST_LAYER = re.compile(r"\s{2,}[1I]\.\s")                    # "1. Outdoor air film" (OCR sometimes reads "I.")
U_LINE = re.compile(r"U\s*=\s*1\s*/\s*R\w?\s*=")
U_VALUE = re.compile(r"(\d+(?:\.\d+)?)\s*W\s*/\s*m")
VARIANT = re.compile(r"(?:^|\s{3,})(WITH(?:OUT)?\b[^\t]*?)\s*$")   # "WITHOUT PLASTER", "WITH 2mm VINYL TILES"


def _left(line):
    """The construction-name column of a line: its text before the first wide gap, when it starts near the margin."""
    match = re.match(r"^(\s{0,14})(\S.*?)(?:\s{3,}|\t|$)", line)
    if not match:
        return ""
    text = match.group(2).strip()
    heading = text.startswith("Overall heat transfer") or text in {"Construction", *SECTION_NAMES} or text.replace(" (cont)", "") in SECTION_NAMES
    return "" if heading or re.fullmatch(r"[\d\s]+", text) or not re.match(r"[A-Za-z]", text) or len(text) > 45 else text


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


def parse(text):
    """[{"id", "section", "name", "u_value_w_m2k"}] from the handbook's text (pdftotext -layout)."""
    # The tables themselves, not the contents list: the heading line followed by the first table's name.
    found = re.search(r"^[ \t]*Overall heat transfer coefficients \(U\)[ \t]*\n[ \t]*Masonry walls", text, re.MULTILINE)
    start = found.start() if found else -1
    end = text.find("Thermal properties of", start) if found else -1
    if start < 0 or end < 0:
        raise ValueError("The U-value tables weren't found in this PDF.")
    lines = text[start:end].splitlines()
    rows, section, name, variant, since_u = [], "", "", "", True
    for index, line in enumerate(lines):
        stripped = " ".join(line.split()).replace(" (cont)", "")
        if stripped in SECTION_NAMES:
            section, name, variant, since_u = stripped, "", "", True
            continue
        if not section:
            continue
        if FIRST_LAYER.search(line) and _left(line):
            # A new construction: its name may start on the line or two above (after the last U line).
            before = [_left(lines[back]) for back in (index - 2, index - 1) if back >= 0 and since_u and _left(lines[back])
                      and not U_LINE.search(lines[back]) and " ".join(lines[back].split()) not in SECTION_NAMES]
            name, variant, since_u = " ".join([*before, _left(line)]), "", False
            continue
        if name and not since_u and _left(line) and not U_LINE.search(line) and not FIRST_LAYER.search(line):
            name = f"{name} {_left(line)}"                      # the name continues on the next line ("indoor plaster")
        found = VARIANT.search(line)
        if found and name:
            variant = " ".join(found.group(1).split()).lower()
            continue
        if U_LINE.search(line) and name:
            values = U_VALUE.findall(line)
            for extra in lines[index + 1:index + 3]:
                if values:
                    break
                values = U_VALUE.findall(extra)
            if values:
                label = f"{name} ({variant})" if variant else name
                key = f"{_slug(section)}:{_slug(label)}"
                key = key if key not in {row["id"] for row in rows} else f"{key}-{len(rows) + 1}"
                rows.append({"id": key, "section": section, "name": label,
                             "u_value_w_m2k": float(values[-1])})    # the last is the summer value where two are given
            since_u = True
    return rows


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    pdf = Path(argv[1]).expanduser()
    out = Path(argv[2]).expanduser() if len(argv) > 2 else construction_u_values.DEFAULT_TABLE_PATH
    if ROOT in out.resolve().parents:
        print("Refusing to write AIRAH's tables inside the repository; choose a path outside it.")
        return 1
    text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    rows = parse(text)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": construction_u_values.SOURCE, "imported_from": pdf.name, "constructions": rows}, indent=1),
                   encoding="utf-8")
    counts = {}
    for row in rows:
        counts[row["section"]] = counts.get(row["section"], 0) + 1
    print(f"Imported {len(rows)} constructions to {out}: " + ", ".join(f"{key} {value}" for key, value in counts.items()) + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
