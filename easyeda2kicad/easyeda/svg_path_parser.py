from __future__ import annotations

# Global imports
import logging
import math
import re
from dataclasses import dataclass


@dataclass
class SvgPathMoveTo:
    start_x: float
    start_y: float


@dataclass
class SvgPathLineTo:
    pos_x: float
    pos_y: float


@dataclass
class SvgPathEllipticalArc:
    radius_x: float
    radius_y: float
    x_axis_rotation: float
    flag_large_arc: bool
    flag_sweep: bool
    end_x: float
    end_y: float


@dataclass
class SvgPathClosePath:
    pass


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

    def point(self, fraction: float) -> tuple[float, float]:
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
    Raises ValueError for a zero radius: SVG draws such an arc as a straight
    line, which has no center.
    """
    if rx == 0 or ry == 0:
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

    return SvgArcGeometry(
        center_x=cx,
        center_y=cy,
        radius_x=rx,
        radius_y=ry,
        x_axis_rotation=phi,
        start_angle=theta1,
        sweep_angle=d_theta,
    )


# Numbers per coordinate set of the absolute SVG path commands EasyEDA uses
_COMMAND_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "Q": 4, "A": 7, "Z": 0}
# Splits a path before every letter except the exponent e/E of a number
_COMMAND_RE = re.compile(r"(?=[A-DF-Za-df-z])")
# SVG number; a sign or second decimal point starts the next one ("10-5", "1.5.5")
_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def split_svg_path(svg_path: str) -> list[tuple[str, str]]:
    """Split an SVG path into (command letter, argument text) pairs."""
    tokens = (token.strip() for token in _COMMAND_RE.split(svg_path))
    return [(token[0], token[1:]) for token in tokens if token]


def parse_svg_args(cmd: str, text: str) -> list[list[float]]:
    """Parse the arguments of an absolute SVG path command into coordinate sets.

    Numbers follow SVG syntax, so "10-5" are two numbers. Raises ValueError for
    other (e.g. relative) commands, invalid or non-finite numbers, incomplete
    coordinate sets and arc flags other than 0 or 1.
    """
    if cmd not in _COMMAND_ARGS:
        raise ValueError(f"path command '{cmd}' is not supported")
    if _NUMBER_RE.sub("", text).strip(" ,\t\n\r"):
        raise ValueError(f"'{cmd}' has an invalid number: {text.strip()!r}")
    numbers = [float(n) for n in _NUMBER_RE.findall(text)]
    if not all(math.isfinite(n) for n in numbers):
        raise ValueError(f"'{cmd}' has a non-finite number")
    count = _COMMAND_ARGS[cmd]
    if not count:
        if numbers:
            raise ValueError(f"'{cmd}' takes no arguments")
        return []
    if not numbers or len(numbers) % count:
        raise ValueError(
            f"'{cmd}' needs a multiple of {count} numbers, got {len(numbers)}"
        )
    sets = [numbers[i : i + count] for i in range(0, len(numbers), count)]
    if cmd == "A" and not all({s[3], s[4]} <= {0.0, 1.0} for s in sets):
        raise ValueError("arc flags must be 0 or 1")
    return sets


def parse_svg_path(
    svg_path: str,
) -> list[SvgPathMoveTo | SvgPathEllipticalArc | SvgPathLineTo | SvgPathClosePath]:
    """Parse the absolute M, L, A and Z commands of an SVG path.

    Other and malformed commands are skipped with a warning.
    """
    parsed_path: list[
        SvgPathMoveTo | SvgPathEllipticalArc | SvgPathLineTo | SvgPathClosePath
    ] = []
    for cmd, text in split_svg_path(svg_path):
        try:
            if cmd not in "MLAZ":
                raise ValueError(f"path command '{cmd}' is not supported")
            sets = parse_svg_args(cmd, text)
        except ValueError as e:
            logging.warning(f"Skipping SVG path command: {e}")
            continue
        if cmd == "Z":
            parsed_path.append(SvgPathClosePath())
        for i, s in enumerate(sets):
            if cmd == "A":
                parsed_path.append(
                    SvgPathEllipticalArc(
                        radius_x=s[0],
                        radius_y=s[1],
                        x_axis_rotation=s[2],
                        flag_large_arc=bool(s[3]),
                        flag_sweep=bool(s[4]),
                        end_x=s[5],
                        end_y=s[6],
                    )
                )
            # Coordinate pairs after the first one of M are line-tos
            elif cmd == "M" and i == 0:
                parsed_path.append(SvgPathMoveTo(start_x=s[0], start_y=s[1]))
            else:
                parsed_path.append(SvgPathLineTo(pos_x=s[0], pos_y=s[1]))
    return parsed_path


if __name__ == "__main__":
    path_1 = "M 400.067 299.929 A 4 3.9 0 1 1 408.032 299.934 L 5 6 5 7 Z"
    path_0 = "M 5 -6 L -5 0 L 5 6 Z"
    path_2 = "M -5 3 L -2 0 L -5 -3"

    print(parse_svg_path(svg_path=path_0))
