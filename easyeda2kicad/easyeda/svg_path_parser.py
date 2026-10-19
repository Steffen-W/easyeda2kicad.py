"""SVG path data and point lists of EasyEDA shapes.

EasyEDA stores outlines as SVG path data
(https://www.w3.org/TR/SVG11/paths.html) and polylines as point lists in the
syntax of the SVG "points" attribute. parse_svg_path() turns path data into
subpaths of lines, cubic Bézier curves and elliptical arcs in absolute
coordinates, which all shape converters build on.
"""

from __future__ import annotations

# Global imports
import math
import re
from dataclasses import dataclass, field
from typing import Iterator, Union

Point = tuple[float, float]

# Line segments per cubic Bézier curve when it is flattened
BEZIER_SEGMENTS = 8
# Maximum angle per line segment when an arc is flattened
_ARC_SEGMENT_DEG = 10.0
# Largest distance by which an arc may deviate from the circular arc through
# its start, middle and end point, or from its chord, to be drawn as that
_ARC_TOLERANCE = 0.1
# Points per arc at which that deviation is measured
_ARC_SAMPLES = 16


@dataclass
class SvgArcGeometry:
    """Center parametrization of an SVG elliptical arc (angles in radians)."""

    center_x: float
    center_y: float
    radius_x: float
    radius_y: float
    x_axis_rotation: float
    start_angle: float
    sweep_angle: float  # signed: positive in the direction of increasing angle

    def point(self, fraction: float) -> Point:
        """Point at the given fraction (0 = start, 1 = end) of the arc."""
        theta = self.start_angle + self.sweep_angle * fraction
        cos_phi = math.cos(self.x_axis_rotation)
        sin_phi = math.sin(self.x_axis_rotation)
        lx = self.radius_x * math.cos(theta)
        ly = self.radius_y * math.sin(theta)
        return (
            cos_phi * lx - sin_phi * ly + self.center_x,
            sin_phi * lx + cos_phi * ly + self.center_y,
        )

    def circle(self) -> tuple[Point, float] | None:
        """Center and signed sweep angle of the circular arc through the start,
        middle and end point; None if that circular arc deviates from this
        arc by more than _ARC_TOLERANCE."""
        if self.radius_x == self.radius_y:
            return (self.center_x, self.center_y), self.sweep_angle
        start, middle, end = self.point(0.0), self.point(0.5), self.point(1.0)
        center = _circumcenter(start, middle, end)
        if center is None:
            return None
        radius = math.dist(center, start)
        if any(
            abs(math.dist(center, self.point(i / _ARC_SAMPLES)) - radius)
            > _ARC_TOLERANCE
            for i in range(1, _ARC_SAMPLES)
        ):
            return None
        sweep = (_angle(center, end) - _angle(center, start)) % math.tau
        return center, sweep if self.sweep_angle > 0 else sweep - math.tau

    def extremes(self) -> list[Point]:
        """Points of the arc farthest in -x, +x, -y and +y direction that lie
        between its start and end."""
        cos_phi = math.cos(self.x_axis_rotation)
        sin_phi = math.sin(self.x_axis_rotation)
        theta_x = math.atan2(-self.radius_y * sin_phi, self.radius_x * cos_phi)
        theta_y = math.atan2(self.radius_y * cos_phi, self.radius_x * sin_phi)
        sweep = abs(self.sweep_angle)
        if not sweep:
            return []
        direction = 1 if self.sweep_angle > 0 else -1
        points = []
        for theta in (theta_x, theta_x + math.pi, theta_y, theta_y + math.pi):
            # Angle from the start in the direction the arc runs
            offset = (theta - self.start_angle) * direction % math.tau
            if offset <= sweep:
                points.append(self.point(offset / sweep))
        return points


def _angle(center: Point, point: Point) -> float:
    return math.atan2(point[1] - center[1], point[0] - center[0])


