"""Stored heat for the draft's rooms: each room's own radiant time series and each outside wall's and roof's conduction
time series (ai/stored_heat.py), and the hourly step that turns heat gains into cooling load with them.

`room_record` builds the room's heat-balance model from its traced surfaces (areas, what each faces) and a typical
construction for each (config/stored_heat.json: named on the drawings when the surface's label or construction says
so, otherwise the default listed there), and stores the 24-hour series on the room. `apply` then takes a room's 24
hours of heat gains (the same contributions the hourly calculation has always produced) and returns them as cooling
load: conduction through outside walls and roofs spread over the day by the construction's CTS, then every gain split
into the part that reaches the air in its own hour and the radiant part, which the room's RTS releases over the
following hours (ASHRAE Fundamentals 2009 Ch 18 Radiant Time Series method). Each contribution keeps its heat gain
beside its cooling load; a day's cooling load adds up to the day's heat gains.
"""
import json
from copy import deepcopy
from math import sqrt

from ai import stored_heat

SERIES_DECIMALS = 6
FURNISHED_FRACTION = 0.5
_CACHE = {}


def config():
    return stored_heat.load()


def _layers(cfg, spec, fit_r=None):
    """Concrete layers (inside face first) from config rows; the "fit" insulation takes resistance fit_r (dropped when
    fit_r is None or not positive)."""
    out = []
    for row in spec:
        name = row[0]
        if name in cfg["resistances"]:
            out.append({"r_m2k_w": cfg["resistances"][name]["r_m2k_w"], "name": name})
            continue
        material = cfg["materials"][name]
        if len(row) > 1 and row[1] == "fit":
            if fit_r is None or fit_r <= 1e-6:
                continue
            thickness = fit_r * material["k_w_mk"]
        else:
            thickness = float(row[1])
        out.append({"name": name, "thickness_m": round(thickness, 5), "k_w_mk": material["k_w_mk"],
                    "density_kg_m3": material["density_kg_m3"], "specific_heat_j_kgk": material["specific_heat_j_kgk"]})
    return out


def _fitted(cfg, spec, u_value, inside_film):
    """Layers with the insulation sized so the construction (inside film included) has the surface's U-value."""
    has_fit = any(len(row) > 1 and row[1] == "fit" for row in spec)
    base = _layers(cfg, spec)
    if not has_fit or not u_value or u_value <= 0:
        return base, None
    fit_r = 1.0 / float(u_value) - inside_film - stored_heat.resistance(base)
    return (_layers(cfg, spec, fit_r), round(fit_r, 4)) if fit_r > 1e-6 else (base, None)


def _text(surface):
    parts = [surface.get("label", ""), surface.get("construction_source", "")]
    if not str(surface.get("construction_id", "")).startswith("au-preliminary"):
        parts.append(str(surface.get("construction_id", "")))
    return " ".join(str(part) for part in parts).casefold()


def _by_words(cfg, group, text, default):
    for key, words in cfg["construction_words"].get(group, []):
        if any(word in text for word in words):
            return key, "named on the drawings"
    return default, "default"


def _choose(cfg, surface, dwelling):
    """(construction key, side, far-face film key or None, how chosen) for one opaque surface."""
    kind, boundary, method = surface.get("kind"), surface.get("boundary_condition", ""), surface.get("boundary_method")
    text, defaults = _text(surface), cfg["defaults"]
    if kind == "opaque_wall":
        if method == "external":
            key, how = _by_words(cfg, "wall", text, defaults["wall"])
            return key, "outdoor", None, how
        key, how = _by_words(cfg, "partition", text, defaults["partition"])
        if boundary == "unconditioned_space":
            return key, "fixed", "inside_film_vertical", how
        return key, "mirror", None, how
    if kind == "roof" or (kind == "ceiling" and method == "external"):
        key, how = _by_words(cfg, "roof", text, defaults["roof"])
        return key, "outdoor", None, how
    if kind == "ceiling":
        if boundary == "unconditioned_space":
            return "ceiling_to_roof_space", "fixed", None, "default"
        return defaults["ceiling_dwelling" if dwelling else "ceiling_commercial"], "mirror", None, "default"
    if kind == "floor":
        upper = defaults["upper_floor_dwelling" if dwelling else "upper_floor_commercial"]
        if boundary == "ground":
            key, how = _by_words(cfg, "floor", text, defaults["ground_floor"])
            return key, None, None, how
        if boundary == "unconditioned_below":
            return upper, "fixed", "inside_film_down", "default"
        return upper, "mirror", None, "default"
    return None, None, None, ""


