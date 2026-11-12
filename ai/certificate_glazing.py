"""A house window's glass from its NatHERS or BASIX certificate (read by the P8 drawing task). No AI, no I/O.

The certificate lists window types (U-value, SHGC) and a window schedule by room ("location"). A traced room takes the
area-weighted U-value and SHGC of the certificate's windows in the room of the same name; a room the certificate
doesn't name takes the whole dwelling's area-weighted values; a certificate with types but no schedule gives the plain
average of its types. Room names match on their words (abbreviations expanded) and, where both have one, the same
number ("Bedroom 2" is not "Bedroom 3"). With two dwellings, a certificate naming one is used only for its rooms.
"""
import re

ABBREVIATIONS = {"ens": "ensuite", "ldry": "laundry", "lndry": "laundry", "pwdr": "powder", "pdr": "powder",
                 "bed": "bedroom", "bdrm": "bedroom", "br": "bedroom", "mbr": "master", "liv": "living", "kit": "kitchen",
                 "din": "dining", "fam": "family", "wir": "robe", "lounge": "living", "meals": "dining"}
IGNORED = {"and", "the", "room", "area", "rm", "level", "floor"}


def tokens(label):
    """(words, numbers) of a room name, abbreviations expanded."""
    raw = re.findall(r"[a-z]+|\d+", str(label).casefold())
    words = {ABBREVIATIONS.get(word, word) for word in raw if not word.isdigit()} - IGNORED
    return words, {word for word in raw if word.isdigit()}


def same_room(room_label, location):
    room_words, room_numbers = tokens(room_label)
    words, numbers = tokens(location)
    if room_numbers and numbers and room_numbers != numbers:
        return False
    if (room_words & {"bedroom", "master"}) and (words & {"bedroom", "master"}):
        # "Master bedroom" and "Bedroom 1" are different rooms unless the numbers or "master" agree.
        if ("master" in room_words) != ("master" in words) and not (room_numbers and room_numbers == numbers):
            return False
    return bool(room_words & words)


def _dwelling_key(text):
    match = re.search(r"\b(?:dwelling|unit)\s*([a-z]|\d+)\b", str(text), re.I)
    return match.group(1).upper() if match else ""


def _weighted(rows, types):
    area = sum(row["height_mm"] * row["width_mm"] for row in rows)
    u_value = sum(types[row["window_id"]]["u_value"] * row["height_mm"] * row["width_mm"] for row in rows) / area
    shgc = sum(types[row["window_id"]]["shgc"] * row["height_mm"] * row["width_mm"] for row in rows) / area
    return round(u_value, 3), round(shgc, 3), round(area / 1e6, 2)


def room_glass(certificates, room_label, dwelling=""):
    """{"u_value", "shgc", "basis" ("room" | "dwelling" | "types"), "source"} for a room, or None with no certificate.
    dwelling: the room's dwelling ("Dwelling A") when the set has two."""
    wanted = _dwelling_key(dwelling)
    usable = [row for row in certificates if row.get("types")]
    if wanted and any(_dwelling_key(row.get("dwelling") or row.get("title")) == wanted for row in usable):
        usable = [row for row in usable if _dwelling_key(row.get("dwelling") or row.get("title")) == wanted]
    if not usable:
        return None
    pages = ", ".join(f"p{row.get('page')}" for row in usable)
    kind = next((row.get("certificate") for row in usable if row.get("certificate")), "") or "energy"
    types = {item["window_id"]: item for row in usable for item in row["types"]}
    windows = [window for row in usable for window in row.get("windows", []) if window.get("window_id") in types]
    in_room = [window for window in windows if same_room(room_label, window["location"])]
    if in_room:
        u_value, shgc, area = _weighted(in_room, types)
        ids = sorted({window["window_id"] for window in in_room})
        return {"u_value": u_value, "shgc": shgc, "basis": "room",
                "source": (f"{kind} certificate ({pages}): {len(in_room)} window(s) in {in_room[0]['location']}, "
                           f"{area:g} m², type(s) {', '.join(ids)}; area-weighted U {u_value:g}, SHGC {shgc:g}")}
    if windows:
        u_value, shgc, area = _weighted(windows, types)
        return {"u_value": u_value, "shgc": shgc, "basis": "dwelling",
                "source": (f"{kind} certificate ({pages}): this room isn't named in its window schedule, so all "
                           f"{len(windows)} windows ({area:g} m²) area-weighted: U {u_value:g}, SHGC {shgc:g}")}
    u_value = round(sum(item["u_value"] for item in types.values()) / len(types), 3)
    shgc = round(sum(item["shgc"] for item in types.values()) / len(types), 3)
    return {"u_value": u_value, "shgc": shgc, "basis": "types",
            "source": f"{kind} certificate ({pages}): no window schedule, so the average of its {len(types)} window type(s): U {u_value:g}, SHGC {shgc:g}"}
