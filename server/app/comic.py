"""Geometry helpers for the comic-panel primitives (see templates/macros/comic.html)."""

import math


def _n(value: float) -> str:
    """Format a coordinate compactly for an SVG path.

    Rounds to 3 decimals (sub-pixel) — trims trailing zeros and, importantly,
    snaps tiny near-zero values (e.g. cos(pi/2) ~ 6e-17) to 0 so the output never
    falls back to scientific notation.
    """
    return f"{(round(value, 3) or 0):g}"


def _hash01(n: float) -> float:
    """Deterministic pseudo-random in [0, 1) from a float (classic sin hash).

    Chosen because JS (the demo page's live mirror) reproduces it bit-for-bit
    with Math.sin, so the server-baked border and the live preview agree.
    """
    x = math.sin(n) * 43758.5453
    return x - math.floor(x)


def comic_border_path(
    width: float,
    height: float,
    radius: float = 0,
    mid_width: float = 4,
    corner_width: float = 4,
    roughness: float = 0,
    seed: float = 0,
    frequency: float = 6,
    overscan: float = 6,
) -> str:
    """Build the SVG path for a variable-width, pen-pressure comic border.

    The border is the even-odd fill between two sub-paths:

    - an **outer** rounded rectangle, oversized by ``overscan`` past the panel on
      every side so that — once the panel clips it to its own rounded rect — the
      border always reaches the edge (no background peeking out), and
    - an **inner** path that walks the panel's rounded-rect perimeter and is
      inset by a *thickness* at each point. The thickness tapers from
      ``corner_width`` at the corners up to ``mid_width`` at the middle of each
      edge, plus a smooth seeded ripple of amplitude ``roughness`` — so the
      stroke swells and thins continuously like a pen pressed with varying force.
      Only the inner edge moves (the outer stays on the panel boundary), and it
      is plain vector geometry, so the result is crisp with no displacement-map
      aliasing.

    ``frequency`` sets how many thickness undulations run around the perimeter;
    ``seed`` picks the (deterministic) ripple. NOTE: panel.html mirrors this math
    in JS for live slider updates — keep the two in sync.
    """
    r_outer = min(radius, width / 2, height / 2)
    wc = min(corner_width, width / 2, height / 2)
    wm = mid_width

    # ---- outer: clean oversized rounded rect (panel overflow clips it) --------
    o_r = r_outer + overscan
    x0, y0, x1, y1 = -overscan, -overscan, width + overscan, height + overscan
    rr = _n(o_r)
    oxa, oxb = _n(x0 + o_r), _n(x1 - o_r)
    oya, oyb = _n(y0 + o_r), _n(y1 - o_r)
    sox0, soy0, sox1, soy1 = _n(x0), _n(y0), _n(x1), _n(y1)
    outer = (
        f"M {oxa},{soy0} L {oxb},{soy0} A {rr},{rr} 0 0 1 {sox1},{oya} "
        f"L {sox1},{oyb} A {rr},{rr} 0 0 1 {oxb},{soy1} "
        f"L {oxa},{soy1} A {rr},{rr} 0 0 1 {sox0},{oyb} "
        f"L {sox0},{oya} A {rr},{rr} 0 0 1 {oxa},{soy0} Z"
    )

    inner = _inner_path(width, height, r_outer, wm, wc, roughness, seed, frequency)
    return f"{outer} {inner}"


def _inner_path(
    width: float,
    height: float,
    r: float,
    wm: float,
    wc: float,
    roughness: float,
    seed: float,
    frequency: float,
) -> str:
    """Inner contour: the rounded-rect perimeter pushed inward by the thickness."""
    st_h = max(0.0, width - 2 * r)  # straight horizontal-edge length
    st_v = max(0.0, height - 2 * r)  # straight vertical-edge length
    arc = (math.pi / 2) * r
    # (kind, length); kinds: lt/lr/lb/ll = line edges, a* = corner arcs.
    segs = [
        ("lt", st_h),
        ("atr", arc),
        ("lr", st_v),
        ("abr", arc),
        ("lb", st_h),
        ("abl", arc),
        ("ll", st_v),
        ("atl", arc),
    ]
    perimeter = sum(length for _, length in segs)
    if perimeter <= 0:
        return ""

    bump = wm - wc  # extra thickness at edge midpoints
    max_w = min(width, height) / 2

    # Smooth seeded ripple, periodic over the loop (integer harmonics).
    freq = max(1.0, frequency)
    harms = [round(freq), round(2 * freq + 1), round(3 * freq + 2)]
    amps = [1.0, 0.5, 0.3]
    amp_sum = sum(amps)
    phases = [2 * math.pi * _hash01((seed + 1) * 1.3 + (i + 1) * 8.7) for i in range(3)]

    def ripple(t: float) -> float:
        v = sum(
            a * math.sin(2 * math.pi * h * t + p)
            for a, h, p in zip(amps, harms, phases, strict=True)
        )
        return v / amp_sum

    def at(s: float) -> tuple[float, float, float, float, float]:
        """Map perimeter arclength s -> (x, y, inward_nx, inward_ny, base_width)."""
        acc = 0.0
        kind, length = segs[-1], 0.0  # fallback (shouldn't trigger)
        u = 0.0
        for k, length in segs:
            if length > 0 and s < acc + length:
                kind, u = k, (s - acc) / length
                break
            acc += length
        else:
            kind = segs[-1][0]
            u = 1.0
        edge_base = wc + bump * math.sin(math.pi * u)
        if kind == "lt":  # top, left->right; inward = down
            return r + st_h * u, 0.0, 0.0, 1.0, edge_base
        if kind == "lr":  # right, top->bottom; inward = left
            return width, r + st_v * u, -1.0, 0.0, edge_base
        if kind == "lb":  # bottom, right->left; inward = up
            return width - r - st_h * u, height, 0.0, -1.0, edge_base
        if kind == "ll":  # left, bottom->top; inward = right
            return 0.0, height - r - st_v * u, 1.0, 0.0, edge_base
        # corner arcs: inward normal points to the arc center; base = corner width
        if kind == "atr":
            cx, cy, a0 = width - r, r, -math.pi / 2
        elif kind == "abr":
            cx, cy, a0 = width - r, height - r, 0.0
        elif kind == "abl":
            cx, cy, a0 = r, height - r, math.pi / 2
        else:  # atl
            cx, cy, a0 = r, r, math.pi
        ang = a0 + (math.pi / 2) * u
        cos_a, sin_a = math.cos(ang), math.sin(ang)
        return cx + r * cos_a, cy + r * sin_a, -cos_a, -sin_a, wc

    n = max(48, min(2000, round(perimeter / 3)))
    coords: list[str] = []
    for i in range(n):
        s = perimeter * i / n
        x, y, nx, ny, base = at(s)
        w = base + roughness * ripple(i / n)
        w = min(max(0.0, w), max_w)
        coords.append(f"{_n(x + nx * w)},{_n(y + ny * w)}")

    return "M " + coords[0] + " L " + " L ".join(coords[1:]) + " Z"