def _inside_film(cfg, kind):
    films = cfg["films"]
    if kind == "floor":
        return films["inside_heat_flow_up_m2k_w"]
    if kind in {"roof", "ceiling"}:
        return films["inside_heat_flow_down_m2k_w"]
    return films["inside_vertical_m2k_w"]


def _covering(cfg, profile_id):
    coverings = cfg["floor_coverings"]
    name = "carpet" if profile_id in coverings["carpet_profiles"] else "hard"
    return name, coverings[name]["layers"]


def room_record(room, dwelling=False, profile_id=""):
    """The room's stored-heat record: its RTS (nonsolar and solar), each outside wall's and roof's CTS, the
    constructions used and how each was chosen. A room with no surfaces at all gets status "not_calculated"."""
    cfg = config()
    load = room.get("cooling_load") or {}
    area = float(room.get("area_m2") or 0)
    height = float(room.get("ceiling_height_mm") or 2700) / 1000.0
    groups, chosen, cts, notes = {}, [], {}, []
    covering_name, covering = _covering(cfg, profile_id)

    def add(key, surface_ids, area_m2, side, layers, film, floor=False, extra=None):
        if area_m2 <= 0:
            return
        group = json.dumps([key, side, layers, film, floor, extra], sort_keys=True)
        item = groups.setdefault(group, {"area_m2": 0.0, "film_r": film, "side": side, "layers": layers,
                                         "floor": floor, **(extra or {})})
        item["area_m2"] += area_m2
        if surface_ids is not None:
            chosen.append({"surface_ids": surface_ids, "construction": key, "area_m2": round(area_m2, 3)})

    kinds = set()
    for surface in load.get("envelope_surfaces", []):
        key, side, far_film, how = _choose(cfg, surface, dwelling)
        surface_area = float(surface.get("area_m2") or 0)
        if not key or surface_area <= 0:
            continue
        kinds.add(surface.get("kind"))
        spec = list(cfg["constructions"][key]["layers"])
        side = side or cfg["constructions"][key]["side"]
        if surface.get("kind") == "floor":
            spec = covering + spec
        if far_film:
            spec = spec + [[far_film]]
        film = _inside_film(cfg, surface.get("kind"))
        layers, fit_r = _fitted(cfg, spec, surface.get("u_value_w_m2k"), film)
        add(key, [surface.get("surface_id")], surface_area, side, layers, film, floor=surface.get("kind") == "floor")
        chosen[-1].update({"label": cfg["constructions"][key]["label"], "chosen": how,
                           "fitted_insulation_r_m2k_w": fit_r})
        if surface.get("kind") == "floor":
            chosen[-1]["floor_covering"] = covering_name
        if side == "outdoor":
            prf_layers = list(reversed(layers)) + [{"r_m2k_w": film}]
            cache_key = json.dumps(prf_layers, sort_keys=True)
            if cache_key not in _CACHE:
                _CACHE[cache_key] = [round(v, SERIES_DECIMALS) for v in stored_heat.conduction_time_series(prf_layers)]
            cts[surface["surface_id"]] = _CACHE[cache_key]
    window_area = 0.0
    for glazing in load.get("glazing_surfaces", []):
        opening = float(glazing.get("explicit_opening_area_m2") or glazing.get("opening_area_m2") or 0)
        u_value = float((glazing.get("window") or {}).get("u_value_w_m2k") or 5.8)
        if opening <= 0:
            continue
        window_area += opening
        add("window", [glazing.get("surface_id")], opening, "window", [], cfg["films"]["inside_vertical_m2k_w"],
            extra={"r_to_outside": round(max(1.0 / u_value - cfg["films"]["inside_vertical_m2k_w"], 0.02), 4)})
    # A room traced without a floor, ceiling or walls still has them: the missing ones are added for the heat
    # balance only (never for its heat gains), so its stored heat isn't overstated by a missing surface.
    if area > 0 and "floor" not in kinds:
        key = cfg["defaults"]["upper_floor_dwelling" if dwelling else "upper_floor_commercial"]
        add(key, None, area, "mirror", _layers(cfg, covering + cfg["constructions"][key]["layers"]),
            cfg["films"]["inside_heat_flow_up_m2k_w"], floor=True)
        notes.append("No floor was traced; a typical floor between storeys is assumed for the stored heat only.")
    if area > 0 and not kinds & {"ceiling", "roof"}:
        key = cfg["defaults"]["ceiling_dwelling" if dwelling else "ceiling_commercial"]
        add(key, None, area, "mirror", _layers(cfg, cfg["constructions"][key]["layers"]),
            cfg["films"]["inside_heat_flow_down_m2k_w"])
        notes.append("No ceiling was traced; a typical ceiling is assumed for the stored heat only.")
    if area > 0 and "opaque_wall" not in kinds:
        key = cfg["defaults"]["partition"]
        add(key, None, max(4 * sqrt(area) * height - window_area, 0), "mirror",
            _layers(cfg, cfg["constructions"][key]["layers"]), cfg["films"]["inside_vertical_m2k_w"])
        notes.append("No walls were traced; plasterboard walls round a square room are assumed for the stored heat only.")
    floor_area = sum(item["area_m2"] for item in groups.values() if item["floor"]) or area
    if floor_area > 0:
        add("furnishings", None, FURNISHED_FRACTION * floor_area, "adiabatic",
            _layers(cfg, cfg["constructions"]["furnishings"]["layers"]), cfg["films"]["inside_vertical_m2k_w"])
    surfaces = list(groups.values())
    if not surfaces or sum(item["area_m2"] for item in surfaces) <= 0:
        return {"method_id": stored_heat.METHOD_ID, "status": "not_calculated",
                "reason": "The room has no traced surfaces or area, so its stored heat can't be modelled."}
    key = json.dumps([[round(item["area_m2"], 2), item["film_r"], item["side"], item["layers"], item["floor"],
                       item.get("r_to_outside")] for item in sorted(surfaces, key=lambda s: json.dumps(s, sort_keys=True))])
    if key not in _CACHE:
        _CACHE[key] = {kind: [round(v, SERIES_DECIMALS) for v in stored_heat.radiant_time_series(surfaces, kind,
                                                                                                cfg["films"]["emittance"])]
                       for kind in ("nonsolar", "solar")}
    series = _CACHE[key]
    return {
        "method_id": stored_heat.METHOD_ID, "status": "calculated",
        "nonsolar_rts": series["nonsolar"], "solar_rts": series["solar"], "surface_cts": cts,
        "radiant_fractions": {name: row["value"] for name, row in cfg["radiant_fractions"].items()},
        "constructions": chosen, "floor_covering": covering_name, "notes": notes,
        "basis": cfg["basis"], "defaults_basis": cfg["defaults"]["basis"], "covering_basis": cfg["floor_coverings"]["basis"],
    }


def validate(raw):
    """A stored-heat record from an input set, checked; {} when absent."""
    if not raw:
        return {}
    if not isinstance(raw, dict) or raw.get("method_id") != stored_heat.METHOD_ID:
        raise ValueError("Stored-heat record has an unknown method.")
    if raw.get("status") != "calculated":
        return deepcopy(raw)
    for name in ("nonsolar_rts", "solar_rts"):
        series = raw.get(name)
        if (not isinstance(series, list) or len(series) != 24 or any(not isinstance(v, (int, float)) for v in series)
                or abs(sum(series) - 1) > 1e-3 or min(series) < -1e-9):
            raise ValueError(f"Stored-heat {name} must be 24 non-negative fractions summing to 1.")
    for surface_id, series in (raw.get("surface_cts") or {}).items():
        if not isinstance(series, list) or len(series) != 24 or abs(sum(series) - 1) > 1e-3:
            raise ValueError(f"Stored-heat conduction series for {surface_id} must be 24 fractions summing to 1.")
    return deepcopy(raw)


# ----- the hourly step -------------------------------------------------------------------------------------------

def _stream_rules(room, record):
    fractions = record["radiant_fractions"]
    surfaces = {s.get("surface_id"): s for s in room["cooling_load"].get("envelope_surfaces", [])}
    glazing = {s.get("surface_id"): s for s in room["cooling_load"].get("glazing_surfaces", [])}

    def opaque(surface_id):
        surface = surfaces.get(surface_id) or {}
        fraction = fractions["roof_conduction"] if surface.get("kind") in {"roof", "ceiling"} else fractions["wall_conduction"]
        return fraction, "nonsolar", record.get("surface_cts", {}).get(surface_id)

    def window(surface_id, name):
        surface = glazing.get(surface_id) or {}
        if name == "glazing_conduction":
            shgc = (surface.get("window") or {}).get("shgc")
            high = isinstance(shgc, (int, float)) and shgc > 0.5
            return fractions["window_conduction_high_shgc" if high else "window_conduction_low_shgc"], "nonsolar", None
        shade = str(surface.get("internal_shade") or (surface.get("window") or {}).get("internal_shade") or "").casefold()
        if shade and shade not in {"none", "no"}:
            return fractions["window_solar_inside_shade"], "nonsolar", None
        return fractions["window_solar_unshaded"], "solar", None

    return opaque, window


