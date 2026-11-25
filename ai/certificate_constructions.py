"""Conservative reading of construction values in a certificate PDF text layer.

Only labelled, adjacent values are accepted. PDF page numbers below are drawing-set pages.
The resulting U is a preliminary one-dimensional total-R estimate, not an
AS/NZS 4859.2 thermal-bridging compliance calculation.
"""
import re
import subprocess
from pathlib import Path


def _field(lines, label, stop=()):
    for index, line in enumerate(lines[:-1]):
        if line.casefold() == label.casefold():
            for following in lines[index + 1:index + 4]:
                if following.casefold() in stop:
                    break
                if following:
                    return following
    return ""


def parse_page(text, page, certificate="BASIX"):
    """Return only explicitly printed wall/roof/ceiling insulation and plan-up bearing."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)
    result = {"page": page, "certificate": certificate}
    wall_block = re.search(r"(?ims)^WALLS\s*\n(.*?)^Insulation External Walls\s*$", joined)
    wall_types = re.findall(r"(?im)^(Brick Veneer|Cavity Brick|Double Brick|Concrete|Blockwork|Lightweight Cladding)\s*$",
                            wall_block.group(1)) if wall_block else []
    wall_r = re.search(r"(?im)^Insulation External Walls\s*\n\s*R\s*([0-9]+(?:\.[0-9]+)?)\b", joined)
    if wall_types and len({name.casefold() for name in wall_types}) == 1 and wall_r:
        excerpt = f"{wall_types[0]}; Insulation External Walls R{wall_r.group(1)}"
        result["wall"] = {"construction": wall_types[0], "insulation_r": float(wall_r.group(1)), "excerpt": excerpt}
    ceiling = re.search(r"(?im)^CEILINGS\s*\n\s*(Ceilings?[^\n]+)\s*\n\s*R\s*([0-9]+(?:\.[0-9]+)?)\b", joined)
    if ceiling:
        result["ceiling"] = {"construction": ceiling.group(1), "insulation_r": float(ceiling.group(2)),
                              "excerpt": f"{ceiling.group(1)} R{ceiling.group(2)}"}
    roof = re.search(r"(?im)^ROOF AREA\s*\n(?:[^\n]*\n){0,8}?\s*Material\s*\n\s*([^\n]+)", joined)
    roof_r = re.search(r"(?im)^ROOF AREA\s*\n(?:[^\n]*\n){0,12}?\s*Insulation\s*\n\s*R\s*([0-9]+(?:\.[0-9]+)?)\b", joined)
    if roof:
        result["roof_material"] = roof.group(1)
    if roof and roof_r:
        result["roof"] = {"construction": roof.group(1), "insulation_r": float(roof_r.group(1)),
                          "excerpt": f"Roof material {roof.group(1)}; insulation R{roof_r.group(1)}"}
    # A north arrow or a window's compass orientation cannot establish the drawing page's rotation.
    north = re.search(r"(?im)^Plan[- ]up azimuth\s*[:=]\s*(\d+(?:\.\d+)?)\s*(?:°|degrees?)\s*(?:true north)?\s*$", joined)
    if north and 0 <= float(north.group(1)) < 360:
        result["north"] = {"plan_up_azimuth_deg": float(north.group(1)), "excerpt": north.group(0).strip()}
    return result


_READ_JOB_MEMO = {}


def _stamp(path):
    try:
        stat = Path(path).stat()
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def read_job(root):
    """read_job_uncached, remembered until the PDF, the page inventory or the OCR file changes: it runs pdftotext over
    the whole PDF (about 1.3 s on a 22-page set), and the job summary, result label and range all ask for it."""
    import json
    root = Path(root)
    try:
        pdf = json.loads((root / "spatial_ocr.json").read_text()).get("source_pdf") or ""
    except (OSError, ValueError):
        pdf = ""
    key = (_stamp(root / "page_inventory.json"), _stamp(root / "spatial_ocr.json"), _stamp(pdf) if pdf else None)
    hit = _READ_JOB_MEMO.get(str(root))
    if hit and hit[0] == key:
        return [dict(row) for row in hit[1]]
    found = read_job_uncached(root)
    _READ_JOB_MEMO[str(root)] = (key, found)
    if len(_READ_JOB_MEMO) > 64:
        _READ_JOB_MEMO.pop(next(iter(_READ_JOB_MEMO)))
    return [dict(row) for row in found]


def read_job_uncached(root):
    """Read certificate-titled pages from the original PDF without altering saved jobs."""
    import json
    root = Path(root)
    inventory = root / "page_inventory.json"
    spatial = root / "spatial_ocr.json"
    if not inventory.is_file() or not spatial.is_file():
        return []
    pages = json.loads(inventory.read_text()).get("pages") or {}
    pdf = Path(json.loads(spatial.read_text()).get("source_pdf") or "")
    if not pdf.is_file():
        return []
    # The whole text layer once (pages split by form feeds). A page is a certificate when its title says so or its own
    # printed text does: the AI's page titles vary between runs ("Basic Commitments" for Turramurra's BASIX page 2 on
    # one job, 2026-11-23), the certificate's printed words don't.
    try:
        texts = subprocess.run(["pdftotext", "-raw", str(pdf), "-"], check=True, capture_output=True, text=True,
                               timeout=60).stdout.split("\f")
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for number, row in sorted(pages.items(), key=lambda item: int(item[0]) if str(item[0]).isdigit() else 0):
        if not str(number).isdigit() or not isinstance(row, dict) or not 1 <= int(number) <= len(texts):
            continue
        title = str(row.get("title") or "")
        output = texts[int(number) - 1]
        named = re.search(r"\b(NatHERS|BASIX)\b", title, re.I) or re.search(r"\b(NatHERS|BASIX)\b", output, re.I)
        if not named:
            continue
        kind = "NatHERS" if "nathers" in (title + " " + named.group(1)).casefold() else "BASIX"
        value = parse_page(output, int(number), kind)
        if any(key in value for key in ("wall", "ceiling", "roof", "north")):
            found.append(value)
    return found


def u_value(kind, construction, insulation_r, ceiling_r=0):
    """AIRAH 2007 pp.150-173 style series total-R estimate for summer heat flow.

    Wall: indoor film .12 + 10 mm plasterboard/.17 + stated bulk insulation +
    100 mm vertical cavity .16 + 110 mm brick/.758 (brick veneer), or just
    wall layers named below + outdoor film .04. Roof with ceiling: indoor
    downward film .16 + 10 mm plasterboard/.17 + ceiling insulation +
    ventilated pitched roof space .46 + tile 19 mm/.84 or sheet steel
    .48 mm/45 + outdoor film .04. An additional printed roof R is added.
    Framing thermal bridges, compressed insulation and penetrations are not
    resolved by this estimate and require construction review.
    """
    name = construction.casefold()
    if kind == "wall":
        if "veneer" in name:
            layer = .110 / .758 + .16
        elif "cavity brick" in name or "double brick" in name:
            layer = 2 * .110 / .758 + .16
        elif "concrete" in name:
            layer = .150 / 1.44
        elif "block" in name:
            layer = .190 / .95
        elif "cladding" in name:
            layer = .006 / .25
        else:
            raise ValueError("No supported wall layers for this certificate construction")
        total = .12 + .010 / .17 + insulation_r + layer + .04
    else:
        if "tile" in name:
            cover = .019 / .84
        elif any(word in name for word in ("metal", "steel", "sheet", "colorbond")):
            cover = .00048 / 45
        else:
            raise ValueError("No supported roof layers for this certificate construction")
        total = .16 + .010 / .17 + ceiling_r + .46 + cover + insulation_r + .04
    return round(1 / total, 4)
