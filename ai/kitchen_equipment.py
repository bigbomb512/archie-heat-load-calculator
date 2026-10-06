"""Identify the kitchen equipment drawn on the plans (Card Q, AI task P6).

This task only lists the appliances; it assigns no heat. Heat to space and
hood exhaust wait for a licensed source (user decision 2026-10-05: AIRAH DA09).

The packet holds a plan crop around the kitchen, the kitchen elevation sheets,
and the short text labels from those areas. The AI lists each appliance with a
type from a fixed list and either a quote of the drawing text or a description
of the drawn symbol it used. Every quote is checked against the supplied
labels, so an item cannot be invented from nothing.
"""

import re

from ai.autonomous_task_scoring import KITCHEN_TYPES


BUDGET_CHARS = 2000
MAX_IMAGE_SIDE = 1536
MAX_LABEL_CHARS = 30
# "door" is not listed: fridge labels are often split over lines ("3 DOOR" / "FRIDGE").
NOT_HEAT_SOURCES = re.compile(r"\b(sink|basin|rack|shelv|bench|bin|trolley|wall|tile|skirting|grout|gpo|partition)\b", re.I)


def _norm(text):
    return " ".join(str(text or "").casefold().split())


def find_kitchen_elevation_pages(ai_input, spatial_ocr):
    """Pages that show the kitchen in elevation, by their title or text."""
    titles = {row.get("page"): str(row.get("title", "")) for row in (ai_input or {}).get("drawing_set", {}).get("pages", [])
              if isinstance(row, dict)}
    pages = []
    for page in (spatial_ocr or {}).get("pages", []):
        number = page.get("page")
        text = " ".join(str(item.get("text", "")) for item in page.get("standalone_text_items", []) or [])
        title = titles.get(number, "")
        if re.search(r"kitchen", title, re.I) or re.search(r"kitchen\s+elevation|elevation\s+\d?\s*kitchen", text, re.I):
            pages.append(number)
    return sorted({page for page in pages if isinstance(page, int)})


def _page_items(spatial_ocr, page_number, region_pt=None):
    """Text items on a page, outside its title block (sheet title, consultants, revisions)."""
    from ai.room_inference import _inside_title_block
    for page in (spatial_ocr or {}).get("pages", []):
        if page.get("page") != page_number:
            continue
        items = page.get("standalone_text_items", []) or []
        for item in items:
            bbox = item.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4 or _inside_title_block(item, page, items):
                continue
            if region_pt:
                left, top, right, bottom = region_pt
                if bbox[2] < left or bbox[0] > right or bbox[3] < top or bbox[1] > bottom:
                    continue
            yield item


def page_labels(spatial_ocr, page_number, region_pt=None):
    """Short text lines on a page (optionally inside a PDF-point region).

    Words are joined into lines (the text layer stores single words); long
    notes and bare numbers are left out, since equipment labels are short.
    """
    from ai.room_inference import _text_item_lines
    lines = [" ".join(str(line).split()) for line, _bbox in _text_item_lines(list(_page_items(spatial_ocr, page_number, region_pt)))]
    unique = []
    for text in lines:
        if not text or len(text) > MAX_LABEL_CHARS or not re.search(r"[A-Za-z]{2,}", text):
            continue
        if _norm(text) not in {_norm(item) for item in unique}:
            unique.append(text)
    return unique


def page_words(spatial_ocr, page_number, region_pt=None):
    """Every word on the page (normalised), for checking quotes that wrap over lines."""
    return {word for item in _page_items(spatial_ocr, page_number, region_pt)
            for word in re.findall(r"[a-z0-9]+", str(item.get("text", "")).casefold())}


def kitchen_prompt(label_lines):
    types = ", ".join(sorted(KITCHEN_TYPES))
    return ("The images show the kitchen of one tenancy: a crop of the floor plan and the kitchen elevations. "
            "List the cooking and refrigeration equipment that is drawn. For each item give its type from this list: "
            f"{types}. Count each physical appliance once, even if it appears on the plan and in an elevation; a canopy "
            "over an island counts once even if it is drawn in two elevations. For each item quote the drawing text that "
            "names it, exactly as it appears in the labels below, or, if it has no text, describe the drawn symbol you "
            "used and say on which image it is. Leave out sinks, basins, racks, benches and shelving (they give no heat). "
            "If you cannot tell what a symbol is, leave it out. Reply with JSON only:\n"
            "{\"items\": [{\"type\": string, \"count\": int, \"page\": int, \"quote\": string|null, "
            "\"symbol\": string|null, \"under_hood\": bool}]}\n\nLabels by page:\n" + label_lines)


