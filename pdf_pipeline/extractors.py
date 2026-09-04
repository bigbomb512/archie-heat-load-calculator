import re
import subprocess

import pdfplumber

from pdf_pipeline.rules import CEILING_CONSTRAINT_WORDS, HVAC_WORDS, KNOWN_TITLES


def clean_text(text):
    return re.sub(r"\s+", " ", text.lower()).strip()


def word_found(text, word):
    if len(word) <= 3:
        pattern = r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return word in text


def found_words(text, words):
    return [word for word in words if word_found(text, word)]


def title_area(raw_text):
    lines = useful_lines(raw_text)
    return clean_text("\n".join(lines[:25] + lines[-35:]))


def useful_lines(raw_text):
    return [line.strip() for line in raw_text.splitlines() if line.strip()]


def extract_text_pages(pdf_path):
    pages = extract_with_poppler(pdf_path)
    if pages:
        return pages
    return extract_with_pdfplumber(pdf_path)


def extract_with_poppler(pdf_path):
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        pages = result.stdout.split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        return pages
    except Exception:
        return []


def extract_with_pdfplumber(pdf_path):
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text(layout=True) or "")
    return pages


def count_pdf_pages(pdf_path):
    count = count_pages_with_pdfinfo(pdf_path)
    if count:
        return count
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
    except Exception:
        return 0


