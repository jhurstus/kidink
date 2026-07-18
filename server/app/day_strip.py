"""View model for the day-of-week strip (spec §9).

Builds the seven Mon–Sun day panels for a target date: each panel's name,
whether it is "today" (the widened, taller panel with the excited art and the
date caption), its width, and the day's one or two event icons per the §9.2
per-kid selection: each kid's most-interesting candidate event, merged into
one icon when the kids agree, split by the torn-panel treatment when they
differ. Icon labels follow the event's own kid assignment (§8): only an event
belonging to a proper subset of the kids carries initials, so a shared event's
icon is always unlabeled — even next to a kid-specific icon.
"""

import math
import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from app.calendar import CalendarEvent
from app.config import Kid
from app.dates import week_of
from app.event_rows import (
    IconItem,
    KidBadge,
    assigned_kids,
    icon_item,
    icon_key,
    kid_badges,
)

# Panel display names, Monday..Sunday (week_of is Mon-first).
DAY_NAMES = [
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
]

# Strip geometry (px), mirrored by day_strip.css. Seven free-standing panels
# fill the strip exactly: the today panel is ~30% wider than the six others,
# panels are split by a small gutter with a wider one between the weekday and
# weekend blocks (the flex gap plus Saturday's extra margin):
#   6*205 + 268 + 5*5 + (5 + 10) = 1538 = _STRIP_W.
_STRIP_W = 1538
_PANEL_W = 205
_TODAY_PANEL_W = 268
_PANEL_GAP = 5
_WEEKEND_EXTRA_GAP = 10

# The today panel's comic-burst frame (§9.1): its solid 4px border gives way to
# a subtly spiky zigzag tracing the same outline — spike tips on the old
# border-box edge, notches cutting about _BURST_AMPLITUDE inward (each notch's
# depth jittered ±_BURST_JITTER for a hand-drawn feel), one jag about every
# _BURST_WAVELENGTH px. The outlines are computed here (CSS cannot repeat a
# jag) and handed to day_strip.css through the view model.
_TODAY_PANEL_H = 220  # mirrors .day-panel-today's height in day_strip.css
_BURST_WAVELENGTH = 15
_BURST_AMPLITUDE = 9
_BURST_JITTER = 0.15  # per-notch depth variation, as a fraction of the amplitude
_BURST_BORDER_W = 4  # matches the .day-panel border width
# A fixed seed keeps the jitter byte-reproducible (spec §3.4): the burst is
# frame decor, identical for every render date.
_BURST_SEED = 0x1DEA

type _Point = tuple[float, float]


def _burst_outline(
    w: float,
    h: float,
    amplitude: float,
    wavelength: float,
    jitter: float = 0.0,
    seed: int = 0,
) -> list[_Point]:
    """Vertices of a comic-burst zigzag tracing the ``w`` x ``h`` rect, clockwise
    from the top-left: spike tips on the rect edge, notches about ``amplitude``
    inward — each notch's depth varied ±``jitter`` (a fraction of the amplitude)
    by a ``seed``-fixed RNG, so the hand-drawn irregularity is reproducible.

    The envelope is the rect with its corners chamfered by about one wavelength
    of the path, and the zigzag is phased so each chamfer carries exactly one
    tip at its middle: a single spike pointing diagonally at each box corner,
    the same size as every other jag. (A tip on the box corner itself would
    stand between notches squeezed together by the corner's converging edges —
    a needle once the amplitude nears the half-wavelength, whose frame stroke
    then miters into a long whisker.) The segment junctions are all notches,
    pushed inward along their junction's bisector normal; each edge fits the
    whole even number of half-waves closest to its length between them."""
    rng = random.Random(seed)

    def depth() -> float:
        return amplitude * (1 + rng.uniform(-jitter, jitter))

    c = round(wavelength / 2 * math.sqrt(2))  # chamfer inset: ~one wave of cut
    octagon: list[_Point] = [
        (c, 0.0),
        (w - c, 0.0),
        (w, c),
        (w, h - c),
        (w - c, h),
        (c, h),
        (0.0, h - c),
        (0.0, c),
    ]
    # Inward normals per segment ((-uy, ux) of a clockwise segment, y down);
    # even segments are the rect edges, odd ones the corner chamfers.
    normals: list[_Point] = []
    for i, (sx, sy) in enumerate(octagon):
        ex, ey = octagon[(i + 1) % 8]
        length = math.hypot(ex - sx, ey - sy)
        normals.append(((sy - ey) / length, (ex - sx) / length))
    points: list[_Point] = []
    for i, (sx, sy) in enumerate(octagon):
        ex, ey = octagon[(i + 1) % 8]
        length = math.hypot(ex - sx, ey - sy)
        ux, uy = (ex - sx) / length, (ey - sy) / length
        nx, ny = normals[i]
        # The segment-start junction: a notch pushed along the bisector of the
        # two adjoining segments' normals.
        (qx, qy), d = normals[i - 1], depth()
        norm = math.hypot(qx + nx, qy + ny)
        points.append((sx + (qx + nx) / norm * d, sy + (qy + ny) / norm * d))
        if i % 2:  # chamfer: one centered tip — the corner's single spike
            points.append((sx + ux * length / 2, sy + uy * length / 2))
            continue
        halves = max(2, 2 * round(length / wavelength))  # even: notch to notch
        for k in range(1, halves):
            along = length * k / halves
            if k % 2:  # tip, on the rect edge
                points.append((sx + ux * along, sy + uy * along))
            else:
                d = depth()
                points.append((sx + ux * along + nx * d, sy + uy * along + ny * d))
    return points


