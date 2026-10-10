"""From an equipment reading to heat in a room: rating × heat-to-room factor, with where each number came from.

The drawings name the equipment but rarely print its power. This step proposes, for each item:
- a type (from its name: cooking, refrigeration, dishwashing, hot drinks, electronics, signage);
- a rated input in watts: the printed rating when there is one; else a TYPICAL value, but only for types whose
  range is narrow enough to be useful (screens, POS, signs, small self-contained refrigeration); cooking,
  dishwashing and hot-drink equipment vary too much and need the rating from the spec sheet;
- a heat-to-room factor: the share of the rated input that ends up as heat in the room.

An operator can instead enter the heat to the room straight from the manufacturer's data sheet (many combi ovens,
dishwashers and hoods list their sensible and latent heat emission): sensible W each, moisture (latent) W each and
where the figures come from. That replaces rating × factor for the item and is labelled as the data sheet's figure.

Every typical value and factor here is a GENERIC PLACEHOLDER, labelled as such, until the licensed AIRAH DA09
tables are available (user decision 2026-10-05). The operator sees and can change every number before it is
used. No I/O.
"""

import re

PLACEHOLDER = "generic placeholder (AIRAH DA09 pending)"
DATA_SHEET = "data sheet heat to room, entered by the operator"

# (type, pattern on the normalised name). First match wins, so the specific patterns come first.
TYPES = (
    # Not heat sources in the room themselves: an exhaust hood (it belongs to airflow) and refrigerated rooms
    # (a coolroom's load is its own refrigeration scope, not equipment in another room).
    ("exhaust_hood", r"\b(range hood|exhaust hood|hood|canopy|exhaust)\b"),
    ("refrigerated_room", r"\b(cool ?room|freezer room|cold room|walk in (cooler|freezer|fridge))\b"),
    ("refrigeration", r"\b(fridge|refrigerat\w*|freezer|chiller|bottle cooler|keg|ice (maker|machine)|cold (food|display)|display (fridge|case|cabinet|chiller))\b"),
    ("cooking", r"\b(oven|fryer|burner|wok|grill|griddle|range|cooktop|stove|bbq|barbecue|salamander|steamer|bain marie|hot food|warmer|rice cooker|pizza|toaster|induction|hot plate|char ?grill|rotisserie|kebab|noodle cooker|pasta cooker|6 burner|4 burner)\b"),
    ("dishwashing", r"\b(dish ?washer|glass ?washer|pot ?washer|warewasher)\b"),
    ("hot_drinks", r"\b(coffee|espresso|boiler|hot water|urn|water dispenser|zip|kettle|grinder|blender)\b"),
    ("electronics", r"\b(tv|television|screen|monitor|pos|computer|printer|terminal|eftpos|speaker|audio|amplifier|router|server|kiosk|touch ?screen)\b"),
    ("signage", r"\b(sign|signage|light ?box|illuminated|neon|logo)\b"),
)

# Typical rated input (W) for types with a narrow range only, by a size word in the name when there is one.
TYPICAL_W = {
    "electronics": ((r"\b(8\d|7\d|65) ?(inch|in|\")", 250), (r"\b(tv|television|screen|monitor)\b", 150),
                    (r"\b(pos|terminal|eftpos|printer|computer)\b", 100), (r".", 100)),
    "signage": ((r".", 100),),
    "refrigeration": ((r"\bice (maker|machine)\b", 800), (r"\b(3|three) ?door\b", 800), (r"\b(2|two|double) ?door\b", 600),
                      (r"\b(chest)\b", 300), (r"\b(underbench|under ?counter|ub)\b", 350), (r"\bdisplay\b", 1000),
                      (r"\b(single|1|one) ?door\b", 400), (r".", 500)),
}

# Share of the rated input that becomes heat in the room (sensible), as a placeholder per type.
HEAT_TO_ROOM = {
    "electronics": 1.0, "signage": 1.0,
    "refrigeration": 1.0,            # self-contained: its condenser rejects heat into the room
    "cooking_hooded": 0.2,           # most heat is captured by the hood; mainly radiant heat reaches the room
    "cooking_unhooded": 0.6,
    "dishwashing": 0.4, "hot_drinks": 0.5,
}