def count_pages_with_pdfinfo(pdf_path):
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return 0

    match = re.search(r"^Pages:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    return int(match.group(1)) if match else 0


def extract_pdf_title(pdf_path):
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""

    match = re.search(r"^Title:\s*(.+?)\s*$", result.stdout, re.MULTILINE)
    return match.group(1).strip() if match else ""


def extract_drawing_title(raw_text):
    lines = useful_lines(raw_text)

    title = title_near_project_manager(lines)
    if title:
        return title

    area = title_area(raw_text)
    for known_title in KNOWN_TITLES:
        if known_title.lower() in area:
            return known_title

    title = title_after_drawing_title_label(lines)
    if title:
        return title

    for known_title in KNOWN_TITLES:
        if known_title.lower() in raw_text.lower():
            return known_title

    return ""


def title_near_project_manager(lines):
    for index, line in enumerate(lines):
        if "project manager" not in line.lower():
            continue

        before_label = re.sub(r"(?i)project manager:.*", "", line).strip()
        title = known_title_from_text(before_label)
        if title:
            return title

        block = []
        for previous_line in lines[max(0, index - 10) : index]:
            if line_is_title_noise(previous_line):
                continue
            block.append(previous_line)

        block_text = " ".join(block)
        title = known_title_from_text(block_text)
        if title:
            return title
        if block_text and "note:" not in block_text.lower():
            return re.sub(r"\s+", " ", block_text).strip()

    return ""


def title_after_drawing_title_label(lines):
    for index, line in enumerate(lines):
        if "drawing title" not in line.lower():
            continue

        title_lines = []
        after_label = re.sub(r"(?i).*drawing title", "", line).strip()
        if after_label:
            title_lines.append(after_label)

        for next_line in lines[index + 1 : index + 8]:
            lower = next_line.lower()
            if (
                "project manager" in lower
                or "drawing number" in lower
                or "project architect" in lower
                or "project designer" in lower
                or "note:" in lower
                or re.search(r"^[a-z]{1,3}\d", lower)
                or re.search(r"^\d+/\d+/\d+", lower)
            ):
                break
            if len(next_line) <= 90:
                title_lines.append(next_line)

        title = re.sub(r"\s+", " ", " ".join(title_lines)).strip()
        if title:
            return title

    return ""


def line_is_title_noise(line):
    lower = line.lower()
    return (
        lower in ["j", "k", "d"]
        or "drawing title" in lower
        or "issues / revisions" in lower
        or "description" in lower
        or len(line) > 120
        or re.search(r"^[a-z]\d+(\.\d+)?$", lower)
        or re.search(r"\d+/\d+\" = ", lower)
        or re.search(r"^\d+\s+\d+/\d+\" = ", lower)
    )


def known_title_from_text(text):
    lower = text.lower()
    for title in KNOWN_TITLES:
        if title.lower() in lower:
            return title
    return ""


def guess_title(raw_text):
    title = extract_drawing_title(raw_text)
    if title:
        return title

    lines = useful_lines(raw_text)
    for line in reversed(lines[-35:]):
        if 8 <= len(line) <= 90 and not re.search(r"^\d+(\.\d+)?$", line):
            return line
    return "Unknown"


def extract_page_info(raw_text, page_type):
    scale = extract_scale(raw_text)
    rooms = extract_rooms(raw_text)
    dimensions = extract_written_dimensions(raw_text)
    constraints = found_words(clean_text(raw_text), CEILING_CONSTRAINT_WORDS)
    hvac_terms = found_words(clean_text(raw_text), HVAC_WORDS)
    level_name = extract_level_name(raw_text)

    return {
        "scale": scale,
        "level_name": level_name,
        "written_dimensions": dimensions,
        "rooms": rooms,
        "ceiling_constraints": constraints,
        "hvac_terms": hvac_terms,
        "drawing_number": extract_drawing_number(raw_text),
        "needs_manual_scale_confirmation": scale is None,
        "notes_for_ai": ai_notes(page_type, scale, dimensions, rooms, constraints, hvac_terms),
    }


def extract_level_name(raw_text):
    text = re.sub(r"\s+", " ", raw_text).strip()
    title = extract_drawing_title(raw_text)
    candidates = [title, text]

    patterns = [
        r"\b(basement(?:\s+(?:floor|level))?)\b(?!\s+\d+)",
        r"\b(lower\s+ground(?:\s+floor)?)\b",
        r"\b(ground\s+floor|ground\s+level|main\s+level|main\s+floor)\b",
        r"\b(level\s+\d+[a-z]?|level\s+[a-z])\b",
        r"\b(floor\s+\d+[a-z]?)\b",
        r"\b(first\s+floor|second\s+floor|third\s+floor|fourth\s+floor|fifth\s+floor)\b",
        r"\b(roof\s+level|roof\s+plan)\b",
    ]

    for candidate in candidates:
        for pattern in patterns:
            match = re.search(pattern, candidate, re.IGNORECASE)
            if match:
                return normalise_level_name(match.group(1))
    return ""


def normalise_level_name(value):
    cleaned = value.lower().strip()
    if cleaned == "roof plan":
        return "Roof"
    if cleaned in {"basement", "basement floor"}:
        return "Basement"
    if cleaned == "lower ground":
        return "Lower Ground Floor"
    words = value.lower().split()
    normalised = []
    for word in words:
        if re.fullmatch(r"\d+[a-z]?", word):
            normalised.append(word.upper())
        else:
            normalised.append(word.title())
    return " ".join(normalised)


def extract_scale(raw_text):
    patterns = [
        r"scale\s*@?\s*[a-z0-9]*\s*(\d+\s*:\s*\d+)",
        r"scale\s*[:\-]?\s*(\d+\s*:\s*\d+)",
        r"\b(1\s*:\s*(?:5|10|20|25|50|75|100|125|150|200|250|500))\b",
        r"(\d+/\d+\"\s*=\s*\d+'-?\d*\")",
        r"(\d+\"\s*=\s*\d+'-?\d*\")",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if match:
            return normalise_scale(match.group(1))
    return None


def normalise_scale(scale):
    scale = re.sub(r"\s+", " ", scale).strip()
    return re.sub(r"\s*:\s*", ":", scale)


def extract_rooms(raw_text):
    rooms = []
    lines = useful_lines(raw_text)

    for index, line in enumerate(lines):
        area = re.search(r"(\d+(?:\.\d+)?)\s*m²", line, re.IGNORECASE)
        if not area:
            area = re.search(r"(\d+(?:\.\d+)?)\s*(sf|sq\.?\s*ft\.?)", line, re.IGNORECASE)
        if not area:
            continue

        name = room_name_for_area(lines, index, area)
        if name:
            rooms.append({"name": name, "area": area.group(0)})

    return unique_items(rooms)


def extract_written_dimensions(raw_text):
    dimensions = []

    for line in useful_lines(raw_text):
        if is_metadata_line(line):
            continue

        clean_line = line.replace(",", "")
        for match in re.finditer(r"(?<![\w.])(\d{3,5})(?![\w.])", clean_line):
            value = int(match.group(1))
            if 400 <= value <= 50000 and not 1900 <= value <= 2099:
                dimensions.append({"value": value, "unit": "mm"})

        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*m\b", clean_line, re.IGNORECASE):
            dimensions.append({"value": float(match.group(1)), "unit": "m"})

    return unique_items(dimensions)[:40]


def is_metadata_line(line):
    lower = line.lower()
    metadata_words = [
        "date",
        "project",
        "drawing number",
        "project manager",
        "project architect",
        "project designer",
        "production leader",
        "peer reviewer",
        "copyright",
        "users\\",
        "revit",
        "description",
        "north brookfield",
    ]
    if any(word in lower for word in metadata_words):
        return True
    if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", line):
        return True
    if re.search(r"\d{1,2}:\d{2}", line):
        return True
    if re.search(r"\b[A-Z]{2}\s+0?\d{5}\b", line):
        return True
    return False


def room_name_for_area(lines, index, area):
    before_area = lines[index][: area.start()].strip()
    if looks_like_room_name(before_area):
        return before_area

    name = previous_room_name(lines, index)
    if name:
        if name == "Counter" and nearby_has_word(lines, index, "service"):
            return "Service Counter"
        return name

    candidates = room_candidates_near(lines, index)
    if not candidates:
        return ""
    if candidates[-1] == "Counter" and nearby_has_word(lines, index, "service"):
        return "Service Counter"
    if len(candidates) >= 2 and len(candidates[-1].split()) == 1 and len(candidates[-2].split()) == 1:
        return f"{candidates[-2]} {candidates[-1]}"
    return candidates[-1]


def previous_room_name(lines, index):
    parts = []
    for line in reversed(lines[max(0, index - 5) : index]):
        if is_room_code(line):
            continue
        if looks_like_room_name(line):
            parts.append(line)
            if len(parts) == 2:
                break
            continue
        if parts:
            break
    return " ".join(reversed(parts)).strip()


def room_candidates_near(lines, index):
    candidates = []
    for line in lines[max(0, index - 8) : index + 1]:
        for chunk in re.split(r"\s{2,}", line):
            chunk = re.sub(r"^[•\-\s]+", "", chunk).strip()
            words = re.findall(r"[A-Za-z][A-Za-z/&'-]*", chunk)
            if len(words) > 4:
                words = words[-2:]
            candidate = " ".join(words)
            if looks_like_room_name(candidate):
                candidates.append(candidate)
    return candidates


def nearby_has_word(lines, index, word):
    nearby = " ".join(lines[max(0, index - 8) : index + 1]).lower()
    return word in nearby


def looks_like_room_name(line):
    line = line.strip()
    lower = line.lower()
    bad_words = [
        "drawing",
        "scale",
        "project",
        "revision",
        "date",
        "number",
        "legend",
        "fhr",
        "fob",
        "ntd",
        "pt",
        "mt",
        "lm",
        "vy",
        "ex.",
        "finish",
        "commencing",
        "cross rail",
        "top cross",
    ]
    if any(word in lower for word in bad_words):
        return False
    if len(line) < 3 or len(line) > 45:
        return False
    if line.startswith("(") or line.endswith(")") or line.isupper():
        return False
    if re.fullmatch(r"x(?:\s+x)*", lower):
        return False
    if re.search(r"\d", line):
        return False
    return re.search(r"[a-zA-Z]", line) is not None


def is_room_code(line):
    line = line.strip()
    return (
        re.search(r"^\d{2,}\.\d+$", line) is not None
        or re.search(r"^[A-Z]{1,3}\d*$", line) is not None
        or re.search(r"^\d+$", line) is not None
    )


def extract_drawing_number(raw_text):
    lines = useful_lines(raw_text)
    for index, line in enumerate(lines):
        if line.lower() == "drawing number" and index + 1 < len(lines):
            return lines[index + 1]

    for pattern in [r"\bMD\d+\.\d+\b", r"\bM\d+\.\d+\b", r"\bA\d+\.\d+\b", r"\bGE-M\d+\b", r"\b\d{2}\.\d{2}\b"]:
        match = re.search(pattern, raw_text)
        if match:
            return match.group(0)
    return ""


def ai_notes(page_type, scale, dimensions, rooms, constraints, hvac_terms):
    notes = []
    if scale:
        notes.append(f"Scale appears to be {scale}.")
    else:
        notes.append("Scale not confidently extracted; ask the user to confirm scale.")
    if dimensions:
        notes.append(f"Detected {len(dimensions)} written dimensions on the drawing.")
    if rooms:
        notes.append(f"Detected {len(rooms)} room/area labels.")
    if constraints:
        notes.append("Ceiling/design constraints mentioned: " + ", ".join(constraints[:8]) + ".")
    if hvac_terms:
        notes.append("HVAC terms mentioned: " + ", ".join(hvac_terms[:8]) + ".")

    if page_type == "reflected_ceiling_plan":
        notes.append("Use this page for diffuser/register placement and ceiling coordination.")
    elif page_type == "floor_plan":
        notes.append("Use this page for walls, room layout, boundaries, and general design context.")
    elif page_type == "existing_hvac_or_services_plan":
        notes.append("Use this page to identify existing HVAC services and connection points.")

    return notes


def unique_items(items):
    seen = set()
    unique = []
    for item in items:
        key = tuple(sorted(item.items()))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique
