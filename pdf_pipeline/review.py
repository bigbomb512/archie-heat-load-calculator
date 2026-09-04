import html
import json
from pathlib import Path

from pdf_pipeline.page_finder import build_design_packet


def create_review_packet(pdf_path, output_dir=None, include_structure=False):
    pdf_path = Path(pdf_path)
    review_dir = Path(output_dir or Path("output") / "review" / safe_folder_name(pdf_path.stem))
    thumbnail_dir = review_dir / "thumbnails"
    review_dir.mkdir(parents=True, exist_ok=True)

    packet = build_design_packet(
        pdf_path,
        render_pages=True,
        thumbnail_dir=thumbnail_dir,
        include_structure=include_structure,
    )
    make_paths_relative(packet, review_dir)

    packet_path = review_dir / "packet.json"
    html_path = review_dir / "review.html"

    packet_path.write_text(json.dumps(packet, indent=2), encoding="utf-8")
    html_path.write_text(render_review_html(packet), encoding="utf-8")

    return {
        "review_dir": str(review_dir),
        "packet": str(packet_path),
        "html": str(html_path),
        "thumbnail_count": len(packet.get("thumbnails", [])),
        "primary_count": len(packet["primary_pages"]),
        "reference_count": len(packet["reference_pages"]),
        "kept_count": len(packet["kept_pages"]),
        "discarded_count": len(packet["discarded_pages"]),
        "structured_count": len(packet.get("structured_pages", [])),
    }


def safe_folder_name(name):
    safe = "".join(char if char.isalnum() or char in "._- " else "_" for char in name)
    return safe.strip() or "pdf_review"


def make_paths_relative(packet, review_dir):
    for thumbnail in packet.get("thumbnails", []):
        thumbnail["path"] = relative_path(thumbnail["path"], review_dir)

    pages = packet["primary_pages"] + packet["reference_pages"] + packet.get("kept_pages", [])
    for page in pages:
        if page.get("thumbnail_path"):
            page["thumbnail_path"] = relative_path(page["thumbnail_path"], review_dir)


def relative_path(path, base_dir):
    return Path(path).resolve().relative_to(Path(base_dir).resolve()).as_posix()


def render_review_html(packet):
    primary = packet["primary_pages"]
    reference = packet["reference_pages"]
    discarded = packet["discarded_pages"]
    all_kept = packet.get("kept_pages", primary + reference)

    cards = "\n".join(render_page_card(page) for page in all_kept)
    discarded_rows = "\n".join(render_discarded_page(page) for page in discarded)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HVAC PDF Review Packet</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172026;
      --muted: #64717d;
      --line: #d8dee4;
      --panel: #ffffff;
      --bg: #f5f7f9;
      --accent: #116466;
      --warn: #9a5b00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 2;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.96);
      padding: 18px 28px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    main {{ padding: 24px 28px 40px; }}
    .meta, .small {{ color: var(--muted); font-size: 13px; }}
    .summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 10px;
    }}
    .pill {{
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fff;
      padding: 5px 10px;
      font-size: 13px;
    }}
    button {{
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      padding: 6px 10px;
      font: inherit;
      font-size: 13px;
      cursor: pointer;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 18px;
      align-items: start;
    }}
    .card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
    }}
    .thumb {{
      display: block;
      width: 100%;
      max-height: 520px;
      object-fit: contain;
      background: #eef2f4;
      border-bottom: 1px solid var(--line);
    }}
    .body {{ padding: 14px; }}
    .topline {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 10px;
    }}
    h2 {{
      margin: 0;
      font-size: 17px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .badge {{
      border-radius: 999px;
      background: #e8f3f2;
      color: var(--accent);
      padding: 4px 9px;
      font-size: 12px;
      white-space: nowrap;
    }}
    dl {{
      display: grid;
      grid-template-columns: 110px 1fr;
      gap: 6px 10px;
      margin: 0 0 12px;
      font-size: 13px;
    }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; }}
    .controls {{
      display: grid;
      gap: 8px;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }}
    label {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
    }}
    select, input[type="text"] {{
      width: 100%;
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 8px;
      background: #fff;
      font: inherit;
    }}
    section {{ margin-top: 26px; }}
    .discarded {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px 14px;
    }}
    .discarded div {{
      padding: 7px 0;
      border-bottom: 1px solid #edf0f2;
      font-size: 13px;
    }}
    .discarded div:last-child {{ border-bottom: 0; }}
    .warn {{ color: var(--warn); font-weight: 600; }}
  </style>
