"""Pass 2 of the PDF review: read the values of one kind of information from every page that holds it.

Pass 1 (ai/page_inventory.py) says which pages hold which kinds of heat-load information. Pass 2 has one
extractor per kind. Each extractor sends every flagged page (or section of a large sheet) to the AI on its
own, with a fixed list of fields to read, and checks the reply. Items from all pages are then merged by an
identity key: the same item on several pages becomes one finding with several page references, and pages
that disagree make the finding "conflicting".

Extractors read only what is printed. Turning a reading into heat (a hood factor, a typical rating for an
appliance without a printed power) is a separate, labelled step. Nothing here is specific to one drawing set.
No I/O.
"""

import hashlib
import json
import math
import re

MAX_IMAGE_PX = 2600     # longest side of one image sent to the AI; larger sheets are cut into sections
TILE_OVERLAP = 0.08     # sections overlap so an item on a cut line is whole in at least one section
MIN_TEXT_LAYER = 2000   # characters; a scanned or picture page often carries only its title block as text


def _text(value, limit=200):
    return " ".join(str(value or "").split())[:limit]


def tiles(width, height, max_px=MAX_IMAGE_PX, overlap=TILE_OVERLAP):
    """Sections (x0, y0, x1, y1) covering a page image; one section when it fits."""
    if width <= max_px and height <= max_px:
        return [(0, 0, width, height)]

    def count(length):
        # The fewest sections whose width, overlap included, still fits.
        parts = max(1, math.ceil(length / max_px))
        while parts > 1 and length / parts * (1 + 2 * overlap) > max_px or parts == 1 and length > max_px:
            parts += 1
        return parts

    cols, rows = count(width), count(height)
    step_x, step_y = width / cols, height / rows
    pad_x, pad_y = step_x * overlap, step_y * overlap
    return [(max(0, round(c * step_x - pad_x)), max(0, round(r * step_y - pad_y)),
             min(width, round((c + 1) * step_x + pad_x)), min(height, round((r + 1) * step_y + pad_y)))
            for r in range(rows) for c in range(cols)]


