"""The contractor's report of a job's final load: what to buy, why, and every value it rests on.

`details(...)` gathers it from the job's saved files (the full hourly report, the input set and the job plan);
`render_html(details)` lays it out as one printable page set: the load, unit sizes to consider (a split per room and
one ducted system for the whole building), the design day by the hour, the peak by month, where the load comes from,
the rooms, the values used room by room with their sources, what was assumed, what isn't included, and the sources.
Charts are inline SVG, so the HTML and its PDF need no scripts or network. Every page carries the label
"Preliminary estimate from the drawings — not an engineer's design".
"""
import html
import json
from math import ceil
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIZES_PATH = ROOT / "config" / "unit_sizes.json"
PACK_PATH = ROOT / "config" / "ai_preliminary_assumption_pack.json"
HOUSE_PATH = ROOT / "config" / "house_profiles.json"
STORED_HEAT_PATH = ROOT / "config" / "stored_heat.json"
TEMPLATE_VERSION = "job-report-v3"
LABEL = "Preliminary estimate from the drawings — not an engineer's design"

# Load groups in the fixed categorical order of the reference palette (dataviz skill, light mode), stacked in that order.
GROUPS = [
    ("air", "Outside air and leakage", ("outside_air", "infiltration", "transfer_air"), "#2a78d6"),
    ("sun", "Sun through glass, walls and roof", ("glazing_solar", "solar", "solar_radiation"), "#eb6834"),
    ("people", "People", ("people",), "#1baf7a"),
    ("lights", "Lights", ("lighting",), "#eda100"),
    ("equipment", "Equipment and appliances", ("equipment_refrigeration", "internal_moisture"), "#e87ba4"),
    ("conduction", "Heat through walls, roof and windows", ("envelope", "glazing_conduction", "dynamic_thermal_mass",
                                                             "dynamic_partition"), "#008300"),
    ("fan", "Fan heat", ("fan_heat",), "#6250d6"),
]
GROUP_OF = {name: key for key, _label, names, _color in GROUPS for name in names}


def _json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def sizes():
    return _json(SIZES_PATH)


def pick(load_kw, steps):
    """(units, size kW): the smallest nominal size at or above the load; above the largest, the fewest equal units."""
    load_kw = max(float(load_kw or 0), 0.0)
    steps = sorted(float(step) for step in steps)
    units = max(1, ceil(load_kw / steps[-1] - 1e-9)) if load_kw > steps[-1] else 1
    share = load_kw / units
    return units, next(step for step in steps if step >= share - 1e-9)


def _label(units, size):
    return f"{units} × {size:g} kW" if units > 1 else f"{size:g} kW"


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ----- gathering -----------------------------------------------------------------------------------------------------

def room_sizing(report, plan_rooms):
    """Each cooled room at its own worst hour of any month, fan heat added at the report's rate, with a wall split."""
    config = sizes()
    fan_percent = _number((report.get("fan_heat") or {}).get("percent_of_room_sensible")) or 0.0
    included = {row["name"] for row in plan_rooms if row.get("final_kw") is not None}
    levels = {row["name"]: row.get("level") or "" for row in plan_rooms}
    areas = {row["name"]: row.get("area_m2") for row in plan_rooms}
    worst = {}
    for scenario in report.get("scenario_results") or []:
        for room in scenario.get("rooms") or []:
            peak = room.get("peak") or {}
            total, sensible = _number(peak.get("design_total_kw")), _number(peak.get("sensible_kw"))
            name = room.get("name", "")
            if total is None or room.get("status") == "blocked" or (included and name not in included):
                continue
            load = total + (sensible or 0.0) * fan_percent / 100
            if name not in worst or load > worst[name]["load_kw"]:
                worst[name] = {"name": name, "level": levels.get(name, ""), "area_m2": areas.get(name),
                               "month": scenario.get("representative_month", ""),
                               "hour": peak.get("display_hour", peak.get("hour")),
                               "load_kw": round(load, 2), "sensible_kw": round((sensible or 0.0) * (1 + fan_percent / 100), 2),
                               "room_kw": round(total, 2)}
    rows = []
    for row in sorted(worst.values(), key=lambda item: (-item["load_kw"], item["name"])):
        units, size = pick(row["load_kw"], config["wall_split_kw"])
        note = config["small_room_note"] if row["load_kw"] < config["small_room_kw"] else ""
        row.update({"units": units, "size_kw": size, "suggested": _label(units, size), "ducted_alternative": ""})
        if units > 1:           # too big for one wall split: a ducted (or cassette) unit is the usual choice
            duct_units, duct_size = pick(row["load_kw"], config["ducted_kw"])
            row["ducted_alternative"] = _label(duct_units, duct_size)
            note = (f"Larger than one wall split: {_label(duct_units, duct_size)} ducted is the usual choice "
                    f"(or {_label(units, size)} wall splits).")
        row["note"] = note
        row["name"] = shown(plan_rooms).get(row["name"], row["name"])
        rows.append(row)
    return rows


