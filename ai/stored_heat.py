"""Stored heat: conduction time series for walls and roofs, and radiant time series for rooms, from our own heat balance.

ASHRAE's Radiant Time Series method (Fundamentals 2009 Ch 18) turns each hour's heat gains into cooling load: the
convective part reaches the room air in the same hour, the radiant part is absorbed by the room's surfaces and given
back to the air over the following hours by that room's radiant time series (RTS); heat conducted through a wall or
roof arrives over the day by the construction's conduction time series (CTS). ASHRAE publishes RTS and CTS tables
for its own representative constructions only, and those tables aren't available to us, so this module calculates
them from layer properties (AIRAH Technical Handbook 2007, pp. 149-177) the same way the published ones were made:

* conduction: each layer's exact transmission matrix for a 24-hour periodic temperature, evaluated harmonic by
  harmonic (the "frequency-domain" route to periodic response factors), for an outside temperature pulse one hour
  wide; the 24 hourly responses are the periodic response factors, they sum to the U-value, and divided by U they
  are the CTS;
* rooms: a heat balance on every inside surface (convection to the room air held at a constant temperature, long-
  wave exchange with the other surfaces through a mean-radiant node, conduction into each construction, whose far
  side is outdoors, the ground or an identical neighbouring room) for a one-hour radiant pulse; the hourly
  convection to the air, divided by its daily total, is the RTS. Nonsolar radiant heat lands on every surface by
  area; solar heat lands on the floor.

Checked against independent published results in tests/test_stored_heat.py (ASHRAE's PRF/RTF generator results for
two constructions in a 2015 journal paper, and ASHRAE 2009 Table 19's heavyweight nonsolar series). These factors
are our own calculation, not ASHRAE's published tables; TODO.md keeps the check against the published tables open.
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

PATH = Path(__file__).resolve().parents[1] / "config" / "stored_heat.json"
METHOD_ID = "rts_heat_balance_v1"
PERIOD_S = 24 * 3600.0
HARMONICS = 1440          # pulse weights fall as 1/n^2; 1440 harmonics leave < 0.05% of a factor unresolved
SIGMA = 5.670374419e-8


@lru_cache(maxsize=1)
def _config_text():
    return PATH.read_text(encoding="utf-8")


def load():
    return json.loads(_config_text())


def _omegas():
    n = np.arange(1, HARMONICS + 1)
    return n, 2 * np.pi * n / PERIOD_S


def _pulse_weights(n):
    """Fourier weights of a one-hour triangular pulse (area one hour, period 24 h), sampled on the hour.

    The same weights describe a pulse held for one hour and its response averaged over each hour, so the factors
    apply to hourly heat gains and hourly loads."""
    x = np.pi * n / 24.0
    return (np.sin(x) / x) ** 2 / 24.0


def _hourly(dc, response):
    """24 hourly values of a periodic response: dc term plus harmonics (response already includes the weights)."""
    n, _omega = _omegas()
    hours = np.arange(24)
    phase = np.exp(1j * 2 * np.pi * np.outer(hours, n) / 24.0)
    return (dc + 2 * np.real(phase @ response))


def _layer(layer, omega):
    """(log scale, a, b, c, d) of one layer's transmission matrix, scaled by exp(-log scale) so it never overflows.

    [T_in, q_in] = M [T_out, q_out] with q flowing from the in face to the out face; a mass layer is
    [[cosh gL, sinh gL / (k g)], [k g sinh gL, cosh gL]] with g = sqrt(i omega / diffusivity)."""
    if layer.get("r_m2k_w") is not None:
        one = np.ones_like(omega, dtype=complex)
        return np.zeros_like(omega, dtype=complex), one, float(layer["r_m2k_w"]) * one, 0 * one, one
    k, rho, c, length = (float(layer[key]) for key in ("k_w_mk", "density_kg_m3", "specific_heat_j_kgk", "thickness_m"))
    g = np.sqrt(1j * omega * rho * c / k)
    x = g * length
    half = 0.5 * (1 + np.exp(-2 * x))           # cosh x / e^x
    sinh = 0.5 * (1 - np.exp(-2 * x))           # sinh x / e^x
    return x, half, sinh / (k * g), k * g * sinh, half


def _matrix(layers, omega):
    """Product of the layers' matrices, outer face first: (log scale, a, b, c, d)."""
    scale = np.zeros_like(omega, dtype=complex)
    a, b, c, d = (np.ones_like(omega, dtype=complex), np.zeros_like(omega, dtype=complex),
                  np.zeros_like(omega, dtype=complex), np.ones_like(omega, dtype=complex))
    for layer in layers:
        s, la, lb, lc, ld = _layer(layer, omega)
        a, b, c, d = a * la + b * lc, a * lb + b * ld, c * la + d * lc, c * lb + d * ld
        scale = scale + s
    return scale, a, b, c, d


def resistance(layers):
    return sum(float(layer["r_m2k_w"]) if layer.get("r_m2k_w") is not None
               else float(layer["thickness_m"]) / float(layer["k_w_mk"]) for layer in layers)


def periodic_response_factors(layers):
    """24 periodic response factors (W/m2K) of a construction whose layers run outside film first, inside film last:
    the heat reaching the room in each of the 24 hours after a one-hour, one-kelvin rise of the outside (sol-air)
    temperature, the room held steady. They sum to the U-value."""
    n, omega = _omegas()
    scale, _a, b, _c, _d = _matrix(layers, omega)
    # q into the room per unit outside temperature with the room at zero: 1/B (exp(-scale) / B~ underflows to zero
    # for thick masonry at high harmonics, which is the right limit).
    transfer = np.exp(-scale) / b
    u = 1.0 / resistance(layers)
    return [float(v) for v in _hourly(u / 24.0, _pulse_weights(n) * transfer)]