def normalise_name(value):
    text = str(value or "").lower()
    text = re.sub(r"\b(u/b|u\.b\.)", "underbench", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def size_numbers(value):
    """The numbers in a printed size, in order: "1500W x 700D x 850H mm" -> "1500x700x850"."""
    return "x".join(re.findall(r"\d+(?:\.\d+)?", str(value or "")))


# --- Equipment -------------------------------------------------------------------------------------------

EQUIPMENT_FIELDS = ("code", "name", "quantity", "size", "model", "supplier", "rated_power", "electrical",
                    "under_hood", "location", "evidence")

EQUIPMENT_PROMPT = """You are reading {where} of an architectural drawing set, for a commercial HVAC cooling heat-load calculation.
List every item of EQUIPMENT or APPLIANCE that gives off heat or uses power and is shown or listed here:
cooking equipment, fridges, freezers, display cases, ice makers, dishwashers, coffee machines, water boilers,
TVs, screens, POS terminals, computers, illuminated signs, and similar. Do NOT list: furniture, sinks or fittings
without power; light fittings (lighting is read separately); rooms or areas, even refrigerated ones (a coolroom or
freezer room is a room; list its refrigeration unit only if one is shown); shopfronts; brand names or logos unless
they are an illuminated sign (then name it "Illuminated sign"); merchandise or zone labels (e.g. "Snacks",
"Accessories") unless a powered unit such as a display fridge is shown for them.

Reply with JSON only (no prose, no code fence):
{{"items": [{{"code": "", "name": "", "quantity": null, "size": "", "model": "", "supplier": "", "rated_power": "",
  "electrical": "", "under_hood": null, "location": "", "evidence": ""}}]}}

code: the item's code or tag as printed (e.g. "E01", "K-03"), else "".
name: the item as printed (e.g. "Combi oven", "2 door drink fridge").
quantity: the number of this item if printed or countable on this page, else null.
size: the printed size with its units (e.g. "750W x 790D x 675H mm"), else "".
model / supplier: as printed, else "".
rated_power: ONLY a power printed for this item with its unit (e.g. "6.3 kW", "2400 W"), else "". Never estimate it.
electrical: a printed electrical supply for it (e.g. "3-phase 32 A", "15 A GPO", "240 V 10 A"), else "".
under_hood: true if the drawing shows it under an exhaust hood or canopy, false if clearly not, null if not shown.
location: the room or area it is in, if shown. evidence: a short quote or where on the page.
Copy printed words and numbers exactly. If the same item appears in a schedule and on a plan on this page, list it once.
If nothing on this {noun} qualifies, reply {{"items": []}}.
"""


def equipment_identity(item):
    if item.get("code"):
        return "code:" + normalise_name(item["code"]).replace(" ", "")
    return "name:" + normalise_name(item.get("name")) + (":" + size_numbers(item.get("size")) if size_numbers(item.get("size")) else "")


def validate_equipment_item(row):
    if not isinstance(row, dict):
        raise ValueError("Each item must be an object.")
    name = _text(row.get("name"), 120)
    if not name:
        raise ValueError("An item has no name.")
    quantity = row.get("quantity")
    if quantity in ("", None):
        quantity = None
    elif isinstance(quantity, str) and quantity.strip().isdigit():
        quantity = int(quantity.strip())
    elif type(quantity) is not int or quantity < 0:
        raise ValueError(f"Quantity for {name!r} must be a whole number or null.")
    hood = row.get("under_hood")
    if hood not in (True, False, None):
        raise ValueError(f"under_hood for {name!r} must be true, false or null.")
    return {"code": _text(row.get("code"), 20), "name": name, "quantity": quantity, "size": _text(row.get("size"), 80),
            "model": _text(row.get("model"), 80), "supplier": _text(row.get("supplier"), 80),
            "rated_power": _text(row.get("rated_power"), 40), "electrical": _text(row.get("electrical"), 60),
            "under_hood": hood, "location": _text(row.get("location"), 80), "evidence": _text(row.get("evidence"), 300)}


EXTRACTORS = {
    "equipment_appliances": {
        "kind": "equipment_appliances", "version": "equipment-v2", "label": "Equipment",
        "fields": EQUIPMENT_FIELDS, "prompt": EQUIPMENT_PROMPT, "identity": equipment_identity,
        "validate_item": validate_equipment_item, "name_field": "name",
        # Fields compared across pages: a different printed value makes the finding conflicting.
        "compared": ("quantity", "size", "model", "rated_power", "electrical", "under_hood"),
        # A heat-load value the drawings often leave out; the finding says so when it isn't printed.
        "wanted": ("rated_power",),
    },
}


def build_prompt(extractor, page, section=None, sections=1):
    return extractor["prompt"].format(where=_where(page, section, sections), noun="page" if sections == 1 else "section")


def _where(page, section=None, sections=1):
    return f"page {page}" if sections == 1 else f"section {section + 1} of {sections} of page {page} (sections overlap)"


def batch_prompt(extractor, keys):
    """Several pages (or sections) read in one call, each its own full-size image read on its own; saves the fixed
    cost of a call (about 4,600 tokens) for every image after the first. keys = [(page, section, sections)]."""
    listing = "; ".join(f"image {index} is {_where(page, section, count)}" for index, (page, section, count) in enumerate(keys, 1))
    body = extractor["prompt"].format(where="the attached images", noun="image")
    return (f"{len(keys)} images are attached, one per page or section: {listing}. Read each image on its own, as if it "
            f"were the only one, following the instructions below; give each image's reply under its number:\n"
            f'{{"images": {{"1": <that image\'s reply>, "2": ...}}}} and nothing else (JSON only, no prose, no code fence).\n\n{body}')


def validate_reply(extractor, reply):
    if not isinstance(reply, dict) or not isinstance(reply.get("items"), list):
        raise ValueError('The reply must be {"items": [...]}.')
    return [extractor["validate_item"](row) for row in reply["items"]]


def text_match(item, page_text, name_field="name"):
    """Is the item's name printed in the page's text layer? None when the page has no usable text layer
    (a scan, or a picture page whose only text is the title block)."""
    if not page_text or len(page_text.strip()) < MIN_TEXT_LAYER:
        return None
    words = [word for word in normalise_name(item.get(name_field)).split() if len(word) > 2]
    if not words:
        return None
    haystack = normalise_name(page_text)
    found = sum(1 for word in words if re.search(rf"\b{re.escape(word)}", haystack))
    return found / len(words) >= 0.6


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


# A picture of the design intent, not a specification: what is seen only there is inferred, not supported.
PICTURE_PAGE_TYPES = {"render_or_photo"}


def merge(extractor, readings, page_texts=None, page_types=None):
    """Merge items from every page into findings.

    readings = [(page, section index, [validated items])]; page_types = {page: pass 1 page_type}. Returns
    findings, each with the merged value, pages, citations, conflicting fields with their readings, and an
    evidence label: conflicting when pages disagree, inferred when seen only on renders or photos,
    else supported.
    """
    page_texts, page_types = page_texts or {}, page_types or {}
    groups = {}
    for page, section, items in readings:
        for item in items:
            groups.setdefault(extractor["identity"](item), []).append((page, section, item))
    findings = []
    for identity, rows in sorted(groups.items()):
        value, conflicts = {}, {}
        for field in extractor["fields"]:
            # Per page first: overlapping sections of one page can each show part of a count, so a
            # page's quantity is the largest its sections read; other fields keep every reading.
            per_page = {}
            for page, _section, item in rows:
                current = item.get(field)
                if current in (None, ""):
                    continue
                if field == "quantity":
                    per_page[page] = [max(per_page.get(page, [current]) + [current])]
                elif current not in per_page.setdefault(page, []):
                    per_page[page].append(current)
            options = []
            for page in sorted(per_page):
                for current in per_page[page]:
                    match = next((row for row in options if row["value"] == current), None)
                    if match:
                        match["pages"].append(page)
                    else:
                        options.append({"value": current, "pages": [page]})
            if field in extractor["compared"] and len(options) > 1:
                conflicts[field] = options
            value[field] = options[0]["value"] if options else (None if field in ("quantity", "under_hood") else "")
        pages = sorted({page for page, _s, _i in rows})
        matches = [text_match(item, page_texts.get(page), extractor["name_field"]) for page, _s, item in rows]
        known = [match for match in matches if match is not None]
        findings.append({
            "id": f"{extractor['kind']}:{_fingerprint(identity)}", "kind": extractor["kind"], "identity": identity,
            "value": value, "pages": pages,
            "citations": [{"page": page, "excerpt": item.get("evidence", "")} for page, _s, item in rows],
            "conflicts": conflicts,
            "evidence": "conflicting" if conflicts else "inferred" if all(page_types.get(page) in PICTURE_PAGE_TYPES for page in pages) else "supported",
            "pictures_only": all(page_types.get(page) in PICTURE_PAGE_TYPES for page in pages),
            "not_printed": [field for field in extractor["wanted"] if not value.get(field)],
            # Found when the name is printed on any cited page with a text layer: the check is for invented items.
            "text_match": None if not known else any(known),
        })
    return findings