def shown(plan_rooms):
    """{drawing label: the name shown} from the plan's rows (a name given on the quick-fix screen, else the label)."""
    return {row["name"]: row.get("display_name") or row["name"] for row in plan_rooms if row.get("name")}


def ducted(total_kw, dwelling=False):
    config = sizes()
    units, size = pick(total_kw, config["ducted_kw"])
    note = ""
    if units > 1:
        note = (f"Larger than the biggest ducted unit in the range ({max(config['ducted_kw']):g} kW): "
                + ("several ducted systems, zoned by area (for example one per floor)." if dwelling
                   else "several ducted units zoned by area, or a commercial packaged unit."))
    return {"load_kw": round(float(total_kw or 0), 2), "units": units, "size_kw": size, "suggested": _label(units, size),
            "note": note}


def design_day(report):
    """The sizing day hour by hour, in load groups; fan heat is the hour's total less its parts."""
    peak = report.get("included_scope_peak") or {}
    scenario = next((row for row in report.get("scenario_results") or [] if row.get("scenario_id") == peak.get("scenario_id")), None)
    if not scenario or len(scenario.get("included_scope_hours") or []) != 24:
        return {}
    month = scenario.get("representative_month", "")
    seasonal = next((row for row in (report.get("seasonal") or {}).get("months") or []
                     if row.get("scenario_id") == scenario.get("scenario_id")), {})
    totals = seasonal.get("hourly_kw") if len(seasonal.get("hourly_kw") or []) == 24 else None
    hours = []
    for index, row in enumerate(scenario["included_scope_hours"]):
        groups = {key: 0.0 for key, *_rest in GROUPS}
        for name, item in (row.get("components") or {}).items():
            groups[GROUP_OF.get(name, "equipment")] += _number((item or {}).get("total_kw")) or 0.0
        parts = sum(groups.values())
        total = _number(totals[index]) if totals else _number(row.get("design_total_kw")) or parts
        groups["fan"] += max(0.0, total - parts)
        hours.append({key: round(max(value, 0.0), 3) for key, value in groups.items()})
    return {"month": month, "hours": hours, "totals": [round(sum(hour.values()), 2) for hour in hours],
            "peak_hour": peak.get("display_hour", peak.get("hour"))}


def breakdown(peak):
    groups = {key: 0.0 for key, *_rest in GROUPS}
    for name, item in (peak.get("components") or {}).items():
        groups[GROUP_OF.get(name, "equipment")] += _number((item or {}).get("total_kw")) or 0.0
    total = sum(groups.values()) or 1.0
    return [{"key": key, "label": label, "color": color, "kw": round(groups[key], 2), "share": groups[key] / total}
            for key, label, _names, color in GROUPS if groups[key] > 0.005]


def _source_head(text):
    """A source as a contractor reads it: the value's name without links or Archie's internal notes."""
    text = str(text or "").split(": http")[0].split(" (http")[0].split(" (")[0].strip()
    if "assumption pack" in text:
        return "Archie's preliminary default (no published source)"
    if text == "controlled_preliminary_profile":
        return "Typical W/m² allowance for the room's use (no published source)"
    if text.casefold().startswith("typical home value"):
        detail = text.split(":", 1)[1].strip() if ":" in text else ""
        return "Typical home value (no published source)" + (f": {detail}" if detail else "")
    return text


ITEM_NAMES = {"Preliminary profile equipment": "Typical equipment allowance"}