</head>
<body>
  <header>
    <h1>HVAC PDF Review Packet</h1>
    <div class="meta">{escape(packet["pdf"])}</div>
    <div class="summary">
      <span class="pill">{len(primary)} primary pages</span>
      <span class="pill">{len(reference)} reference pages</span>
      <span class="pill">{len(discarded)} discarded pages</span>
      <span class="pill">{len(packet.get("thumbnails", []))} thumbnails rendered</span>
      <button id="download">Download reviewed decisions</button>
    </div>
  </header>
  <main>
    <section>
      <div class="grid">
        {cards or '<p class="warn">No kept pages were detected. Review the discarded list or adjust the rules.</p>'}
      </div>
    </section>
    <section>
      <h2>Discarded Pages</h2>
      <p class="small">These were treated as obvious non-HVAC pages. Scan this list for anything that should be recovered.</p>
      <div class="discarded">
        {discarded_rows or '<div>No obvious discard pages found.</div>'}
      </div>
    </section>
  </main>
  <script>
    document.getElementById("download").addEventListener("click", function () {{
      const pages = Array.from(document.querySelectorAll("[data-page]")).map(function (card) {{
        return {{
          page: Number(card.dataset.page),
          detected_type: card.dataset.detectedType,
          decision: card.querySelector("select").value,
          scale_confirmed: card.querySelector('input[type="checkbox"]').checked,
          note: card.querySelector('input[type="text"]').value
        }};
      }});
      const blob = new Blob([JSON.stringify({{
        source_pdf: {json.dumps(packet["pdf"])},
        reviewed_at: new Date().toISOString(),
        pages: pages
      }}, null, 2)], {{ type: "application/json" }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "reviewed_decisions.json";
      link.click();
      URL.revokeObjectURL(url);
    }});
  </script>
</body>
</html>
"""


def render_page_card(page):
    extracted = page.get("extracted", {})
    scale = extracted.get("scale") or "manual confirmation needed"
    dimensions = format_dimensions(extracted.get("written_dimensions", []))
    rooms = format_rooms(extracted.get("rooms", []))
    constraints = ", ".join(extracted.get("ceiling_constraints", [])[:10]) or "none detected"
    hvac_terms = ", ".join(extracted.get("hvac_terms", [])[:10]) or "none detected"
    drawing_number = extracted.get("drawing_number") or "not detected"
    thumb = page.get("thumbnail_path") or ""

    return f"""<article class="card" data-page="{page['page']}" data-detected-type="{escape(page['type'])}">
  <img class="thumb" src="{escape(thumb)}" alt="Page {page['page']} thumbnail">
  <div class="body">
    <div class="topline">
      <h2>Page {page['page']}: {escape(page['title'])}</h2>
      <span class="badge">{escape(page['importance'])}</span>
    </div>
    <dl>
      <dt>Detected as</dt><dd>{escape(page['type'])}</dd>
      <dt>Confidence</dt><dd>{page['confidence']:.2f}</dd>
      <dt>Drawing no.</dt><dd>{escape(drawing_number)}</dd>
      <dt>Scale</dt><dd>{escape(scale)}</dd>
      <dt>Dimensions</dt><dd>{escape(dimensions)}</dd>
      <dt>Rooms</dt><dd>{escape(rooms)}</dd>
      <dt>Constraints</dt><dd>{escape(constraints)}</dd>
      <dt>HVAC terms</dt><dd>{escape(hvac_terms)}</dd>
    </dl>
    <div class="controls">
      <select aria-label="Page decision">
        <option>Confirm as detected</option>
        <option>Confirm as floor plan</option>
        <option>Confirm as RCP</option>
        <option>Keep as reference</option>
        <option>Discard</option>
      </select>
      <label><input type="checkbox"> Scale is correct</label>
      <input type="text" placeholder="Correct scale or note">
    </div>
  </div>
</article>"""


def render_discarded_page(page):
    matched = ", ".join(page.get("matched_words", []))
    return (
        f"<div>Page {page['page']}: {escape(page['title'])} "
        f"<span class=\"small\">{escape(page['type'])}; matched {escape(matched)}</span></div>"
    )


def format_dimensions(dimensions):
    if not dimensions:
        return "none detected"
    return ", ".join(f"{item['value']} {item['unit']}" for item in dimensions[:12])


def format_rooms(rooms):
    if not rooms:
        return "none detected"
    return ", ".join(f"{room['name']} ({room['area']})" for room in rooms[:8])


def escape(value):
    return html.escape(str(value), quote=True)
