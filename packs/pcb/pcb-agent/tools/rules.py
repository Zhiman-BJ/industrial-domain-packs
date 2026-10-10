"""Design-rule arithmetic every layout needs, independent of any checker.

IPC-2221 (the generic standard for PCB design) gives the copper cross-section a
trace needs for a current at a temperature rise:

    A [mil^2] = (I / (k * dT^0.44)) ^ (1/0.725)      k = 0.048 external, 0.024 internal
    width [mil] = A / (thickness_oz * 1.378)

That is the whole formula. Use it to size VBUS, motor and regulator traces
instead of guessing a "wide" number.
"""
from __future__ import annotations


def trace_width_mm(current_a: float, rise_c: float = 10.0, copper_oz: float = 1.0,
                   external: bool = True) -> float:
    k = 0.048 if external else 0.024
    area_mil2 = (current_a / (k * rise_c ** 0.44)) ** (1 / 0.725)
    width_mil = area_mil2 / (copper_oz * 1.378)
    return round(width_mil * 0.0254, 3)


def clearance_mm(voltage_v: float, coated: bool = False) -> float:
    """IPC-2221 table 6-1, external uncoated / coated conductors, up to 300 V."""
    steps = ((15, 0.1), (30, 0.1), (50, 0.6), (100, 0.6), (150, 0.6), (170, 1.25), (250, 1.25), (300, 1.25))
    coated_steps = ((15, 0.05), (30, 0.05), (50, 0.13), (100, 0.13), (150, 0.4), (170, 0.4), (250, 0.4), (300, 0.8))
    for vmax, c in (coated_steps if coated else steps):
        if voltage_v <= vmax:
            return c
    return 2.5


def via_count_for(current_a: float, drill_mm: float = 0.3, per_via_a: float | None = None) -> int:
    """How many stitched vias a power net needs. A 0.3 mm drill with 25 um
    plating carries roughly 1 A at 10 C rise; scale from there."""
    if per_via_a is None:
        per_via_a = 1.0 * (drill_mm / 0.3)
    n = int(current_a / per_via_a + 0.999)
    return max(n, 1)


def suggest_net_classes(nets: dict[str, float]) -> dict[str, dict]:
    """{net: current_A} -> {net: {"track_mm": w, "class": "POWER"|"SIGNAL"}}"""
    out = {}
    for net, i in nets.items():
        w = trace_width_mm(i) if i else 0.2
        out[net] = {"track_mm": max(w, 0.2), "class": "POWER" if i and i >= 0.5 else "SIGNAL"}
    return out