def room_values(input_set, plan_rooms):
    """Each cooled room's people, lights, outside air and equipment as the calculation used them."""
    uses = {row["name"]: (row.get("use") or {}).get("taxonomy_label") or "" for row in plan_rooms}
    included = {row["name"] for row in plan_rooms if row.get("final_kw") is not None}
    names = shown(plan_rooms)
    rows, equipment = [], []
    for room in ((input_set.get("material") or {}).get("hourly_load_model") or {}).get("rooms") or []:
        label = room.get("name", "")
        if included and label not in included:
            continue
        name = names.get(label, label)
        load = room.get("cooling_load") or {}
        rows.append({"name": name, "use": uses.get(label, ""), "area_m2": room.get("area_m2"), "people": room.get("occupancy"),
                     "people_w": (load.get("people_sensible_w_per_person"), load.get("people_latent_w_per_person")),
                     "lighting_w_m2": load.get("lighting_w_m2"), "outside_air_lps": load.get("outside_air_lps")})
        for source in room.get("heat_sources") or []:
            watts, quantity = _number(source.get("watts")) or 0.0, _number(source.get("quantity")) or 0.0
            counted = watts * quantity * (_number(source.get("diversity_factor")) or 1.0) * (_number(source.get("space_gain_factor")) or 1.0)
            if counted <= 0:
                continue
            item = str(source.get("name", ""))
            equipment.append({"room": name, "name": ITEM_NAMES.get(item, item), "quantity": quantity, "watts": watts,
                              "counted_w": round(counted), "source": _source_head(source.get("source"))})
    return rows, equipment


def envelope_values(input_set):
    """The distinct wall, roof, floor and glass values used, with how many surfaces use each and their source."""
    found = {}
    for room in ((input_set.get("material") or {}).get("hourly_load_model") or {}).get("rooms") or []:
        load = room.get("cooling_load") or {}
        for surface in load.get("envelope_surfaces") or []:
            if surface.get("boundary_method") != "external":
                continue
            key = ("surface", surface.get("kind"), surface.get("u_value_w_m2k"), _source_head(surface.get("source")))
            found.setdefault(key, 0)
            found[key] += 1
        for glass in load.get("glazing_surfaces") or []:
            window = glass.get("window") or {}
            key = ("glass", "window", (window.get("u_value_w_m2k"), window.get("shgc") or window.get("solar_transmission_factor")),
                   _source_head(glass.get("glass_source") or window.get("source") or glass.get("source")))
            found.setdefault(key, 0)
            found[key] += 1
    names = {"opaque_wall": "Outside wall", "roof": "Roof", "ceiling": "Ceiling", "floor": "Floor", "window": "Window glass"}
    rows = []
    for (kind, surface, value, source), count in sorted(found.items(), key=lambda item: (item[0][0], str(item[0][1]))):
        text = (f"U {value[0]:g} W/m²K, SHGC {value[1]:g}" if kind == "glass" and None not in value
                else f"U {value:g} W/m²K" if isinstance(value, (int, float)) else str(value))
        rows.append({"what": names.get(surface, str(surface)), "value": text, "count": count, "source": source})
    return rows


SURFACE_WORDS = {"floor": "Floors", "ceiling": "Ceilings", "roof": "Roofs", "wall": "Walls"}


def _surface_rule(reason):
    """A per-surface boundary note as a rule shared by many surfaces: what it is next to, then what follows."""
    text = re.sub(r"\s*\(Phase 1 rule[^)]*\)", "", reason.split(" Matched to the ")[0]).strip()
    if ": " not in text:
        return text
    context, rule = text.split(": ", 1)
    if "(not air-conditioned" in context:
        return f"next to a space that isn't air-conditioned: {rule}"
    if "air-conditioned to the same temperature" in context:
        return f"next to an air-conditioned room at the same temperature: {rule}"
    if context.startswith("Over no traced room"):
        return f"over an open or untraced space: {rule}"
    return rule