def _streams(item, fractions, opaque, window):
    """[(stream id, sensible heat gain kW, radiant fraction, series kind, CTS or None)] for one contribution, or None
    for a contribution that isn't changed (outside air, infiltration, transfer air, moisture, reviewed dynamic paths)."""
    name, inputs = item["name"], item.get("inputs") or {}
    if name == "people":
        return [(None, item["sensible_kw"], fractions["people"], "nonsolar", None)]
    if name == "lighting":
        return [(None, item["sensible_kw"], fractions["lighting"], "nonsolar", None)]
    if name == "equipment_refrigeration":
        return [(None, item["sensible_kw"], fractions["equipment"], "nonsolar", None)]
    if name == "envelope":
        rows = inputs.get("surfaces") or []
        return [(row.get("surface_id"), float(row.get("gain_kw") or 0), *opaque(row.get("surface_id"))) for row in rows]
    if name == "solar":
        rows = inputs.get("surfaces") or []
        return [(row.get("surface_id"), float(row.get("gain_kw") or 0), *opaque(row.get("surface_id"))) for row in rows]
    if name in {"glazing_conduction", "glazing_solar"}:
        return [(inputs.get("surface_id"), item["sensible_kw"], *window(inputs.get("surface_id"), name))]
    return None


def apply(room, hourly):
    """hourly: 24 lists of contributions (hour 0-23). Returns 24 new lists with sensible cooling loads in place of the
    heat gains of every changed contribution (latent heat unchanged), each keeping "heat_gain_sensible_kw"."""
    record = room.get("stored_heat") or {}
    if record.get("status") != "calculated" or len(hourly) != 24:
        return hourly
    opaque, window = _stream_rules(room, record)
    hourly = [deepcopy(items) for items in hourly]
    occurrences, gains, rules = [], {}, {}
    for hour, items in enumerate(hourly):
        seen = {}
        row = []
        for item in items:
            streams = _streams(item, record["radiant_fractions"], opaque, window)
            index = seen.get(item["name"], 0)
            seen[item["name"]] = index + 1
            if streams is None:
                row.append(None)
                continue
            keys = []
            for stream_id, gain, fraction, kind, cts in streams:
                key = (item["name"], index if stream_id is None else stream_id)
                gains.setdefault(key, [0.0] * 24)[hour] += gain
                rules[key] = (fraction, kind, cts)
                keys.append(key)
            row.append(keys)
        occurrences.append(row)
    loads = {}
    for key, gain in gains.items():
        fraction, kind, cts = rules[key]
        arrived = stored_heat.convolve(cts, gain) if cts else gain
        rts = record["solar_rts"] if kind == "solar" else record["nonsolar_rts"]
        released = stored_heat.convolve(rts, arrived) if fraction else [0.0] * 24
        zero_lag = (cts[0] if cts else 1.0) * (1 - fraction + fraction * rts[0])
        loads[key] = {"gain": gain, "arrived": arrived, "fraction": fraction, "kind": kind if fraction else "none",
                      "cts": bool(cts), "zero_lag": zero_lag,
                      "load": [(1 - fraction) * arrived[h] + fraction * released[h] for h in range(24)]}
    for hour, items in enumerate(hourly):
        for item, keys in zip(items, occurrences[hour]):
            if not keys:
                continue
            load = sum(loads[key]["load"][hour] for key in keys)
            gain = sum(loads[key]["gain"][hour] for key in keys)
            item["heat_gain_sensible_kw"] = round(gain, 4)
            item["sensible_kw"] = round(load, 4)
            item["total_kw"] = round(item["sensible_kw"] + item.get("latent_kw", 0.0), 4)
            first = loads[keys[0]]
            note = {"method_id": record["method_id"], "heat_gain_sensible_kw": round(gain, 4),
                    "radiant_fraction": first["fraction"], "time_series": first["kind"],
                    "conduction_time_series": first["cts"]}
            if item["name"] in {"envelope", "solar"}:
                for row in (item.get("inputs") or {}).get("surfaces", []):
                    key = (item["name"], row.get("surface_id"))
                    if key in loads:
                        row["cooling_load_kw"] = round(loads[key]["load"][hour], 4)
                        row["earlier_hour_load_kw"] = round(loads[key]["load"][hour] -
                                                            loads[key]["gain"][hour] * loads[key]["zero_lag"], 6)
                        row["conduction_time_series"] = loads[key]["cts"]
            if item["name"] in {"glazing_conduction", "glazing_solar"} and len(keys) == 1:
                note["earlier_hour_load_kw"] = round(loads[keys[0]]["load"][hour] -
                                                       loads[keys[0]]["gain"][hour] * loads[keys[0]]["zero_lag"], 6)
            item["inputs"] = {**(item.get("inputs") or {}), "stored_heat": note}
    return hourly
