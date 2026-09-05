import re
import shutil
import subprocess
import tempfile

import pdfplumber


def extract_structured_pdf(pdf_path, page_numbers=None):
    wanted = set(page_numbers or [])
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            if wanted and index not in wanted:
                continue
            pages.append(structured_page(page, index, pdf_path))
    return {
        "source_pdf": str(pdf_path),
        "page_count": len(pages),
        "pages": pages,
    }


def structured_page(page, page_number, pdf_path=None):
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
    text = page.extract_text(layout=True) or ""
    ocr_text, ocr_status = ocr_page_text(pdf_path, page_number) if needs_ocr(words, text) else ("", "not_needed")
    text = text or ocr_text
    tables = page.extract_tables() or []
    elements = word_elements(words) + table_elements(tables, page_number)

    return {
        "page": page_number,
        "width": round(page.width, 2),
        "height": round(page.height, 2),
        "text": text,
        "markdown": page_markdown(text, tables),
        "elements": elements,
        "word_count": len(words),
        "table_count": len(tables),
        "needs_ocr": ocr_status in ["needed_tesseract_missing", "needed_pdftoppm_missing", "ocr_failed"],
        "ocr_status": ocr_status,
    }


def needs_ocr(words, text):
    return len(words) == 0 and not text.strip()


def ocr_page_text(pdf_path, page_number):
    if not pdf_path:
        return "", "needed_tesseract_missing"
    if not shutil.which("tesseract"):
        return "", "needed_tesseract_missing"
    if not shutil.which("pdftoppm"):
        return "", "needed_pdftoppm_missing"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = f"{tmp}/page"
            subprocess.run(
                ["pdftoppm", "-png", "-f", str(page_number), "-singlefile", "-r", "300", str(pdf_path), prefix],
                check=True,
                capture_output=True,
            )
            result = subprocess.run(
                ["tesseract", f"{prefix}.png", "stdout"],
                check=True,
                capture_output=True,
                text=True,
            )
        return result.stdout.strip(), "used_tesseract"
    except Exception:
        return "", "ocr_failed"


def word_elements(words):
    elements = []
    for word in words:
        text = word.get("text", "").strip()
        if not text:
            continue
        elements.append(
            {
                "type": "word",
                "text": text,
                "bbox": bbox(word),
            }
        )
    return elements


def table_elements(tables, page_number):
    elements = []
    for index, table in enumerate(tables, start=1):
        rows = clean_table(table)
        if rows:
            elements.append(
                {
                    "type": "table",
                    "page": page_number,
                    "index": index,
                    "row_count": len(rows),
                    "column_count": max(len(row) for row in rows),
                    "markdown": table_markdown(rows),
                }
            )
    return elements


def bbox(word):
    return [
        round(word["x0"], 2),
        round(word["top"], 2),
        round(word["x1"], 2),
        round(word["bottom"], 2),
    ]


def page_markdown(text, tables):
    parts = []
    clean = normalize_text(text)
    if clean:
        parts.append(clean)
    for table in tables:
        rows = clean_table(table)
        if rows:
            parts.append(table_markdown(rows))
    return "\n\n".join(parts)


def normalize_text(text):
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def clean_table(table):
    rows = []
    for row in table:
        values = [normalize_cell(cell) for cell in row]
        if any(values):
            rows.append(values)
    return rows


def normalize_cell(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def table_markdown(rows):
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    separator = ["---"] * width
    body = padded[1:]

    lines = [markdown_row(header), markdown_row(separator)]
    lines.extend(markdown_row(row) for row in body)
    return "\n".join(lines)


def markdown_row(row):
    return "| " + " | ".join(escape_markdown(cell) for cell in row) + " |"


def escape_markdown(value):
    return value.replace("|", "\\|")


def useful_page_structure(structured_pdf, pages):
    by_page = {page["page"]: page for page in structured_pdf["pages"]}
    useful = []
    for page in pages:
        structured_page_data = by_page.get(page["page"])
        if structured_page_data:
            useful.append(compact_page_structure(structured_page_data))
    return useful


def compact_page_structure(page):
    return {
        "page": page["page"],
        "width": page["width"],
        "height": page["height"],
        "word_count": page["word_count"],
        "table_count": page["table_count"],
        "needs_ocr": page["needs_ocr"],
        "ocr_status": page.get("ocr_status", "not_checked"),
        "markdown": page["markdown"],
        "elements": page["elements"][:300],
    }