def _stored_heat_line(rooms):
    defaults, coverings, count = set(), {}, 0
    for room in rooms:
        record = room.get("stored_heat") or {}
        if record.get("status") != "calculated":
            continue
        count += 1
        defaults.update(row["label"] for row in record.get("constructions", []) if row.get("chosen") == "default")
        coverings[record.get("floor_covering", "")] = coverings.get(record.get("floor_covering", ""), 0) + 1
    if not count:
        return None
    finishes = ", ".join(f"{'carpet' if name == 'carpet' else 'tiles'} in {number} room{'s' if number > 1 else ''}"
                         for name, number in sorted(coverings.items()) if name)
    return {"component": "stored heat", "rooms": [],
            "reason": ("ASHRAE's radiant time series method, with each room's factors from Archie's own heat balance "
                       "(not ASHRAE's published tables)"
                       + (f"; constructions not shown on the drawings taken as: {'; '.join(sorted(defaults))}" if defaults else "")
                       + (f"; floor finish {finishes}" if finishes else "") + ".")}


def assumed(input_set):
    """This job's assumptions (the input set's list: the full report keeps it only for the envelope-only run), one line
    per distinct statement with the rooms it applies to. Notes about one room's floor, ceiling, roof or wall are grouped
    by surface and rule, and stored heat is one line for the job, so a large house stays readable."""
    rooms = ((input_set.get("material") or {}).get("hourly_load_model") or {}).get("rooms") or []
    names = {room.get("room_id"): room.get("name", "") for room in rooms}
    grouped = {}
    for item in input_set.get("phase1_assumptions") or []:
        if not isinstance(item, dict) or not item.get("reason") or item.get("component") == "stored heat":
            continue
        component, reason = str(item.get("component") or "").strip(), str(item["reason"]).strip()
        room = names.get(item.get("room_id"), "")
        own = room and component.casefold().startswith(room.casefold() + " ")
        rest = component[len(room):].strip().split(" ")[0].casefold() if own else ""
        key = (SURFACE_WORDS[rest], _surface_rule(reason)) if rest in SURFACE_WORDS else (component, reason)
        grouped.setdefault(key, set())
        if room:
            grouped[key].add(room)
    rows = [{"component": component, "reason": reason, "rooms": sorted(members)}
            for (component, reason), members in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1]))]
    line = _stored_heat_line(rooms)
    return ([line] if line else []) + rows


def sources(report, dwelling):
    """The published sources the job's values rest on, in the order the report uses them."""
    pack, house, stored = _json(PACK_PATH), _json(HOUSE_PATH), _json(STORED_HEAT_PATH)
    basis = (report.get("design_conditions_basis") or {}).get("design_day") or {}
    fan = report.get("fan_heat") or {}
    config = sizes()
    rows = []
    if basis.get("source"):
        rows.append(("Design weather", basis["source"]))
    values = pack.get("value_sources") or {}
    house_values = house.get("value_sources") or {}
    if dwelling and house_values.get("lighting_w_m2"):
        rows.append(("House lighting", house_values["lighting_w_m2"]))
    if values.get("lighting_w_m2"):
        rows.append(("Lighting", values["lighting_w_m2"]))
    if values.get("occupancy_density_people_m2"):
        rows.append(("People per m²", values["occupancy_density_people_m2"]))
    if values.get("people_activity"):
        rows.append(("Heat from people", values["people_activity"]))
    if values.get("outside_air"):
        rows.append(("Outside air", values["outside_air"]))
    if dwelling:
        for key, label in (("infiltration_ach", "House air leakage"), ("cooled_hours", "House cooled hours")):
            if house_values.get(key):
                rows.append((label, house_values[key]))
    if fan.get("source"):
        rows.append(("Fan heat", f"{fan['source']}: {fan.get('basis', '')}".strip(": ")))
    if stored.get("basis"):
        rows.append(("Stored heat", stored["basis"]))
    rows.append(("Split-system sizes", config.get("wall_split_source", "")))
    rows.append(("Ducted sizes", config.get("ducted_source", "")))
    rows.append(("Unit ratings", config.get("rating_note", "")))
    # The value notes are written for Archie's own record ("unchanged" since the last decision); not for the reader.
    tidy = lambda text: str(text).replace(", unchanged:", ":").replace(", unchanged.", ".").replace(", unchanged", "")
    return [{"topic": topic, "text": tidy(text)} for topic, text in rows if text]


# ----- charts ----------------------------------------------------------------------------------------------------------

def _e(value):
    return html.escape(str(value))