def _inset_outline(points: list[_Point], inset: float) -> list[_Point]:
    """The closed polygon offset ``inset`` inward: every edge line shifts
    ``inset`` along its inward normal, and each vertex is re-intersected from
    its two shifted edges (a miter join) — so the offset flanks stay exactly
    parallel to the originals and the band between reads as an even stroke."""
    shifted: list[tuple[_Point, _Point]] = []
    for i, (px, py) in enumerate(points):
        qx, qy = points[(i + 1) % len(points)]
        dx, dy = qx - px, qy - py
        norm = math.hypot(dx, dy)
        shifted.append(((px - dy / norm * inset, py + dx / norm * inset), (dx, dy)))
    result: list[_Point] = []
    for i in range(len(points)):
        (ax, ay), (adx, ady) = shifted[i - 1]
        (bx, by), (bdx, bdy) = shifted[i]
        # Adjacent zigzag flanks are never parallel, so the cross can't be 0.
        s = ((bx - ax) * bdy - (by - ay) * bdx) / (adx * bdy - ady * bdx)
        result.append((ax + adx * s, ay + ady * s))
    return result


def _fmt(value: float) -> str:
    """A CSS-friendly coordinate: 2-decimal precision, no trailing zeros."""
    return f"{round(value, 2) + 0:g}"


def _css_polygon(points: list[_Point]) -> str:
    return "polygon(" + ", ".join(f"{_fmt(x)}px {_fmt(y)}px" for x, y in points) + ")"


def _css_ring(outer: list[_Point], inner: list[_Point]) -> str:
    """A CSS ``path()`` covering the band between two nested outlines: both
    loops as subpaths, the even-odd rule keeping only the area between them."""

    def loop(pts: list[_Point]) -> str:
        return "M" + "L".join(f"{_fmt(x)} {_fmt(y)}" for x, y in pts) + "Z"

    return f"path(evenodd, '{loop(outer)} {loop(inner)}')"


# The today panel's burst outlines, computed once (the panel size is fixed):
# the outer zigzag masks the panel's silhouette; the ring between it and its
# border-width-inset copy is the frame painted by the CSS ::after.
_TODAY_BURST_OUTER = _burst_outline(
    _TODAY_PANEL_W,
    _TODAY_PANEL_H,
    _BURST_AMPLITUDE,
    _BURST_WAVELENGTH,
    jitter=_BURST_JITTER,
    seed=_BURST_SEED,
)
_TODAY_BURST_CLIP = _css_polygon(_TODAY_BURST_OUTER)
_TODAY_BURST_RING = _css_ring(
    _TODAY_BURST_OUTER, _inset_outline(_TODAY_BURST_OUTER, _BURST_BORDER_W)
)

type StripIconRequest = tuple[IconItem, bool]
"""One §9.2 strip pick's image inputs: ``(item, excited)`` — the excited flag
asks for the today panel's amped-up art variant (§9.1)."""

# Structural stand-in for app.images.StripIconResolver (kept as a plain
# Callable so this view-model module needs no images import): a batch of
# (item, excited) requests -> a (icon_key, excited) -> URL-or-None mapping,
# resolved in one call so missing images can generate concurrently behind it.
# Keyed by the pair, not the key alone: a recurring event can land on today
# (excited) and another day (base) in the same render.
type StripIconResolver = Callable[
    [Sequence[StripIconRequest]], Mapping[tuple[str, bool], str | None]
]


def no_strip_icons(
    requests: Sequence[StripIconRequest],
) -> Mapping[tuple[str, bool], str | None]:
    """Default resolver: no icons — keeps the builder pure by default."""
    return {}


@dataclass(frozen=True)
class DayIcon:
    """One event icon in a day panel (§9.2)."""

    title: str
    """The event title: the icon's alt text and the §7.3 fallback-chip text."""

    icon_url: str | None
    """``None`` -> the template renders the fallback chip (§7.3)."""

    kids: list[KidBadge]
    """The event's own §8 kid badges: empty unless the event belongs to a
    proper subset of the configured kids — a shared event's icon is unlabeled
    even beside a kid-specific one (§9.2)."""


@dataclass(frozen=True)
class DayCell:
    """One day panel in the strip."""

    name: str
    iso: str
    is_today: bool
    width: int
    icons: list[DayIcon]
    """The day's one or two event icons (§9.2), in kid config order; empty for
    an event-less day."""


