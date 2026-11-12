"""How much of the sun on a window gets in: SHGC by the angle the sun strikes the glass, and inside blinds.

Decided 2026-11-12 (user: "work on the glass sun by angle and blinds"), from ASHRAE Handbook—Fundamentals 2001,
fenestration chapter, as reproduced in Arici's course notes (Michigan Tech MEEM 4200, Chapter 7, Tables 7-3 and 7-4,
"Source: ASHRAE Handbook, Fundamentals Volume, 2001"):

- **Angle.** A window's SHGC is quoted at normal incidence; at an angle more is reflected. Table 7-3 gives SHGC at 0,
  40, 50, 60, 70 and 80 degrees and for diffuse light for single clear (ID 1a), double clear (5a) and low-e double
  (21a) glass. The chapter's advice when a maker gives only the normal SHGC: use the angle-dependence of the most
  similar window. So the direct sun on the glass is scaled by SHGC(angle) / SHGC(0) of the similar window, linearly
  between the tabulated angles (0 at 90 degrees), and the diffuse sky and ground light by SHGC(diffuse) / SHGC(0).
  The window's own normal SHGC is then applied as before.
- **Blinds.** Inside blinds and shades multiply the glass's solar gain by an interior attenuation coefficient (IAC),
  Table 7-4, by device and glass; angle dependence is neglected, as the source says. A blind counts only where the
  drawings or an operator show it (NatHERS models window coverings only where present). When its colour or opacity
  isn't known, the device's highest IAC is used (the least credit). Curtains and drapes need the fabric table
  (Table 7-5), which isn't captured here, so they get no credit and say so.
No I/O.
"""

SOURCE = ("ASHRAE Handbook—Fundamentals 2001, fenestration chapter (reproduced in Arici, MEEM 4200 Chapter 7, "
          "Tables 7-3 and 7-4)")
ANGLES = (0.0, 40.0, 50.0, 60.0, 70.0, 80.0)

# SHGC at the tabulated incidence angles, and for diffuse light (Table 7-3, centre of glazing).
GLASS = {
    "single_clear": {"ashrae_id": "1a", "label": "uncoated single glazing, clear 3.2 mm",
                     "shgc": (0.86, 0.84, 0.82, 0.78, 0.67, 0.42), "diffuse": 0.78},
    "double_clear": {"ashrae_id": "5a", "label": "uncoated double glazing, clear/clear 3.2 mm",
                     "shgc": (0.76, 0.74, 0.71, 0.64, 0.50, 0.26), "diffuse": 0.66},
    "double_low_e": {"ashrae_id": "21a", "label": "low-e double glazing (e = 0.1 on surface 2), 3.2 mm",
                     "shgc": (0.65, 0.64, 0.62, 0.56, 0.43, 0.23), "diffuse": 0.57},
}

# IAC (Table 7-4): venetian blinds (medium, light) and roller shades (opaque dark, opaque white, translucent light).
IAC = {
    "single_clear": {"label": "single glazing, clear residential",
                     "venetian": {"medium": 0.75, "light": 0.68}, "roller": {"dark": 0.82, "white": 0.40, "translucent": 0.45}},
    "single_tinted": {"label": "single glazing, heat absorbing",
                      "venetian": {"medium": 0.84, "light": 0.78}, "roller": {"dark": 0.66, "white": 0.44, "translucent": 0.47}},
    "double_clear": {"label": "clear double, residential",
                     "venetian": {"medium": 0.71, "light": 0.66}, "roller": {"dark": 0.81, "white": 0.40, "translucent": 0.46}},
    "double_low_e": {"label": "other glazings (approximate)",
                     "venetian": {"medium": 0.83, "light": 0.77}, "roller": {"dark": 0.74, "white": 0.45, "translucent": 0.52}},
}
SHADE_KINDS = ("venetian", "roller")


def glass_type(u_value_w_m2k, shgc):
    """The tabulated window most like this one: single glazing at U 4.5 W/m²K or more, else double; a double with a
    normal SHGC below 0.70 is taken as low-e (ASHRAE's clear double is 0.76, its low-e 0.65)."""
    if u_value_w_m2k is None or u_value_w_m2k >= 4.5:
        return "single_clear"
    return "double_clear" if (shgc or 0) >= 0.70 else "double_low_e"


def angle_modifier(kind, incidence_deg):
    """SHGC(angle) / SHGC(0) for the direct sun, linear between the tabulated angles and 0 at 90 degrees."""
    values = GLASS[kind]["shgc"]
    ratios = [value / values[0] for value in values] + [0.0]
    angles = list(ANGLES) + [90.0]
    angle = min(max(float(incidence_deg), 0.0), 90.0)
    for index in range(len(angles) - 1):
        low, high = angles[index], angles[index + 1]
        if angle <= high:
            share = (angle - low) / (high - low)
            return ratios[index] + share * (ratios[index + 1] - ratios[index])
    return 0.0


def diffuse_modifier(kind):
    """SHGC(diffuse) / SHGC(0) for sky and ground light."""
    return GLASS[kind]["diffuse"] / GLASS[kind]["shgc"][0]


def iac(shade, kind, shgc=None):
    """(IAC, note) for an inside blind or shade on this glass; (1.0, note) when there is none or it can't be credited.

    shade: {"kind": "venetian" | "roller" | "curtain", "colour": "light" | "medium" | "dark" | "white",
    "opacity": "opaque" | "translucent"}. A single glass with a normal SHGC below 0.70 uses the heat-absorbing row."""
    if not isinstance(shade, dict) or not shade.get("kind"):
        return 1.0, ""
    device = str(shade["kind"]).casefold()
    if device not in SHADE_KINDS:
        return 1.0, f"A {device} is shown, but curtains and drapes need the fabric table, so it gets no credit."
    row_id = "single_tinted" if kind == "single_clear" and shgc is not None and shgc < 0.70 else kind
    row = IAC[row_id]
    colour = str(shade.get("colour") or "").casefold()
    opacity = str(shade.get("opacity") or "").casefold()
    if device == "venetian":
        key = colour if colour in row["venetian"] else "medium"
        known = colour in row["venetian"]
        value = row["venetian"][key]
        what = f"{key} venetian blinds"
    else:
        key = ("translucent" if opacity == "translucent" else "white" if colour in {"white", "light"} and opacity == "opaque"
               else "dark" if opacity == "opaque" or colour == "dark" else "")
        known = bool(key)
        key = key or max(row["roller"], key=row["roller"].get)
        value = row["roller"][key]
        what = {"dark": "opaque dark roller shade", "white": "opaque white roller shade", "translucent": "translucent light roller shade"}[key]
    note = (f"Inside {what} on {row['label']}: IAC {value:g} ({SOURCE}, Table 7-4)"
            + ("" if known else "; colour or opacity not shown, so the least credit for the device is taken") + ".")
    return value, note
