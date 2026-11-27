"""Build Turramurra's corrected outlines on the turra_fix copy from the best AI outline per room (runs 4, b, c),
plus Kitchen and Dining drawn from neighbouring walls. Writes only the copy's reviewer_room_geometry.json."""
import copy, json, sys
from pathlib import Path
sys.path.insert(0, ".")
from shapely.geometry import Polygon
from ai import reviewer_room_geometry
from ai.room_use_resolution import room_identity
from backend import reviewer_room_geometry_service as g

S = Path(sys.argv[1])
FIX = S / "turra_fix"
runs = {name: {(r["page"], r["room_label"]): r for r in json.loads((S / f"turra_{name}" / "reviewer_room_geometry.json").read_text())["records"]}
        for name in ("live4", "rb", "rc")}
MM = 14.109
NOTE = "Picked from AI runs and checked against the plan by Claude, 26 Nov 2026 (retrace copy, not yet engineer-approved)."
picks = [  # (page, source run, source label, final label)
    (8, "rc", "Bedroom", "Bedroom 5"), (8, "rc", "Ensuite", "Ensuite"), (8, "live4", "Wir", "Wir"),
    (8, "live4", "Toilet", "Toilet"), (8, "rb", "Linen", "Linen"), (8, "live4", "Study", "Study"),
    (8, "rc", "Entry", "Entry"), (8, "live4", "Media", "Media"), (8, "live4", "Living", "Living"),
    (8, "live4", "Sitting", "Sitting"), (8, "rc", "Scullery", "Scullery"), (8, "rc", "Laundry", "Laundry"),
    (9, "rc", "Bedroom 2", "Bedroom 2"), (9, "rc", "Wir", "Wir"), (9, "rc", "Ensuite (2)", "Ensuite (2)"),
    (9, "rc", "Bathroom", "Bathroom"), (9, "rc", "Bedroom 3", "Bedroom 3"), (9, "rc", "Ensuite (1)", "Ensuite (1)"),
    (9, "live4", "Bedroom 4", "Bedroom 4"), (9, "rc", "Ensuite (3)", "Ensuite (3)"), (9, "rc", "Walk in robe", "Walk in robe"),
    (9, "rc", "Storage (1)", "Storage (1)"), (9, "rc", "Storage (2)", "Storage (2)"), (9, "rc", "Study nook", "Study nook"),
    (9, "rc", "Master Bedroom", "Master Bedroom"), (9, "rc", "Sitting", "Sitting"), (9, "rc", "Corridor", "Corridor"),
    (9, "rc", "Balcony", "Balcony"),
]
drawn = {"Kitchen": [[1826, 823], [2380, 823], [2380, 1006], [1826, 1006]],
         "Dining": [[1782, 1006], [2380, 1006], [2380, 1371], [1782, 1371]]}

def finish(record, label, points=None):
    record = copy.deepcopy(record)
    if points is not None:
        record["points_image_px"] = [*points, points[0]]
        record["snapped_line_ids"] = [None] * len(record["points_image_px"])
        record["edges"] = [{"index": i, "boundary": "unknown"} for i in range(len(points))]
    record["level_name"] = {8: "Ground Floor", 9: "First Floor", 25: "Basement"}[record["page"]]  # some AI rows took another floor's name
    record["room_label"], record["room_id"] = label, room_identity(label, record["level_name"])
    record["trace_id"] = "room_trace_fix_" + f"{record['page']}_{label}".lower().replace(" ", "_").replace("(", "").replace(")", "")
    record["part_count"], record["part_index"] = 1, 0
    record["ai_measured_area_m2"] = round(Polygon(record["points_image_px"]).area * MM * MM / 1e6, 2)
    record["reviewer"], record["note"] = "Claude (retrace review)", NOTE
    return record

records = [finish(runs[src][(page, label)], final) for page, src, label, final in picks]
template = runs["rc"][(8, "Laundry")]
records += [finish(template, label, points) for label, points in drawn.items()]
for p in (8, 9):  # cellar page 25 unchanged
    pass
records += [finish(r, r["room_label"]) for (page, _), r in runs["rc"].items() if page == 25]
for a in records:
    for b in records:
        if a is not b and a["page"] == b["page"]:
            o = Polygon(a["points_image_px"]).buffer(0).intersection(Polygon(b["points_image_px"]).buffer(0)).area * MM * MM / 1e6
            if o > 0.5 and a["room_label"] < b["room_label"]:
                print(f"overlap p{a['page']} {a['room_label']} / {b['room_label']}: {o:.2f} m2")
artifact = reviewer_room_geometry.validate_artifact({"records": records})
g._atomic_json(FIX / "reviewer_room_geometry.json", artifact)
for r in sorted(records, key=lambda r: (r["page"], r["room_label"])):
    print(f"p{r['page']} {r['room_label']:<15} {r['ai_measured_area_m2']:6.2f} m2")
print(len(records), "outlines written")