def build_packet(plan_page, kitchen_bbox_px, elevation_pages, labels_by_page, words_by_page=None):
    """Describe the packet and its prompt; refuse (ValueError) if it would exceed the budget."""
    lines = "\n".join(f"p. {page}: " + " | ".join(labels) for page, labels in sorted(labels_by_page.items()) if labels)
    prompt = kitchen_prompt(lines)
    if len(prompt) > BUDGET_CHARS:
        raise ValueError(f"The kitchen labels make the prompt {len(prompt)} characters, over the {BUDGET_CHARS}-character "
                         "budget; the task is blocked rather than dropping drawing text.")
    images = [{"page": plan_page, "kind": "plan_crop", "bbox_px": list(kitchen_bbox_px)}]
    images += [{"page": page, "kind": "elevation_page", "bbox_px": None} for page in elevation_pages]
    if len(images) > 3:
        raise ValueError("At most one plan crop and two elevation sheets fit the kitchen task budget.")
    return {"task": "P6_kitchen", "plan_page": plan_page, "images": images,
            "labels_by_page": {str(page): labels for page, labels in labels_by_page.items()},
            "words_by_page": {str(page): sorted(words) for page, words in (words_by_page or {}).items()}}, prompt


def validate_reply(reply, packet):
    """Check the AI's equipment list against the fixed types and the supplied labels."""
    if not isinstance(reply, dict) or not isinstance(reply.get("items"), list):
        raise ValueError("Kitchen reply must contain an items list.")
    pages = {image["page"] for image in packet.get("images", [])}
    labels = {int(page): [_norm(text) for text in values] for page, values in packet.get("labels_by_page", {}).items()}
    words = {int(page): set(values) for page, values in packet.get("words_by_page", {}).items()}
    items = []
    for row in reply["items"]:
        if not isinstance(row, dict):
            raise ValueError("Each kitchen item must be an object.")
        kind = str(row.get("type", "")).strip()
        if kind not in KITCHEN_TYPES:
            raise ValueError(f"Unknown kitchen equipment type: {kind!r}.")
        count = row.get("count")
        if type(count) is not int or not 1 <= count <= 30:
            raise ValueError(f"The count for {kind} must be a whole number from 1 to 30.")
        page = row.get("page")
        if page not in pages:
            raise ValueError(f"{kind} cites page {page!r}, which is not one of the supplied images.")
        quote, symbol = row.get("quote"), row.get("symbol")
        if quote:
            wanted = _norm(quote)
            in_label = any(wanted == label or wanted in label for label in labels.get(page, []))
            quote_words = re.findall(r"[a-z0-9]+", wanted)
            on_page = bool(quote_words) and set(quote_words) <= words.get(page, set())
            if not (in_label or on_page):
                raise ValueError(f"The quote for {kind} ({quote!r}) is not text on page {page}.")
            if NOT_HEAT_SOURCES.search(quote) and not re.search(r"fridge|freezer|oven|hood|fryer|grill|wok", quote, re.I):
                raise ValueError(f"{quote!r} names something that gives no heat; leave it out.")
        elif not (isinstance(symbol, str) and len(symbol.strip()) >= 8):
            raise ValueError(f"{kind} needs either a quote of the drawing text or a description of the drawn symbol.")
        items.append({"type": kind, "count": count, "page": page, "quote": quote or None,
                      "symbol": (symbol or "").strip()[:200] or None, "under_hood": bool(row.get("under_hood")),
                      "evidence_kind": "text" if quote else "symbol"})
    return items


def to_determinations(items, source="ai_determined"):
    """The P6_kitchen list scored by ai/autonomous_task_scoring.score_kitchen."""
    totals = {}
    for item in items:
        totals[item["type"]] = totals.get(item["type"], 0) + item["count"]
    return [{"type": kind, "count": count, "source": source} for kind, count in sorted(totals.items())]