def _nice_top(value):
    """(axis top, gridline step): the smallest tidy step that covers the value in at most six gridlines."""
    for step in (0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        ticks = max(1, ceil(value / step - 1e-9))
        if ticks <= 6:
            return ticks * step, step
    return value, value / 5


def design_day_svg(day):
    """Stacked hourly columns (load groups in the palette's order), y in kW from zero, with the sizing hour marked."""
    if not day:
        return ""
    width, height, left, right, top, bottom = 720, 300, 48, 12, 14, 34
    plot_w, plot_h = width - left - right, height - top - bottom
    peak_total = max(day["totals"]) or 1.0
    y_max, step = _nice_top(peak_total * 1.12)       # headroom for the sizing hour's label
    bar_w = plot_w / 24
    y = lambda kw: top + plot_h - kw / y_max * plot_h
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Cooling load by hour on the {_e(day["month"])} design day, stacked by where it comes from" class="chart">']
    tick = 0.0
    while tick <= y_max + 1e-9:
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" class="grid"/>'
                     f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" class="axis" text-anchor="end">{tick:g}</text>')
        tick += step
    parts.append(f'<text x="12" y="{top + plot_h / 2:.0f}" class="axis" transform="rotate(-90 12 {top + plot_h / 2:.0f})" text-anchor="middle">kW</text>')
    for hour, groups in enumerate(day["hours"]):
        x = left + hour * bar_w + 2
        base = 0.0
        for key, label, _names, color in GROUPS:
            value = groups.get(key, 0.0)
            if value <= 0:
                continue
            y0, y1 = y(base), y(base + value)
            h = max(y0 - y1 - 1, 0.5)          # a 1px surface gap between stacked segments
            parts.append(f'<rect x="{x:.1f}" y="{y1:.1f}" width="{bar_w - 4:.1f}" height="{h:.1f}" fill="{color}">'
                         f'<title>{hour:02d}:00 {_e(label)}: {value:.1f} kW</title></rect>')
            base += value
        if hour % 3 == 0:
            parts.append(f'<text x="{x + (bar_w - 4) / 2:.1f}" y="{height - bottom + 16}" class="axis" text-anchor="middle">{hour:02d}</text>')
    if isinstance(day.get("peak_hour"), int):
        hour = day["peak_hour"]
        x = left + hour * bar_w + bar_w / 2
        total = day["totals"][hour]
        # Above every bar (the neighbouring hours are often nearly as high), with a tick down to the sizing hour.
        label_y = top + 10
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{label_y + 3:.1f}" y2="{y(total) - 2:.1f}" stroke="#17242b" stroke-width="1"/>'
                     f'<text x="{x:.1f}" y="{label_y:.1f}" class="label" text-anchor="middle">{total:.1f} kW at {hour:02d}:00</text>')
    parts.append(f'<text x="{left + plot_w / 2:.0f}" y="{height - 4}" class="axis" text-anchor="middle">Hour of the day (local clock time)</text></svg>')
    return "".join(parts)


def months_svg(months, design_month):
    """Each month's peak (21st, hour by hour) as a column; the sizing month darker and labelled."""
    if not months:
        return ""
    width, height, left, right, top, bottom = 720, 220, 48, 12, 18, 30
    plot_w, plot_h = width - left - right, height - top - bottom
    y_max, step = _nice_top(max(row["kw"] for row in months) or 1.0)
    y = lambda kw: top + plot_h - kw / y_max * plot_h
    bar_w = plot_w / len(months)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Peak cooling load by month" class="chart">']
    tick = 0.0
    while tick <= y_max + 1e-9:
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" class="grid"/>'
                     f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" class="axis" text-anchor="end">{tick:g}</text>')
        tick += step
    for index, row in enumerate(months):
        x = left + index * bar_w + bar_w * 0.2
        design = row["month"] == design_month
        color = "#2a78d6" if design else "#a9c9ec"
        parts.append(f'<rect x="{x:.1f}" y="{y(row["kw"]):.1f}" width="{bar_w * 0.6:.1f}" height="{max(y(0) - y(row["kw"]), 0.5):.1f}" '
                     f'rx="2" fill="{color}"><title>{_e(row["month"])}: {row["kw"]:.1f} kW at {row["hour"]:02d}:00</title></rect>'
                     f'<text x="{x + bar_w * 0.3:.1f}" y="{height - bottom + 16}" class="axis" text-anchor="middle">{_e(row["month"][:3])}</text>')
        if design:
            parts.append(f'<text x="{x + bar_w * 0.3:.1f}" y="{y(row["kw"]) - 5:.1f}" class="label" text-anchor="middle">{row["kw"]:.1f} kW</text>')
    parts.append("</svg>")
    return "".join(parts)


