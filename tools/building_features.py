#!/usr/bin/env python3
"""Which building features each permitted drawing set prints, for the evaluation coverage map (Build queue task 7).

    python3 tools/building_features.py --cases <cases.json> [--out <folder>]

The cases file (kept outside the repository, because file names carry addresses) maps a neutral label to a PDF:

    {"house15": "/abs/path/to/drawings.pdf", "shop-bb": "/abs/path/to/other.pdf"}

Only the PDF's text layer is read: no AI, no network, nothing saved into a job. A feature is reported with the pages
whose text names it. "Not printed" means the text layer doesn't name it, not that the building lacks it (a void drawn
but never labelled isn't seen, and a scanned page has no text at all). A word printed on nearly every page is more
likely title-block or general-note text than a feature of this building, so such matches are flagged, not counted.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Each feature: the printed phrases that name it. Whole words, any case. Kept to phrases that name the feature itself,
# not words that merely occur near it ("existing" alone appears in most notes, so it isn't one).
FEATURES = {
    "basement or lower ground": [r"basement", r"lower ground", r"cellar"],
    "split level": [r"split[- ]level", r"half[- ]level"],
    "void or double-height space": [r"void", r"double[- ]height"],
    "raked, skillion or cathedral ceiling": [r"raked ceiling", r"raking ceiling", r"skillion", r"cathedral",
                                             r"vaulted", r"sloping ceiling", r"raked"],
    "attached garage or carport": [r"garage", r"carport"],
    "second dwelling (granny flat, dual occupancy)": [r"granny flat", r"secondary dwelling", r"ancillary dwelling",
                                                      r"dual occupancy", r"dwelling [ab12]"],
    "attached house (duplex, semi, townhouse)": [r"duplex", r"semi[- ]detached", r"townhouses?", r"terrace house",
                                                 r"party wall"],
    # Not bare "demolition" or "to be retained": standard safety notes and tree notes print those on new houses too.
    "existing building or demolition plans (alterations, or knock-down)": [r"alterations and additions", r"alterations & additions", r"demolition plan",
                                  r"demolition (?:basement|ground|first|lower ground|second) floor plan",
                                  r"existing (?:basement |ground |first |lower ground |second )?floor plan",
                                  r"(?:existing )?structures to be demolished"],
    "courtyard or atrium": [r"courtyard", r"atrium", r"light ?well"],
    "large sliding or folding doors": [r"stacker", r"bi-?fold", r"sliding door"],
    "skylight or roof window": [r"skylights?", r"roof window"],
    "eaves, pergola or awning": [r"eaves?", r"pergola", r"awning", r"louvres?"],
    "lift": [r"lift(?![- ]off)"],   # not "lift-off hinge door"
    "pool": [r"pool"],
    "several buildings on the site": [r"building [ab12]\b", r"pool house", r"cabana", r"outbuilding", r"studio building"],
}
LEVEL_NAMES = [r"basement", r"lower ground(?: floor)?", r"ground floor", r"first floor", r"second floor",
               r"third floor", r"level [0-9]", r"attic", r"loft", r"mezzanine", r"roof terrace"]
EVERY_PAGE_SHARE = 0.8      # named on 80 % or more of a set's pages: likely title block or general notes
SCANNED_WORDS = 20          # a page with fewer words than this has no usable text layer


def _compile(patterns):
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.IGNORECASE)


MATCHERS = {name: _compile(patterns) for name, patterns in FEATURES.items()}
LEVELS = _compile(LEVEL_NAMES)


def detect(pages):
    """pages: the text of each page, in order (page 1 first). Returns the features with page evidence."""
    total = len(pages)
    scanned = [number for number, text in enumerate(pages, 1) if len(text.split()) < SCANNED_WORDS]
    features = {}
    for name, matcher in MATCHERS.items():
        hits = [number for number, text in enumerate(pages, 1) if matcher.search(text)]
        examples = sorted({matcher.search(pages[number - 1]).group(0).lower() for number in hits})[:4]
        everywhere = total >= 3 and len(hits) >= EVERY_PAGE_SHARE * total
        features[name] = {"pages": hits, "examples": examples, "every_page": everywhere,
                          "printed": bool(hits) and not everywhere}
    levels = sorted({re.sub(r"\s+", " ", match.group(0).lower()) for text in pages for match in LEVELS.finditer(text)})
    return {"pages": total, "scanned_pages": scanned, "drawing": "scanned" if len(scanned) > total / 2 else "CAD (text layer)",
            "level_names": levels, "features": features}


def read_pdf(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


def table(results):
    labels = list(results)
    lines = ["| Feature | " + " | ".join(labels) + " | Cases |", "|---|" + "---|" * len(labels) + "---|"]
    lines.append("| Drawing type | " + " | ".join(results[label]["drawing"] for label in labels) + " | |")
    lines.append("| Level names printed | " + " | ".join(", ".join(results[label]["level_names"]) or "none"
                                                       for label in labels) + " | |")
    for name in FEATURES:
        cells, count = [], 0
        for label in labels:
            row = results[label]["features"][name]
            if row["printed"]:
                count += 1
                shown = row["pages"][:5]
                cells.append("p" + ", ".join(map(str, shown)) + ("…" if len(row["pages"]) > 5 else ""))
            elif row["every_page"]:
                cells.append("every page: check")   # title-block text, or a whole-project fact (e.g. "Dwelling A")
            else:
                cells.append("—")
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {count} |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    results = {}
    for label, pdf in cases.items():
        results[label] = detect(read_pdf(pdf))
        print(f"{label}: {results[label]['pages']} pages, {results[label]['drawing']}", file=sys.stderr, flush=True)
    markdown = table(results)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "building_features.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
        (out / "building_features.md").write_text(markdown + "\n", encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
