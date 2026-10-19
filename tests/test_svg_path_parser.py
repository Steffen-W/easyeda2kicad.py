"""Unit tests for the SVG path data and point list parser."""

from __future__ import annotations

import math

import pytest

from easyeda2kicad.easyeda.svg_path_parser import (
    BEZIER_SEGMENTS,
    Point,
    SvgArc,
    SvgArcGeometry,
    SvgCubic,
    SvgLine,
    SvgSubpath,
    parse_svg_font_size,
    parse_svg_numbers,
    parse_svg_path,
    parse_svg_points,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10-5", [10.0, -5.0]),
        (".5.5 1e1,2", [0.5, 0.5, 10.0, 2.0]),
        (" ", []),
    ],
)
def test_numbers_follow_svg_syntax(text: str, expected: list[float]) -> None:
    assert parse_svg_numbers(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("1# 2", "invalid number"),
        ("1,,2", "invalid number"),
        (",1 2", "invalid number"),
        ("1 2,", "invalid number"),
        ("1e999", "non-finite"),
    ],
)
def test_malformed_numbers_are_rejected(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_svg_numbers(text)


def test_points() -> None:
    assert parse_svg_points("1 2,3-4") == [(1.0, 2.0), (3.0, -4.0)]
    with pytest.raises(ValueError, match="odd number of coordinates"):
        parse_svg_points("1 2 3")


def test_lines_and_close() -> None:
    assert parse_svg_path("M 0 0 L 10 0 H 10 V 5 Z") == [
        SvgSubpath(
            start=(0.0, 0.0),
            segments=[
                SvgLine((10.0, 0.0)),
                SvgLine((10.0, 0.0)),
                SvgLine((10.0, 5.0)),
                SvgLine((0.0, 0.0)),
            ],
            closed=True,
        )
    ]


def test_relative_commands_match_absolute_ones() -> None:
    relative = parse_svg_path(
        "m 1 1 2 0 h 2 v 2 c 1 0 1 1 0 1 q -1 0 -1 -1 a 1 1 0 0 1 -1 -1 z"
    )
    absolute = parse_svg_path(
        "M 1 1 L 3 1 H 5 V 3 C 6 3 6 4 5 4 Q 4 4 4 3 A 1 1 0 0 1 3 2 Z"
    )
    assert relative == absolute


def test_extra_move_pairs_are_lines() -> None:
    (subpath,) = parse_svg_path("M 0 0 5 5")
    assert subpath.segments == [SvgLine((5.0, 5.0))]


def test_close_adds_no_line_when_already_at_start() -> None:
    (subpath,) = parse_svg_path("M 0 0 L 5 0 L 0 0 Z")
    assert subpath.segments == [SvgLine((5.0, 0.0)), SvgLine((0.0, 0.0))]
    assert subpath.closed


def test_drawing_after_close_starts_a_new_subpath_at_the_start() -> None:
    first, second = parse_svg_path("M 1 1 L 5 1 Z L 1 5")
    assert first.closed
    assert second == SvgSubpath(start=(1.0, 1.0), segments=[SvgLine((1.0, 5.0))])


def test_quadratic_curves_are_elevated_to_cubic() -> None:
    (subpath,) = parse_svg_path("M 0 0 Q 6 6 12 0")
    assert subpath.segments == [SvgCubic((4.0, 4.0), (8.0, 4.0), (12.0, 0.0))]


def test_smooth_curves_mirror_the_previous_control_point() -> None:
    (smooth,) = parse_svg_path("M 0 0 C 0 5 5 5 5 0 S 10 -5 10 0 Q 12 3 14 0 T 18 0")
    (explicit,) = parse_svg_path(
        "M 0 0 C 0 5 5 5 5 0 C 5 -5 10 -5 10 0 Q 12 3 14 0 Q 16 -3 18 0"
    )
    assert smooth == explicit


def test_smooth_curve_without_previous_curve_starts_at_current_point() -> None:
    (smooth,) = parse_svg_path("M 0 0 L 1 0 S 3 2 4 0")
    assert smooth.segments[1] == SvgCubic((1.0, 0.0), (3.0, 2.0), (4.0, 0.0))


def test_arc() -> None:
    (subpath,) = parse_svg_path("M400-300A3 3 0 0 1 410-300")
    assert subpath.segments == [
        SvgArc(
            radius_x=3,
            radius_y=3,
            x_axis_rotation=0,
            large_arc=False,
            sweep=True,
            end=(410.0, -300.0),
        )
    ]


def test_arc_ending_at_its_start_is_omitted() -> None:
    (subpath,) = parse_svg_path("M 0 0 S 1 1 2 0 L 5 0 A 3 3 0 1 1 5 0 S 7 1 8 0")
    assert len(subpath.segments) == 3
    assert subpath.segments[2] == SvgCubic((5.0, 0.0), (7.0, 1.0), (8.0, 0.0))


@pytest.mark.parametrize("radii", ["0 5", "1e-200 5", "1e8 1e8", "1e20 1e20"])
def test_arc_with_zero_radius_or_within_tolerance_of_its_chord_is_a_line(
    radii: str,
) -> None:
    (subpath,) = parse_svg_path(f"M 0 0 A {radii} 0 0 1 10 0")
    assert subpath.segments == [SvgLine((10.0, 0.0))]


@pytest.mark.parametrize(("sweep", "expected"), [("1", math.tau), ("0", -math.tau)])
def test_large_arc_ending_at_its_start_within_float_precision_is_a_full_ellipse(
    sweep: str, expected: float
) -> None:
    (subpath,) = parse_svg_path(f"M 0 0 A 10 10 0 1 {sweep} 1e-15 0")
    ((start, arc),) = subpath.walk()
    assert isinstance(arc, SvgArc)
    assert arc.geometry(start).sweep_angle == pytest.approx(expected)


def test_omitted_arc_after_close_starts_no_subpath() -> None:
    (subpath,) = parse_svg_path("M 0 0 L 10 0 L 10 10 Z A 5 5 0 0 1 0 0")
    assert subpath.closed


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("L 1 2", "path must start with M"),
        ("M 0 0 X 1", "'X' is not supported"),
        ("M 0 0 L 1# 2", "'L': invalid number"),
        ("M 0 0 L 1e999 2", "'L': non-finite"),
        ("M 0 0 L 1 2 3", "multiple of 2 numbers, got 3"),
        ("M 0 0 L", "multiple of 2 numbers, got 0"),
        ("M 0 0 L 1 1 Z 1", "takes no arguments"),
        ("M 0 0 A 1 1 0 -1 1 5 5", "flags must be 0 or 1"),
        ("M 0 0 A 1 1 0 0 0.5 5 5", "flags must be 0 or 1"),
    ],
)
def test_malformed_paths_are_rejected(path: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_svg_path(path)


def test_empty_path_has_no_subpaths() -> None:
    assert parse_svg_path(" ") == []


def test_flatten_follows_arcs_and_curves() -> None:
    (subpath,) = parse_svg_path("M 10 0 A 10 10 0 0 1 0 10 C 0 15 5 20 10 20")
    points = subpath.flatten()
    # 90° arc in 10° steps, then the curve in BEZIER_SEGMENTS steps
    assert len(points) == 1 + 9 + BEZIER_SEGMENTS
    assert all(math.hypot(x, y) == pytest.approx(10) for x, y in points[:10])
    assert points[-1] == (10.0, 20.0)
    assert points[10 + BEZIER_SEGMENTS // 2 - 1] == pytest.approx((3.125, 16.875))


def _bounds(points: list[Point]) -> tuple[float, float, float, float]:
    xs, ys = [x for x, _ in points], [y for _, y in points]
    return min(xs), min(ys), max(xs), max(ys)


@pytest.mark.parametrize(
    ("path", "bounds"),
    [
        ("M 0 0 C 0 10 10 10 10 0", (0, 0, 10, 7.5)),
        ("M 10 0 A 10 10 0 1 0 0 10", (-10, -10, 10, 10)),
        # Ellipse rx=2, ry=1 rotated by 90°: extent 1 in x and 2 in y
        ("M 0 -2 A 2 1 90 0 1 0 2", (0, -2, 1, 2)),
    ],
)
def test_extremes_bound_the_drawn_path(
    path: str, bounds: tuple[float, float, float, float]
) -> None:
    (subpath,) = parse_svg_path(path)
    assert _bounds(subpath.extremes()) == pytest.approx(bounds)


def _arc(path: str) -> SvgArcGeometry:
    (subpath,) = parse_svg_path(path)
    ((start, arc),) = subpath.walk()
    assert isinstance(arc, SvgArc)
    return arc.geometry(start)


def test_circular_arc_keeps_its_geometry() -> None:
    circle = _arc("M 10 0 A 10 10 0 0 1 0 10").circle()
    assert circle is not None
    center, sweep = circle
    assert center == pytest.approx((0, 0))
    assert sweep == pytest.approx(math.pi / 2)


def test_nearly_circular_arc_becomes_the_circle_through_start_middle_end() -> None:
    arc = _arc("M -4 0 A 4 3.9 0 1 1 4 0")
    circle = arc.circle()
    assert circle is not None
    center, sweep = circle
    radius = math.dist(center, arc.point(0))
    assert math.dist(center, arc.point(0.5)) == pytest.approx(radius)
    assert math.dist(center, arc.point(1)) == pytest.approx(radius)
    assert sweep * arc.sweep_angle > 0


def test_elliptical_arc_has_no_circle() -> None:
    assert _arc("M 0 0 A 10 5 0 0 1 20 0").circle() is None


@pytest.mark.parametrize(
    ("text", "size"), [("9pt", 12.0), (" 12px ", 12.0), ("7.5", 7.5)]
)
def test_font_size_in_user_units(text: str, size: float) -> None:
    assert parse_svg_font_size(text) == pytest.approx(size)


@pytest.mark.parametrize("text", ["", "pt", "0pt", "-1", "9em", "9 9"])
def test_invalid_font_size_is_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse_svg_font_size(text)