def breakdown_svg(rows):
    """Horizontal bars, one per load group, kW and share labelled."""
    if not rows:
        return ""
    width, row_h, left, right = 720, 26, 250, 110
    height = row_h * len(rows) + 8
    top_kw = max(row["kw"] for row in rows) or 1.0
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Where the load comes from at the sizing hour" class="chart">']
    for index, row in enumerate(sorted(rows, key=lambda item: -item["kw"])):
        y0 = 4 + index * row_h
        bar = (width - left - right) * row["kw"] / top_kw
        parts.append(f'<text x="{left - 10}" y="{y0 + 15}" class="axis dark" text-anchor="end">{_e(row["label"])}</text>'
                     f'<rect x="{left}" y="{y0 + 4}" width="{max(bar, 1):.1f}" height="14" rx="2" fill="{row["color"]}">'
                     f'<title>{_e(row["label"])}: {row["kw"]:.1f} kW</title></rect>'
                     f'<text x="{left + bar + 8:.1f}" y="{y0 + 15}" class="label">{row["kw"]:.1f} kW · {round(row["share"] * 100)}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def legend():
    return "".join(f'<span class="key"><i style="background:{color}"></i>{_e(label)}</span>' for _key, label, _n, color in GROUPS)


# ----- the page ------------------------------------------------------------------------------------------------------

def _kw(value, digits=1):
    value = _number(value)
    return "" if value is None else f"{value:.{digits}f}"


def render_html(d):
    e = _e
    config = sizes()
    stale = "".join(f"<p class=warn>{e(reason)}</p>" for reason in d.get("stale_reasons", []))
    shr = (d["sensible_kw"] / d["total_kw"]) if d.get("total_kw") else None
    duct = d.get("ducted") or {}
    split_rows = "".join(
        f"<tr><td>{e(row['name'])}</td><td>{e(row['level'])}</td><td>{e(row['month'][:3])} {e(row['hour'])}:00</td>"
        f"<td class=n>{_kw(row['load_kw'])}</td><td class=n>{_kw(row['sensible_kw'])}</td><td class=n><b>{e(row['suggested'])}</b></td>"
        f"<td class=muted>{e(row['note'])}</td></tr>" for row in d.get("room_sizing", []))
    components = "".join(f"<tr><td><i class=swatch style='background:{row['color']}'></i>{e(row['label'])}</td><td class=n>{_kw(row['kw'])}</td>"
                         f"<td class=n>{round(row['share'] * 100)}%</td></tr>" for row in d.get("breakdown", []))
    rooms = "".join(f"<tr><td>{e(row['name'])}</td><td>{e(row['level'])}</td><td class=n>{_kw(row['area_m2'])}</td>"
                    f"<td class=n>{_kw(row['kw'])}</td><td class=n>{'' if not row.get('w_m2') else round(row['w_m2'])}</td></tr>"
                    for row in d["rooms"])
    months = "".join(f"<tr><td>{e(row['month'])}</td><td class=n>{_kw(row['kw'])}</td><td class=n>{row['hour']:02d}:00</td></tr>"
                     for row in d["months"])
    values = "".join(
        f"<tr><td>{e(row['name'])}</td><td>{e(row['use'])}</td><td class=n>{_kw(row['area_m2'])}</td><td class=n>{_kw(row['people'], 0)}</td>"
        f"<td class=n>{_kw(row['people_w'][0], 0)} / {_kw(row['people_w'][1], 0)}</td><td class=n>{_kw(row['lighting_w_m2'])}</td>"
        f"<td class=n>{_kw(row['outside_air_lps'])}</td></tr>" for row in d.get("room_values", []))
    equipment = "".join(f"<tr><td>{e(row['room'])}</td><td>{e(row['name'])}</td><td class=n>{row['quantity']:g}</td>"
                        f"<td class=n>{row['watts']:.0f}</td><td class=n>{row['counted_w']}</td><td class=muted>{e(row['source'])}</td></tr>"
                        for row in d.get("equipment", []))
    envelope = "".join(f"<tr><td>{e(row['what'])}</td><td>{e(row['value'])}</td><td class=n>{row['count']}</td>"
                       f"<td class=muted>{e(row['source'])}</td></tr>" for row in d.get("envelope", []))
    def where(rooms):
        if not rooms:
            return ""
        text = ", ".join(rooms) if len(rooms) <= 6 else f"{len(rooms)} rooms"
        return f" <span class=muted>({e(text)})</span>"

    assumed_rows = "".join(f"<li><b>{e(row['component'].capitalize())}:</b> {e(row['reason'])}{where(row['rooms'])}</li>"
                           for row in d.get("assumed", []))
    to_confirm = "".join(f"<li>{e(text)}{f' <span class=muted>({count} rooms)</span>' if count > 1 else ''}</li>"
                         for text, count in d.get("assumptions", []))
    excluded = "".join(f"<li>{e(text)}</li>" for text in d["exclusions"])
    source_rows = "".join(f"<li><b>{e(row['topic'])}:</b> {e(row['text'])}</li>" for row in d.get("sources", []))
    not_cooled = ", ".join(e(name) for name in d["not_cooled"]) or "None"
    day = d.get("design_day") or {}
    return f"""<!doctype html><html lang="en" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Cooling load: {e(d['name'])}</title>
<style>
:root{{color-scheme:light;--ink:#17242b;--muted:#5b6b73;--line:#d6dde0;--wash:#f5f7f8;--blue:#2a78d6}}
body{{font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;max-width:900px;margin:24px auto 64px;padding:0 16px;color:var(--ink);background:#fff}}
h1{{font-size:22px;margin:0}}h2{{font-size:16px;margin:26px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px;break-after:avoid}}
h3{{font-size:14px;margin:16px 0 6px}}.muted{{color:var(--muted)}}.warn{{background:#fff4e0;padding:8px 10px;border-radius:6px}}
.banner{{border:1px solid var(--ink);border-radius:6px;padding:8px 12px;margin:10px 0;font-weight:600}}
.total{{font-size:40px;font-weight:700;margin:12px 0 2px;font-variant-numeric:tabular-nums}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:12px 0}}
.card{{border:1px solid var(--line);border-radius:10px;padding:10px 12px}}.card b{{display:block;font-size:22px;font-variant-numeric:tabular-nums}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}td,th{{padding:4px 8px;border-bottom:1px solid #eef1f2;text-align:left;vertical-align:top}}
th{{font-weight:600;background:var(--wash)}}td.n,th.n{{text-align:right;white-space:nowrap}}ul{{padding-left:18px}}li{{margin:3px 0}}
.chart{{width:100%;height:auto;display:block;margin:6px 0}}.chart .grid{{stroke:#e6eaec;stroke-width:1}}
.chart .axis{{font-size:11px;fill:var(--muted)}}.chart .axis.dark{{fill:var(--ink)}}.chart .label{{font-size:11px;fill:var(--ink);font-weight:600}}
.keys{{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px}}.key i,.swatch{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}}
.footer{{position:fixed;bottom:0;left:0;right:0;font-size:10px;color:var(--muted);text-align:center;padding:4px;background:#fff}}
svg,tr,.card{{break-inside:avoid}}
@page{{size:A4;margin:14mm 12mm 16mm}}@media print{{body{{margin:0 auto;max-width:none}}.pagebreak{{break-before:page}}}}
@media (max-width:600px){{.total{{font-size:32px}}td,th{{padding:4px}}}}
</style></head><body>
<div class=footer>{e(LABEL)} · {e(d['name'])} · generated {e(d['generated_at'])}</div>
<h1>Cooling load: {e(d['name'])}</h1><div class=muted>{e(d['address'])}</div>
<div class=banner>{e(LABEL)}. Use it to plan and quote; have the design checked before equipment is ordered for a job that needs an engineer's sign-off.</div>
{stale}
<div class=total>{_kw(d['total_kw'])} kW</div>
<div>Design cooling load for the whole building at {e(d['peak_hour'])}:00 on the {e(d['peak_month'])} design day. {e(d['rooms_counted'])} rooms counted. {e(d['household_note'])}</div>
<div class=cards>
<div class=card>Sensible<b>{_kw(d['sensible_kw'])} kW</b><span class=muted>raises the air temperature</span></div>
<div class=card>Latent<b>{_kw(d['latent_kw'])} kW</b><span class=muted>moisture from people and outside air</span></div>
<div class=card>Sensible heat ratio<b>{'' if shr is None else f'{shr:.2f}'}</b><span class=muted>check the unit's sensible capacity</span></div>
</div>

<h2>Units to consider</h2>
<h3>One ducted system for the whole building</h3>
<p>The building's load at its busiest hour is <b>{_kw(duct.get('load_kw'))} kW</b>: nominal size <b>{e(duct.get('suggested', ''))}</b>.
Sized on the whole building at one hour, because rooms peak at different times. {e(duct.get('note', ''))}</p>
<h3>Or a split system in each room</h3>
<p class=muted>Each room at its own worst hour of the year, with fan heat. A room's load is larger than its share of the whole
building's, because rooms don't all peak together; split systems in every room add up to more than one ducted system.</p>
<table><tr><th>Room</th><th>Level</th><th>Worst hour</th><th class=n>Load kW</th><th class=n>Sensible kW</th><th class=n>Nominal size</th><th></th></tr>
{split_rows or '<tr><td colspan=7>No room loads.</td></tr>'}</table>
<p class=muted>{e(config.get('rule', ''))} {e(config.get('rating_note', ''))} Size steps: wall splits {', '.join(f'{v:g}' for v in config.get('wall_split_kw', []))} kW;
ducted {', '.join(f'{v:g}' for v in config.get('ducted_kw', []))} kW (one manufacturer's range; see Sources).</p>

<h2>The {e(day.get('month') or d['peak_month'])} design day, hour by hour</h2>
<div class=keys>{legend()}</div>
{design_day_svg(day)}
<p class=muted>The whole building's cooling load in each hour of the day that sizes the system, stacked by where it comes from.
Stored heat is included: sun, lights and people warm the floor and walls and reach the air over the following hours.</p>

<h2>Peak by month</h2>
{months_svg(d['months'], d['peak_month'])}
<table><tr><th>Month</th><th class=n>Peak kW</th><th class=n>Hour</th></tr>{months}</table>
<p class=muted>The 21st of each month, hour by hour, with that day's sun and the month's design temperature.</p>

<h2>Where the load comes from at the sizing hour</h2>
{breakdown_svg(d.get('breakdown', []))}
<table><tr><th>Part</th><th class=n>kW</th><th class=n>Share</th></tr>{components}</table>

<h2>Rooms</h2><p class=muted>Each room's load on the design day that sizes the system.</p>
<table><tr><th>Room</th><th>Level</th><th class=n>m²</th><th class=n>kW</th><th class=n>W/m²</th></tr>{rooms}</table>
<p class=muted>Not cooled: {not_cooled}.</p>

<h2>Values used, room by room</h2>
<table><tr><th>Room</th><th>Use</th><th class=n>m²</th><th class=n>People</th><th class=n>W/person sens. / lat.</th><th class=n>Lights W/m²</th><th class=n>Outside air L/s</th></tr>
{values or '<tr><td colspan=7>None.</td></tr>'}</table>
<h3>Equipment</h3>
<table><tr><th>Room</th><th>Item</th><th class=n>Qty</th><th class=n>W each</th><th class=n>W counted</th><th>Value from</th></tr>
{equipment or '<tr><td colspan=6>No equipment counted.</td></tr>'}</table>
<p class=muted>W counted = quantity × W × how much of the time it runs × the share of its heat that stays in the room.
"Typical value" is a typical rating for that kind and size of item from Archie's equipment value list; "generic value" is
for the kind of item only, and the weakest. A manufacturer's data sheet for the installed model replaces either.</p>
<h3>Walls, roof and glass to outside</h3>
<table><tr><th>What</th><th>Value</th><th class=n>Surfaces</th><th>Source</th></tr>{envelope or '<tr><td colspan=4>None.</td></tr>'}</table>
<p class=muted>Design conditions: {e(d['design_basis'])}</p>

<h2>Still to confirm</h2><ul>{to_confirm or '<li>None reported.</li>'}</ul>
<h2>Assumed for this job</h2><ul>{assumed_rows or '<li>None reported.</li>'}</ul>
<h2>Not included</h2><ul>{excluded or '<li>None reported.</li>'}</ul>
<h2>Sources</h2><ul>{source_rows}</ul>
</body></html>"""