def _circumcenter(a: Point, b: Point, c: Point) -> Point | None:
    """Center of the circle through three points; None if they are collinear."""
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if d == 0:
        return None
    a2, b2, c2 = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    return (
        (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d,
        (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d,
    )


def is_zero_radius(radius: float) -> bool:
    """Whether an arc radius counts as zero; this includes radii whose square
    underflows, which the center computation divides by."""
    return radius * radius == 0


def svg_arc_geometry(
    sx: float,
    sy: float,
    ex: float,
    ey: float,
    rx: float,
    ry: float,
    x_rotation_deg: float,
    large_arc: bool,
    sweep: bool,
) -> SvgArcGeometry:
    """Convert an SVG arc from endpoint to center parametrization.

    Implements the SVG spec endpoint-to-center conversion
    (https://www.w3.org/TR/SVG11/implnote.html#ArcConversionEndpointToCenter).
    All coordinates are in the caller's coordinate system (SVG or KiCad).
    Raises ValueError for a zero radius (see is_zero_radius): SVG draws such
    an arc as a straight line, which has no center.
    """
    if is_zero_radius(rx) or is_zero_radius(ry):
        raise ValueError(f"arc with zero radius (rx={rx}, ry={ry})")
    phi = math.radians(x_rotation_deg % 360.0)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    # Step 1: rotate midpoint of chord into ellipse-local frame
    dx2 = (sx - ex) / 2.0
    dy2 = (sy - ey) / 2.0
    x1 = cos_phi * dx2 + sin_phi * dy2
    y1 = -sin_phi * dx2 + cos_phi * dy2

    # Ensure radii are positive and large enough
    rx = abs(rx)
    ry = abs(ry)
    rx_sq = rx * rx
    ry_sq = ry * ry
    x1_sq = x1 * x1
    y1_sq = y1 * y1
    radii_scale = x1_sq / rx_sq + y1_sq / ry_sq
    if radii_scale > 1.0:
        scale = math.sqrt(radii_scale)
        rx *= scale
        ry *= scale
        rx_sq = rx * rx
        ry_sq = ry * ry

    # Step 2: compute center in ellipse-local frame
    sign = -1.0 if large_arc == sweep else 1.0
    num = max(0.0, rx_sq * ry_sq - rx_sq * y1_sq - ry_sq * x1_sq)
    den = rx_sq * y1_sq + ry_sq * x1_sq
    coef = sign * math.sqrt(num / den) if den > 0 else 0.0
    cx1 = coef * (rx * y1 / ry)
    cy1 = coef * -(ry * x1 / rx)

    # Step 3: center in SVG frame
    cx = cos_phi * cx1 - sin_phi * cy1 + (sx + ex) / 2.0
    cy = sin_phi * cx1 + cos_phi * cy1 + (sy + ey) / 2.0

    # Step 4: start angle and angular extent
    def angle_between(ux: float, uy: float, vx: float, vy: float) -> float:
        n = math.hypot(ux, uy) * math.hypot(vx, vy)
        if n == 0:
            return 0.0
        cos_val = max(-1.0, min(1.0, (ux * vx + uy * vy) / n))
        a = math.acos(cos_val)
        if ux * vy - uy * vx < 0:
            a = -a
        return a

    ux = (x1 - cx1) / rx
    uy = (y1 - cy1) / ry
    vx = (-x1 - cx1) / rx
    vy = (-y1 - cy1) / ry

    theta1 = angle_between(1.0, 0.0, ux, uy)
    d_theta = angle_between(ux, uy, vx, vy)

    if not sweep and d_theta > 0:
        d_theta -= 2 * math.pi
    elif sweep and d_theta < 0:
        d_theta += 2 * math.pi
    elif d_theta == 0 and large_arc:
        # End and start coincide within float precision: the full ellipse
        d_theta = 2 * math.pi if sweep else -2 * math.pi

    return SvgArcGeometry(
        center_x=cx,
        center_y=cy,
        radius_x=rx,
        radius_y=ry,
        x_axis_rotation=phi,
        start_angle=theta1,
        sweep_angle=d_theta,
    )


def cubic_bezier_point(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    """Point at parameter t (0 = p0, 1 = p3) of a cubic Bézier curve."""
    a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
    return (
        a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
        a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
    )


def _cubic_bezier_extremes(p0: Point, p1: Point, p2: Point, p3: Point) -> list[Point]:
    """Points of a cubic Bézier curve between its ends where it turns in x or
    in y direction."""
    points = []
    for axis in (0, 1):
        # B'(t) / 3 = a t² + b t + c
        a = -p0[axis] + 3 * p1[axis] - 3 * p2[axis] + p3[axis]
        b = 2 * (p0[axis] - 2 * p1[axis] + p2[axis])
        c = p1[axis] - p0[axis]
        if a == 0:
            roots = [-c / b] if b else []
        else:
            discriminant = b * b - 4 * a * c
            if discriminant < 0:
                continue
            root = math.sqrt(discriminant)
            roots = [(-b - root) / (2 * a), (-b + root) / (2 * a)]
        points += [cubic_bezier_point(p0, p1, p2, p3, t) for t in roots if 0 < t < 1]
    return points


@dataclass(frozen=True)
class SvgLine:
    end: Point


@dataclass(frozen=True)
class SvgCubic:
    control1: Point
    control2: Point
    end: Point


@dataclass(frozen=True)
class SvgArc:
    radius_x: float  # > 0
    radius_y: float  # > 0
    x_axis_rotation: float  # degrees
    large_arc: bool
    sweep: bool
    end: Point

    def is_straight(self, start: Point) -> bool:
        """Whether the arc from start stays within _ARC_TOLERANCE of its chord,
        as arcs with a huge radius compared to the chord do."""
        arc = self.geometry(start)
        (sx, sy), (ex, ey) = start, self.end
        chord = math.hypot(ex - sx, ey - sy)
        return chord > 0 and all(
            abs((ex - sx) * (y - sy) - (ey - sy) * (x - sx)) / chord <= _ARC_TOLERANCE
            for x, y in (arc.point(i / _ARC_SAMPLES) for i in range(1, _ARC_SAMPLES))
        )

    def geometry(self, start: Point) -> SvgArcGeometry:
        return svg_arc_geometry(
            sx=start[0],
            sy=start[1],
            ex=self.end[0],
            ey=self.end[1],
            rx=self.radius_x,
            ry=self.radius_y,
            x_rotation_deg=self.x_axis_rotation,
            large_arc=self.large_arc,
            sweep=self.sweep,
        )


SvgSegment = Union[SvgLine, SvgCubic, SvgArc]


@dataclass
class SvgSubpath:
    start: Point
    segments: list[SvgSegment] = field(default_factory=list)
    closed: bool = False

    def walk(self) -> Iterator[tuple[Point, SvgSegment]]:
        """Each segment together with its start point."""
        current = self.start
        for segment in self.segments:
            yield current, segment
            current = segment.end

    def flatten(self) -> list[Point]:
        """Points along the subpath, with curves and arcs approximated by
        line segments (at most _ARC_SEGMENT_DEG per arc segment)."""
        points = [self.start]
        for start, segment in self.walk():
            if isinstance(segment, SvgArc):
                arc = segment.geometry(start)
                # Rounding keeps float noise (90.0000001°) from adding a segment
                steps = math.ceil(
                    round(abs(math.degrees(arc.sweep_angle)) / _ARC_SEGMENT_DEG, 6)
                )
                points += [arc.point(i / steps) for i in range(1, steps)]
            elif isinstance(segment, SvgCubic):
                points += [
                    cubic_bezier_point(
                        start,
                        segment.control1,
                        segment.control2,
                        segment.end,
                        i / BEZIER_SEGMENTS,
                    )
                    for i in range(1, BEZIER_SEGMENTS)
                ]
            points.append(segment.end)
        return points

    def extremes(self) -> list[Point]:
        """Start, segment ends and the points where curves and arcs turn in x
        or y direction; their bounding box is that of the subpath."""
        points = [self.start]
        for start, segment in self.walk():
            if isinstance(segment, SvgArc):
                points += segment.geometry(start).extremes()
            elif isinstance(segment, SvgCubic):
                points += _cubic_bezier_extremes(
                    start, segment.control1, segment.control2, segment.end
                )
            points.append(segment.end)
        return points


# Numbers per coordinate set of each path command
_COMMAND_ARGS = {
    "M": 2,
    "L": 2,
    "H": 1,
    "V": 1,
    "C": 6,
    "S": 4,
    "Q": 4,
    "T": 2,
    "A": 7,
    "Z": 0,
}
# Splits path data before every letter except the exponent e/E of a number
_COMMAND_RE = re.compile(r"(?=[A-DF-Za-df-z])")
# SVG number; a sign or second decimal point starts the next one ("10-5", "1.5.5")
_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
# Whitespace with at most one comma, as allowed between two numbers
_SEPARATOR_RE = re.compile(r"\s*,?\s*")


def parse_svg_numbers(text: str) -> list[float]:
    """Numbers separated as in SVG: by whitespace with at most one comma, or
    by a sign or second decimal point that starts the next number ("10-5",
    ".5.5").

    Raises ValueError for any other text and for non-finite numbers.
    """
    matches = list(_NUMBER_RE.finditer(text))
    if not matches:
        outside = text
    else:
        outside = text[: matches[0].start()] + text[matches[-1].end() :]
    separators = [text[a.end() : b.start()] for a, b in zip(matches, matches[1:])]
    if outside.strip() or not all(map(_SEPARATOR_RE.fullmatch, separators)):
        raise ValueError(f"invalid number in {text.strip()!r}")
    numbers = [float(m.group()) for m in matches]
    if not all(math.isfinite(n) for n in numbers):
        raise ValueError(f"non-finite number in {text.strip()!r}")
    return numbers


def parse_svg_points(text: str) -> list[Point]:
    """Point list "x1 y1 x2 y2 ..." as in the SVG points attribute.

    Raises ValueError for malformed numbers and an odd number of coordinates.
    """
    numbers = parse_svg_numbers(text)
    if len(numbers) % 2:
        raise ValueError(f"odd number of coordinates ({len(numbers)})")
    return list(zip(numbers[::2], numbers[1::2]))


def _reflect(control: Point | None, current: Point) -> Point:
    """First control point of a smooth curve (S, T): the previous control
    point mirrored at the current point, or the current point itself."""
    if control is None:
        return current
    return (2 * current[0] - control[0], 2 * current[1] - control[1])


def _point(args: list[float], index: int, origin: Point) -> Point:
    return (origin[0] + args[index], origin[1] + args[index + 1])


def parse_svg_path(path: str) -> list[SvgSubpath]:
    """Parse SVG path data into subpaths in absolute coordinates.

    Supports all path commands, absolute and relative. As drawn by SVG, H and
    V become lines, quadratic curves become cubic ones, arcs with a zero
    radius or within tolerance of their chord become lines, arcs ending at
    their start are omitted and Z adds the closing line if the subpath does
    not end at its start. Raises ValueError for malformed path data.
    """
    subpaths: list[SvgSubpath] = []
    current: Point = (0.0, 0.0)
    # Control points a following S or T command mirrors
    cubic_control: Point | None = None
    quadratic_control: Point | None = None

    for token in _COMMAND_RE.split(path):
        token = token.strip()
        if not token:
            continue
        cmd, command = token[0], token[0].upper()
        if command not in _COMMAND_ARGS:
            raise ValueError(f"path command '{cmd}' is not supported")
        try:
            numbers = parse_svg_numbers(token[1:])
        except ValueError as e:
            raise ValueError(f"'{cmd}': {e}") from None
        if command != "M" and not subpaths:
            raise ValueError("path must start with M")

        count = _COMMAND_ARGS[command]
        if not count:
            if numbers:
                raise ValueError(f"'{cmd}' takes no arguments")
            subpath = subpaths[-1]
            if current != subpath.start:
                subpath.segments.append(SvgLine(subpath.start))
            subpath.closed = True
            current = subpath.start
            cubic_control = quadratic_control = None
            continue
        if not numbers or len(numbers) % count:
            raise ValueError(
                f"'{cmd}' needs a multiple of {count} numbers, got {len(numbers)}"
            )
        for i in range(0, len(numbers), count):
            args = numbers[i : i + count]
            origin = current if cmd.islower() else (0.0, 0.0)
            next_cubic_control = next_quadratic_control = None
            segment: SvgSegment
            if command == "M" and i == 0:
                current = _point(args, 0, origin)
                subpaths.append(SvgSubpath(start=current))
                cubic_control = quadratic_control = None
                continue
            if command in "ML":  # coordinate pairs after the first one of M are lines
                segment = SvgLine(_point(args, 0, origin))
            elif command == "H":
                segment = SvgLine((origin[0] + args[0], current[1]))
            elif command == "V":
                segment = SvgLine((current[0], origin[1] + args[0]))
            elif command in "CS":
                if command == "C":
                    control1 = _point(args, 0, origin)
                    args = args[2:]
                else:
                    control1 = _reflect(cubic_control, current)
                next_cubic_control = _point(args, 0, origin)
                segment = SvgCubic(
                    control1, next_cubic_control, _point(args, 2, origin)
                )
            elif command in "QT":
                if command == "Q":
                    next_quadratic_control = _point(args, 0, origin)
                    args = args[2:]
                else:
                    next_quadratic_control = _reflect(quadratic_control, current)
                control = next_quadratic_control
                end = _point(args, 0, origin)
                # Degree elevation: quadratic → cubic
                segment = SvgCubic(
                    (
                        current[0] + 2 / 3 * (control[0] - current[0]),
                        current[1] + 2 / 3 * (control[1] - current[1]),
                    ),
                    (
                        end[0] + 2 / 3 * (control[0] - end[0]),
                        end[1] + 2 / 3 * (control[1] - end[1]),
                    ),
                    end,
                )
            else:  # A
                if not {args[3], args[4]} <= {0.0, 1.0}:
                    raise ValueError("arc flags must be 0 or 1")
                end = _point(args, 5, origin)
                if end == current:
                    cubic_control = quadratic_control = None
                    continue
                if is_zero_radius(args[0]) or is_zero_radius(args[1]):
                    segment = SvgLine(end)
                else:
                    segment = SvgArc(
                        radius_x=abs(args[0]),
                        radius_y=abs(args[1]),
                        x_axis_rotation=args[2],
                        large_arc=bool(args[3]),
                        sweep=bool(args[4]),
                        end=end,
                    )
                    if segment.is_straight(current):
                        segment = SvgLine(end)

            # Drawing on after Z starts a new subpath at the closed one's start
            if subpaths[-1].closed:
                subpaths.append(SvgSubpath(start=current))
            subpaths[-1].segments.append(segment)
            current = segment.end
            cubic_control, quadratic_control = (
                next_cubic_control,
                next_quadratic_control,
            )

    return subpaths