def normalise(name):
    text = str(name or "").lower().replace("u/b", "underbench")
    text = " ".join(re.sub(r"[^a-z0-9\"]+", " ", text).split())
    return re.sub(r"\bub\b", "underbench", text)


def equipment_type(name):
    text = normalise(name)
    for kind, pattern in TYPES:
        if re.search(pattern, text):
            return kind
    return "other"


def printed_watts(text):
    """A printed rating in watts: "6.3 kW" -> 6300, "2400 W" -> 2400; None for anything else (amps, volts)."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(kw|w)\b", str(text or "").lower())
    if not match:
        return None
    value = float(match.group(1)) * (1000 if match.group(2) == "kw" else 1)
    return round(value) if value > 0 else None


def typical_watts(kind, name):
    text = normalise(name)
    for pattern, watts in TYPICAL_W.get(kind, ()):
        if re.search(pattern, text):
            return watts
    return None


def factor_key(kind, under_hood):
    if kind == "cooking":
        return "cooking_hooded" if under_hood else "cooking_unhooded"
    return kind


def proposal(item):
    """Proposed rating, factor and heat for one equipment item, each with its source; missing parts named."""
    kind = equipment_type(item.get("name"))
    printed = printed_watts(item.get("rated_power"))
    typical = None if printed else typical_watts(kind, item.get("name"))
    rated = printed or typical
    factor = HEAT_TO_ROOM.get(factor_key(kind, item.get("under_hood")))
    quantity = item.get("quantity") if type(item.get("quantity")) is int and item.get("quantity") > 0 else 1
    needed = []
    if kind in {"exhaust_hood", "refrigerated_room"}:
        reason = ("an exhaust hood: its airflow belongs to ventilation, not equipment heat" if kind == "exhaust_hood"
                  else "a refrigerated room: its load is its own refrigeration scope")
        return {"type": kind, "quantity": quantity, "rated_input_w": None, "rated_source": "", "heat_to_space_factor": None,
                "factor_source": "", "heat_w": None, "needed": [], "not_equipment": reason}
    if not rated:
        needed.append("rated power or heat to room (from the data sheet)")
    if factor is None:
        needed.append("heat-to-room factor")
    if kind == "cooking" and item.get("under_hood") is None:
        needed.append("whether it is under a hood")
    return {
        "type": kind, "quantity": quantity,
        "rated_input_w": rated, "rated_source": "printed on the drawings" if printed else PLACEHOLDER if typical else "",
        "heat_to_space_factor": factor, "factor_source": PLACEHOLDER if factor is not None else "",
        "heat_w": round(quantity * rated * factor) if rated and factor is not None else None,
        "needed": needed,
    }


def _each(value, field):
    number = value.get(field)
    return float(number) if isinstance(number, (int, float)) and not isinstance(number, bool) and number >= 0 else None


def _quantity(value):
    return value.get("quantity") if type(value.get("quantity")) is int and value.get("quantity") > 0 else 1


def from_data_sheet(value):
    """True when the operator entered the item's heat to the room from its data sheet."""
    return bool(_each(value, "sensible_to_room_w"))


def latent_w(value):
    """Moisture (latent) heat into the room for an accepted item: only from a data-sheet entry, else 0."""
    latent = _each(value, "latent_to_room_w") if from_data_sheet(value) else None
    return round(_quantity(value) * latent) if latent else 0


def heat_w(value):
    """Sensible heat into the room for an accepted item: the data sheet's figure when entered, else rating × factor;
    None when neither is complete."""
    if from_data_sheet(value):
        return round(_quantity(value) * _each(value, "sensible_to_room_w"))
    try:
        rated, factor = float(value.get("rated_input_w")), float(value.get("heat_to_space_factor"))
    except (TypeError, ValueError):
        return None
    return round(_quantity(value) * rated * factor) if rated > 0 and 0 <= factor <= 1 else None
