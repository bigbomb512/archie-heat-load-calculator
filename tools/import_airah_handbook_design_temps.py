#!/usr/bin/env python3
"""Import the summer comfort design temperatures from the operators' own copy of the AIRAH Technical Handbook.

    python3 tools/import_airah_handbook_design_temps.py ~/Downloads/AIRAH_Handbook.pdf [output.json]

Reads the "Design temperature data" table (Australian locations: CWB, DB, WB, CDB for comfort) and writes it to
ai.design_weather.DEFAULT_TABLE_PATH (~/.archie/…) or the given path. The table is AIRAH's copyright, so the output is
refused inside this repository; the calculation reads it from there (see ai/design_weather.py).
"""

import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import design_weather  # noqa: E402

ROW = re.compile(r"^([A-Z][A-Za-z .]+?)\s{2,}(-?\d+\.\d)\s+(-?\d+\.\d)\s+(-?\d+\.\d)\s+(-?\d+\.\d)\b")


def parse(text):
    """{location: {"cwb", "db", "wb", "cdb"}} from the handbook's text (pdftotext -layout)."""
    start = text.find("AUSTRALIA", text.find("Design temperature data", text.find("Section 2")))
    end = text.find("CWB = Coincident wet bulb", start)
    if start < 0 or end < 0:
        raise ValueError("The design temperature table wasn't found in this PDF.")
    rows = {}
    for line in text[start:end].splitlines():
        match = ROW.match(line.strip())
        name = design_weather.canonical(match.group(1)) if match else ""
        if name:
            cwb, db, wb, cdb = (float(value) for value in match.groups()[1:])
            rows[name] = {"cwb": cwb, "db": db, "wb": wb, "cdb": cdb}
    return rows


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    pdf = Path(argv[1]).expanduser()
    out = Path(argv[2]).expanduser() if len(argv) > 2 else design_weather.DEFAULT_TABLE_PATH
    if ROOT in out.resolve().parents:
        print("Refusing to write AIRAH's table inside the repository; choose a path outside it.")
        return 1
    text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    rows = parse(text)
    missing = sorted(set(design_weather.LOCATIONS) - set(rows))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": design_weather.SOURCE, "imported_from": pdf.name, "locations": rows}, indent=1),
                   encoding="utf-8")
    print(f"Imported {len(rows)} locations to {out}." + (f" Not found: {', '.join(missing)}." if missing else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