def conduction_time_series(layers):
    """The construction's CTS: its periodic response factors divided by U (24 fractions summing to 1)."""
    prf = periodic_response_factors(layers)
    total = sum(prf)
    return [value / total for value in prf]


def _solve_room(surfaces, absorbed, emittance=0.9, mean_c=24.0):
    """Hourly convection to the room air after a one-hour radiant pulse of 1 W·h split over surfaces by `absorbed`
    (fractions summing to 1). Surfaces: {area_m2, film_r (inside film), side, layers}: side "outdoor"/"fixed" (layers
    from the inside face out, ending in the outside film or a deep ground layer held steady), "mirror" (an identical
    room beyond), "pair_top"/"pair_bottom" (floor/ceiling of stacked identical rooms, layers top-down, pair_index
    the other face) or "window" (r_to_outside, no mass)."""
    h_r = 4 * emittance * SIGMA * (mean_c + 273.15) ** 3
    area = np.array([float(s["area_m2"]) for s in surfaces])
    h_c = np.array([1.0 / float(s["film_r"]) - h_r for s in surfaces])
    if (h_c <= 0).any():
        raise ValueError("An inside film resistance is smaller than its radiative part.")
    m = len(surfaces)
    n, omega = _omegas()
    count = len(omega) + 1
    allw = np.concatenate([[0.0], omega])
    # Conduction admittances: flux leaving surface i into its construction = self_i T_i - cross_i T_partner.
    self_y = np.zeros((count, m), dtype=complex)
    cross = {}
    for i, s in enumerate(surfaces):
        layers = s["layers"]
        if s["side"] == "window":            # no mass: inside face to outdoors through the rest of the window
            self_y[:, i] = 1.0 / float(s["r_to_outside"])
            continue
        if s["side"] in {"outdoor", "fixed"}:
            scale, a, b, c, d = _matrix(layers, allw[1:])
            r = resistance(layers)
            self_y[1:, i] = d / b
            self_y[0, i] = 1.0 / r
        elif s["side"] == "adiabatic":        # no heat leaves the back face (furnishings): q_in / T_in = c / a
            scale, a, b, c, d = _matrix(layers, allw[1:])
            self_y[1:, i] = c / a
            self_y[0, i] = 0.0
        elif s["side"] == "mirror":           # an identical room on the far side, at the same history
            scale, a, b, c, d = _matrix(layers, allw[1:])
            self_y[1:, i] = (d - np.exp(-scale)) / b
            self_y[0, i] = 0.0
        elif s["side"] in {"pair_top", "pair_bottom"}:
            # Floor and ceiling of identical stacked rooms: one floor/ceiling construction, its layers listed top-down.
            # The floor is its top face; the ceiling is the bottom face of the same construction one storey up, whose
            # top face is the floor above at this room's floor temperature (and vice versa below).
            j = s["pair_index"]
            scale, a, b, c, d = _matrix(layers, allw[1:])
            self_y[1:, i] = d / b if s["side"] == "pair_top" else a / b
            self_y[0, i] = 1.0 / resistance(layers)
            cross[(i, j)] = np.concatenate([[1.0 / resistance(layers)], np.exp(-scale) / b])
        else:
            raise ValueError(f"Unknown surface side {s['side']!r}.")
    total_area = area.sum()
    weights = np.concatenate([[1.0 / 24.0], _pulse_weights(n)])
    source = np.array(absorbed, dtype=float)
    # Balance on surface i (per unit area): absorbed_i/A_i = h_c T_i + h_r (T_i - sum_k A_k T_k / A) + self_i T_i - cross_ij T_j
    matrix = np.zeros((count, m, m), dtype=complex)
    for i in range(m):
        matrix[:, i, i] += h_c[i] + h_r + self_y[:, i]
        for k in range(m):
            matrix[:, i, k] -= h_r * area[k] / total_area
    for (i, j), values in cross.items():
        matrix[:, i, j] -= values
    rhs = np.broadcast_to(source / area, (count, m)).astype(complex) * weights[:, None]
    temps = np.linalg.solve(matrix, rhs[..., None])[..., 0]
    load = temps @ (h_c * area)
    return [float(v) for v in _hourly(float(np.real(load[0])), load[1:])]


def radiant_time_series(surfaces, kind="nonsolar", emittance=0.9):
    """The room's RTS (24 fractions summing to 1). Nonsolar radiant heat lands on every surface by area; solar
    heat lands on the floor surfaces (kind "solar")."""
    area = np.array([float(s["area_m2"]) for s in surfaces])
    if kind == "solar":
        floor = np.array([1.0 if s.get("floor") else 0.0 for s in surfaces]) * area
        if floor.sum() <= 0:
            floor = area
        absorbed = floor / floor.sum()
    else:
        absorbed = area / area.sum()
    hourly = _solve_room(surfaces, absorbed, emittance)
    total = sum(hourly)
    return [value / total for value in hourly]


def convolve(series, gains):
    """Hourly result of 24 periodic hourly gains spread by a 24-value time series: out[h] = sum_j r_j gain[h-j]."""
    return [sum(series[j] * gains[(hour - j) % 24] for j in range(24)) for hour in range(24)]