@dataclass(frozen=True)
class DayStrip:
    """The complete view model rendered by ``templates/modules/day_strip.html``."""

    week: list[DayCell]
    date_label: str

    burst_clip: str = _TODAY_BURST_CLIP
    """The today panel's comic-burst silhouette (§9.1) as a CSS ``polygon()``:
    the panel's ``clip-path``, set through ``--burst-clip``."""

    burst_ring: str = _TODAY_BURST_RING
    """The burst frame — the band between the silhouette and its border-width
    inward offset — as an even-odd CSS ``path()``: painted black over the
    panel's content by its ``::after``, set through ``--burst-ring``."""


def build_day_strip(
    target: date,
    events: Iterable[CalendarEvent] = (),
    kids: Sequence[Kid] = (),
    icon_resolver: StripIconResolver = no_strip_icons,
) -> DayStrip:
    """Build the full day-strip view model for the resolved render date ``target``.

    ``events`` are the week's expanded calendar events (see
    :func:`app.calendar.expand_events`); they are grouped by local day and each
    panel shows the §9.2 per-kid icon selection over its non-chore events.
    ``kids`` (config order, :class:`app.config.Kid`) drives both the per-kid
    candidacy and the icon badges. All seven days' icons are resolved through
    ``icon_resolver`` in a single batch — today's picks flagged excited so the
    today panel gets the amped-up art (§9.1) — so missing images can generate
    concurrently (see :data:`app.images.StripIconResolver`; the default
    resolves nothing, keeping the view model a pure function of its inputs).
    """
    by_day: dict[date, list[CalendarEvent]] = {}
    for event in events:
        by_day.setdefault(event.local_day, []).append(event)
    return DayStrip(
        week=_build_week_cells(week_of(target), target, by_day, kids, icon_resolver),
        date_label=_format_date_label(target),
    )


def _rank_key(event: CalendarEvent) -> tuple:
    """Candidate rank: ``interesting`` descending, ties broken by title ascending
    — a total order, so each pick is deterministic for a given day (spec §3.4)."""
    return (-event.overrides.interesting, event.title)


def _day_picks(
    day_events: list[CalendarEvent], kids: Sequence[Kid], *, single: bool = False
) -> list[CalendarEvent]:
    """The day's shown events per the §9.2 per-kid selection.

    A kid's candidates are the day's non-chore events that apply to them —
    shared, or assigned to them (see :func:`app.event_rows.assigned_kids`);
    their pick is the most-interesting candidate. Kids agreeing on one event
    share a single entry, so the result has 0..len(kids) entries in kid config
    order. With no kids configured the day degrades to one overall pick.

    When ``single`` (the today panel, §9.1, which never splits into a torn
    two-image cell), the picks collapse to just the one most-interesting
    candidate — the global top, which is always one of the per-kid picks — so
    the today cell shows exactly one image, carrying that event's own kid label.
    """
    candidates = [event for event in day_events if not event.is_chore]
    if not candidates:
        return []
    if not kids:
        return [min(candidates, key=_rank_key)]
    picks: list[CalendarEvent] = []
    for i in range(len(kids)):
        mine = [e for e in candidates if i in assigned_kids(e, kids)]
        if not mine:
            continue
        top = min(mine, key=_rank_key)
        if not any(top is pick for pick in picks):
            picks.append(top)
    if single and picks:
        return [min(picks, key=_rank_key)]
    return picks


def _build_week_cells(
    week: list[date],
    target: date,
    by_day: dict[date, list[CalendarEvent]],
    kids: Sequence[Kid],
    icon_resolver: StripIconResolver,
) -> list[DayCell]:
    """Build the seven ``DayCell``s for ``week`` (Mon..Sun), flagging ``target``.

    ``week`` must be the seven Mon–Sun dates (see ``dates.week_of``); ``by_day``
    maps each local day to its events. The days' picked events (§9.2) are
    resolved to icon URLs through one batched ``icon_resolver`` call, keyed by
    each event's ``icon_description`` (falling back to its title, §6.4/§7.1)
    plus the excited flag — set on today's picks only, selecting the today
    panel's amped-up art variant (§9.1).
    """
    # The today cell never splits into a torn two-image panel (§9.1), so its
    # picks collapse to the single most-interesting candidate; every other day
    # keeps the full 0..2 §9.2 selection.
    picks_by_day = [
        _day_picks(by_day.get(day, []), kids, single=(day == target)) for day in week
    ]
    icons = icon_resolver(
        [
            (icon_item(event), day == target)
            for day, picks in zip(week, picks_by_day, strict=True)
            for event in picks
        ]
    )
    cells: list[DayCell] = []
    for day, name, picks in zip(week, DAY_NAMES, picks_by_day, strict=True):
        is_today = day == target
        cells.append(
            DayCell(
                name=name,
                iso=day.isoformat(),
                is_today=is_today,
                width=_TODAY_PANEL_W if is_today else _PANEL_W,
                icons=[
                    DayIcon(
                        title=event.title,
                        icon_url=icons.get((icon_key(event), is_today)),
                        kids=kid_badges(event, kids),
                    )
                    for event in picks
                ],
            )
        )
    return cells


def _format_date_label(target: date) -> str:
    """Format the today-panel date, e.g. "June 3, 2026" (no leading zero)."""
    return f"{target:%B} {target.day}, {target.year}"
