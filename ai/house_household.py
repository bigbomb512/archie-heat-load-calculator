"""Who lives in a house and where they are — no AI (user decisions 30 October 2026).

- 2 people in the main bedroom, 1 in each other bedroom; the household is their sum, per dwelling.
- Main bedroom: the one labelled MASTER/MAIN; else the bedroom with the ensuite; else the largest.
- The household is in the bedrooms at night and in the living areas (living, family, kitchen, dining, study, media,
  rumpus, gym) in the afternoon and evening (the house profiles' hours). Each living room is sized for the whole
  household; the whole-house total counts each person once per hour (ai/hourly_loads.py).
- Two dwellings in one set: each counts its own household; rooms are split by "Dwelling A/B" or "Unit 1/2" in their
  label or level, else the set is one household.
"""
import re

BEDROOM = "house_bedroom"
LIVING = "house_living"
COOLED_SCOPES = {"comfort_hvac", "comfort_hvac_with_process_exception"}
_MAIN = re.compile(r"\b(?:master|main)\b|\bmbr\b", re.I)
_DWELLING = re.compile(r"\b(?:dwelling|unit|residence|house)\s*([a-z]|\d+)\b", re.I)


def dwelling_of(room):
    """The dwelling a room belongs to: "Dwelling A", "Unit 2" … from its label or level, else "the house"."""
    for text in (room.get("label", ""), room.get("level", "")):
        match = _DWELLING.search(str(text))
        if match:
            return f"{match.group(0)[:-len(match.group(1))].strip().title()} {match.group(1).upper()}"
    return "the house"


def plan(rooms, ensuite_bedrooms=()):
    """{room_id: {"count", "role", "group_id", "household", "rationale"}} for a house's cooled bedrooms and living areas.

    rooms: [{"room_id", "label", "level", "category", "scope", "area_m2"}]; ensuite_bedrooms: room_ids of bedrooms an
    ensuite opens off. Dwellings with no bedroom get no household (their living areas keep the profile fallback).
    """
    groups = {}
    for room in rooms:
        if room.get("scope") in COOLED_SCOPES and room.get("category") in {BEDROOM, LIVING}:
            groups.setdefault(dwelling_of(room), []).append(room)
    result = {}
    for dwelling, members in sorted(groups.items()):
        bedrooms = sorted((room for room in members if room["category"] == BEDROOM),
                          key=lambda room: (-(room.get("area_m2") or 0), str(room.get("label", "")), str(room["room_id"])))
        if not bedrooms:
            continue
        labelled = [room for room in bedrooms if _MAIN.search(str(room.get("label", "")))]
        with_ensuite = [room for room in bedrooms if room["room_id"] in set(ensuite_bedrooms)]
        if labelled:
            main, why = labelled[0], f"labelled {labelled[0].get('label')}"
        elif with_ensuite:
            main, why = with_ensuite[0], "the bedroom with the ensuite"
        else:
            main, why = bedrooms[0], "the largest bedroom (no bedroom is labelled master or main and none has an ensuite)"
        household = 2 + (len(bedrooms) - 1)
        group_id = f"household:{dwelling}"
        names = ", ".join(str(room.get("label")) for room in bedrooms)
        for room in bedrooms:
            count = 2 if room is main else 1
            result[room["room_id"]] = {
                "count": count, "role": "bedroom", "group_id": group_id, "household": household,
                "rationale": (f"{'Main bedroom' if count == 2 else 'Bedroom'}: {count} "
                              f"{'people' if count == 2 else 'person'} (2 in the main bedroom, 1 in each other bedroom; "
                              f"user decision 2026-10-30). Main bedroom: {main.get('label')}, {why}."),
            }
        for room in members:
            if room["category"] == LIVING:
                result[room["room_id"]] = {
                    "count": household, "role": "living", "group_id": group_id, "household": household,
                    "rationale": (f"Living area: the whole household of {household} ({dwelling}: {len(bedrooms)} bedrooms — "
                                  f"{names}; 2 in the main bedroom, 1 in each other). The room is sized for all of them at "
                                  "its busiest hour; the whole-house total counts each person once per hour."),
                }
    return result
