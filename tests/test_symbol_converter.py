"""Unit tests for export_kicad_symbol converter functions."""

from __future__ import annotations

import dataclasses
import math

import pytest

from easyeda2kicad.easyeda.easyeda_importer import convert_fields_to_types
from easyeda2kicad.easyeda.parameters_easyeda import (
    EeSymbolArc,
    EeSymbolBbox,
    EeSymbolEllipse,
    EeSymbolPath,
    EeSymbolPin,
    EeSymbolPinClock,
    EeSymbolPinDot,
    EeSymbolPinDotBis,
    EeSymbolPinName,
    EeSymbolPinPath,
    EeSymbolPinSettings,
    EeSymbolPolygon,
    EeSymbolPolyline,
)
from easyeda2kicad.easyeda.parameters_easyeda import EasyedaPinType
from easyeda2kicad.easyeda.svg_path_parser import BEZIER_SEGMENTS
from easyeda2kicad.kicad.export_kicad_symbol import (
    KiSymbolShapes,
    convert_ee_ellipses,
    convert_ee_paths,
    convert_ee_pins,
    convert_ee_polylines,
    integrate_sub_units,
)
from easyeda2kicad.kicad.parameters_kicad_symbol import (
    KICAD_SYM_VERSION_20211014,
    KICAD_SYM_VERSION_20220914,
    KiPinStyle,
    overbar_active_low,
)


# Origin (400, 300); EE pixel (410, 310) → KiCad mm (2.54, -2.54)
BBOX = EeSymbolBbox(x=400.0, y=300.0, width=100.0, height=100.0)
_PX = 0.0254 * 10  # 1 EE pixel = 0.254 mm


# ---- convert_ee_pins: pin styles ----


def _make_pin(
    dot: bool = False,
    clock: bool = False,
    path: str = "M 400 300 h 20",
    name: str = "A",
    number: str = "1",
) -> EeSymbolPin:
    return EeSymbolPin(
        settings=EeSymbolPinSettings(
            is_displayed=True,
            type=EasyedaPinType.unspecified,
            spice_pin_number=number,
            pos_x=400,
            pos_y=300,
            rotation=0,
            id="p1",
            is_locked=False,
        ),
        pin_dot=EeSymbolPinDot(dot_x=400, dot_y=300),
        pin_path=EeSymbolPinPath(path=path, color=""),
        name=EeSymbolPinName(
            is_displayed=True,
            pos_x=400,
            pos_y=290,
            rotation=0,
            text=name,
            text_anchor="start",
            font="",
            font_size=7.0,
        ),
        dot=EeSymbolPinDotBis(is_displayed=dot, circle_x=400, circle_y=300),
        clock=EeSymbolPinClock(is_displayed=clock, path=""),
    )


def test_pin_name_and_number_spaces_become_underscores() -> None:
    (pin,) = convert_ee_pins([_make_pin(name="HOLD# or RESET#", number="A 1")], BBOX)
    assert (pin.name, pin.number) == ("~{HOLD}_or_~{RESET}", "A_1")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("CS#", "~{CS}"),
        ("WP#(IO2)", "~{WP}(IO2)"),
        ("HOLD# or RESET#(IO3)", "~{HOLD} or ~{RESET}(IO3)"),
        ("RST#/IO3", "~{RST}/IO3"),
        ("SPI_CS#", "~{SPI_CS}"),
        ("#RST", "#RST"),
        ("D#0", "D#0"),
        ("GND", "GND"),
    ],
)
def test_active_low_words_get_an_overbar(name: str, expected: str) -> None:
    assert overbar_active_low(name) == expected


def test_pin_style_inverted_clock() -> None:
    pins = convert_ee_pins([_make_pin(dot=True, clock=True)], BBOX)
    assert pins[0].style == KiPinStyle.inverted_clock


def test_pin_style_inverted() -> None:
    pins = convert_ee_pins([_make_pin(dot=True, clock=False)], BBOX)
    assert pins[0].style == KiPinStyle.inverted


def test_pin_style_clock() -> None:
    pins = convert_ee_pins([_make_pin(dot=False, clock=True)], BBOX)
    assert pins[0].style == KiPinStyle.clock


@pytest.mark.parametrize(
    "path", ["M 400 300 h 20", "M 400 300 v -20", "M400,300H380", "M 400 300 L 400 320"]
)
def test_pin_length_follows_the_pin_line(path: str) -> None:
    (pin,) = convert_ee_pins([_make_pin(path=path)], BBOX)
    assert pin.length == pytest.approx(20 * _PX)


def test_pin_with_malformed_path_gets_length_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    (pin,) = convert_ee_pins([_make_pin(path="M 400 300 h")], BBOX)
    assert pin.length == 0
    assert "Pin path" in caplog.text


# ---- convert_ee_paths ----


def _make_path(path_str: str, fill: bool = False) -> EeSymbolPath:
    return EeSymbolPath(
        path=path_str,
        stroke_color="",
        stroke_width="1",
        stroke_style="",
        fill_color=fill,
        id="pt1",
        is_locked=False,
    )


def _make_arc(path_str: str, fill: bool = False) -> EeSymbolArc:
    return EeSymbolArc(
        path=path_str,
        helper_dots="",
        stroke_color="",
        stroke_width="1",
        stroke_style="",
        fill_color=fill,
        id="a1",
        is_locked=False,
    )


def _paths(*paths: str) -> KiSymbolShapes:
    return convert_ee_paths([_make_path(path) for path in paths], BBOX)


@pytest.mark.parametrize("fill", [True, False])
def test_convert_ee_paths_mlz(fill: bool) -> None:
    shapes = convert_ee_paths(
        [_make_path("M 400 300 L 410 300 L 405 310 Z", fill)], BBOX
    )
    assert len(shapes.polygons) == 1
    assert shapes.polygons[0].filled == fill
    assert shapes.polygons[0].points == [
        (0.0, 0.0),
        (2.54, 0.0),
        (1.27, -2.54),
        (0.0, 0.0),
    ]
    assert shapes.beziers == []


def test_convert_ee_paths_pure_C() -> None:
    shapes = _paths("M 400 300 C 402 295 408 295 410 300")
    assert shapes.polygons == []
    (bezier,) = shapes.beziers
    assert bezier.points == pytest.approx(
        [(0.0, 0.0), (2 * _PX, 5 * _PX), (8 * _PX, 5 * _PX), (10 * _PX, 0.0)]
    )


def test_convert_ee_paths_two_C_segments() -> None:
    # Real EasyEDA example from JS source: two chained cubic bezier segments
    shapes = convert_ee_paths(
        [
            _make_path(
                "M 368 282.5 C 371 290 372 290 375 282.5 C 378 275 379 275 382 282.5"
            )
        ],
        EeSymbolBbox(x=368.0, y=282.5),
    )
    assert shapes.polygons == []
    assert [(b.points[0], b.points[3]) for b in shapes.beziers] == pytest.approx(
        [((0.0, 0.0), (1.778, 0.0)), ((1.778, 0.0), (3.556, 0.0))]
    )


def test_convert_ee_paths_Q_elevation() -> None:
    # Degree elevation: C1 = P0 + 2/3*(Q1-P0), C2 = Q + 2/3*(Q1-Q)
    (bezier,) = _paths("M 400 300 Q 406 306 412 300").beziers
    assert bezier.points == pytest.approx(
        [(0.0, 0.0), (4 * _PX, -4 * _PX), (8 * _PX, -4 * _PX), (12 * _PX, 0.0)]
    )


def test_convert_ee_paths_mixed_L_C() -> None:
    shapes = _paths("M 400 300 L 405 300 C 407 295 408 295 410 300 L 415 300")
    # Lines before and after the curve are separate polylines
    assert [p.points for p in shapes.polygons] == [
        [(0.0, 0.0), (1.27, 0.0)],
        [(2.54, 0.0), (3.81, 0.0)],
    ]
    assert shapes.beziers[0].points[0] == (1.27, 0.0)


def test_convert_ee_paths_relative_and_compact_syntax() -> None:
    assert _paths("m400 300 10 0l-5 10z") == _paths("M 400 300 L 410 300 L 405 310 Z")


def test_convert_ee_paths_subpaths_are_separate_polygons() -> None:
    shapes = _paths("M 400 300 L 410 300 M 400 310 L 410 310")
    assert [len(p.points) for p in shapes.polygons] == [2, 2]


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("M 400 300 X 999 L 410 300", "'X' is not supported"),
        ("M 400 300 L 410", "multiple of 2 numbers"),
        ("Z", "path must start with M"),
        ("M 400 300", "without segments"),
    ],
)
def test_convert_ee_paths_skips_malformed_path(
    path: str, message: str, caplog: pytest.LogCaptureFixture
) -> None:
    assert _paths(path) == KiSymbolShapes([], [], [], [])
    assert message in caplog.text


def test_bezier_export_kicad7() -> None:
    # KiCad >= 20220914: emits (bezier ...) with 4 xy points
    (bezier,) = _paths("M 400 300 C 402 295 408 295 410 300").beziers
    out = bezier.export(KICAD_SYM_VERSION_20220914)
    assert "(bezier" in out
    assert out.count("(xy ") == 4


def test_bezier_export_fallback_old_version() -> None:
    # KiCad < 20220914: falls back to a (polyline ...) along the curve
    (bezier,) = _paths("M 400 300 C 402 295 408 295 410 300").beziers
    out = bezier.export(KICAD_SYM_VERSION_20211014)
    assert "(polyline" in out
    assert "(bezier" not in out
    points = bezier.approximate()
    assert out.count("(xy ") == len(points) == BEZIER_SEGMENTS + 1
    assert points[0] == bezier.points[0] and points[-1] == bezier.points[-1]
    assert points[len(points) // 2][1] != points[0][1]  # curve, not a chord


# ---- convert_ee_paths: arcs ----


def test_arc_shape_and_path_arc_are_converted_alike() -> None:
    path = "M 410 300 A 10 10 0 0 1 400 310"
    from_arc = convert_ee_paths([_make_arc(path)], BBOX)
    assert len(from_arc.arcs) == 1
    assert from_arc == _paths(path)


def test_arc_mid_point_lies_on_the_arc() -> None:
    (arc,) = _paths("M 410 300 A 10 10 0 0 1 400 310").arcs
    # Clockwise in EasyEDA (y down) from (410,300) to (400,310) around (400,300);
    # start and end are swapped by the y flip.
    assert (arc.start_x, arc.start_y) == pytest.approx((0.0, -10 * _PX))
    assert (arc.end_x, arc.end_y) == pytest.approx((10 * _PX, 0.0))
    half = math.sqrt(0.5) * 10 * _PX
    assert (arc.middle_x, arc.middle_y) == pytest.approx((half, -half))


def test_nearly_closed_arc_becomes_circle() -> None:
    shapes = convert_ee_paths(
        [_make_arc("M 410 300 A 10 10 0 1 1 410 300.05", fill=True)], BBOX
    )
    assert shapes.arcs == []
    (circle,) = shapes.circles
    assert circle.radius == pytest.approx(10 * _PX, abs=1e-3)
    # The circle passes through the arc's start (410, 300)
    start = (10 * _PX, 0.0)
    assert math.dist((circle.pos_x, circle.pos_y), start) == pytest.approx(
        circle.radius, abs=1e-3
    )
    assert circle.background_filling


def test_arc_zero_radius_is_a_line() -> None:
    shapes = _paths("M 400 300 A 0 5 0 0 1 410 300")
    assert shapes.arcs == [] and shapes.circles == []
    assert [p.points for p in shapes.polygons] == [[(0.0, 0.0), (2.54, 0.0)]]


def test_arc_with_invalid_flag_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    assert _paths("M 400 300 A 5 5 0 2 1 410 300") == KiSymbolShapes([], [], [], [])
    assert "arc flags must be 0 or 1" in caplog.text


# ---- convert_ee_polylines ----


def test_filled_closed_path_with_curves_becomes_one_filled_outline() -> None:
    path = "M 400 300 C 400 290 410 290 410 300 A 5 5 0 0 1 400 300 Z"
    shapes = convert_ee_paths([_make_path(path, fill=True)], BBOX)
    assert shapes.beziers == [] and shapes.arcs == []
    (outline,) = shapes.polygons
    assert outline.filled
    assert len(outline.points) == 1 + BEZIER_SEGMENTS + 18


def test_elliptical_arc_becomes_a_polyline() -> None:
    shapes = _paths("M 400 300 A 10 5 0 0 1 420 300")
    assert shapes.arcs == [] and shapes.circles == []
    (polyline,) = shapes.polygons
    assert len(polyline.points) == 1 + 18
    assert not polyline.filled


def _make_ellipse(rx: float, ry: float, fill: bool) -> EeSymbolEllipse:
    return EeSymbolEllipse(
        center_x=410,
        center_y=310,
        radius_x=rx,
        radius_y=ry,
        stroke_color="",
        stroke_width="1",
        stroke_style="",
        fill_color=fill,
        id="e1",
        is_locked=False,
    )


@pytest.mark.parametrize("fill", [True, False])
def test_circular_ellipse_becomes_a_circle(fill: bool) -> None:
    shapes = convert_ee_ellipses([_make_ellipse(5, 5, fill)], BBOX)
    (circle,) = shapes.circles
    assert (circle.pos_x, circle.pos_y, circle.radius) == pytest.approx(
        (2.54, -2.54, 1.27)
    )
    assert circle.background_filling == fill


def test_ellipse_with_zero_radius_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    assert convert_ee_ellipses([_make_ellipse(0, 0, True)], BBOX) == (
        KiSymbolShapes([], [], [], [])
    )
    assert "zero radius" in caplog.text


def test_elliptical_ellipse_becomes_a_closed_polyline() -> None:
    shapes = convert_ee_ellipses([_make_ellipse(10, 5, True)], BBOX)
    (outline,) = shapes.polygons
    assert outline.filled
    xs = [x for x, _ in outline.points]
    ys = [y for _, y in outline.points]
    assert (min(xs), max(xs)) == pytest.approx((0.0, 5.08))
    assert (min(ys), max(ys)) == pytest.approx((-3.81, -1.27))


def _make_polyline(points: str, fill: bool = False) -> EeSymbolPolyline:
    return EeSymbolPolyline(
        points=points,
        stroke_color="",
        stroke_width="1",
        stroke_style="",
        fill_color=fill,
        id="pl1",
        is_locked=False,
    )


@pytest.mark.parametrize(
    ("fill", "expected"), [("#880000", True), ("none", False), ("", False)]
)
def test_fill_color_is_read_from_the_easyeda_fill(fill: str, expected: bool) -> None:
    names = [f.name for f in dataclasses.fields(EeSymbolPolyline)]
    values = ["0 0 1 1", "#000000", "1", "0", fill, "pl1", "0"]
    fields = convert_fields_to_types(dict(zip(names, values)), EeSymbolPolyline)
    assert EeSymbolPolyline(**fields).fill_color is expected


def test_polyline_fill_closes_polygon() -> None:
    (polygon,) = convert_ee_polylines(
        [_make_polyline("400 300 410 300 405 310", fill=True)], BBOX
    )
    assert polygon.filled
    assert polygon.points[0] == polygon.points[-1]


@pytest.mark.parametrize("fill", [True, False])
def test_polygon_is_closed_and_filled_by_its_fill(fill: bool) -> None:
    polyline = _make_polyline("400 300 410 300 405 310", fill)
    polygon = EeSymbolPolygon(**vars(polyline))
    (ki_polygon,) = convert_ee_polylines([polygon], BBOX)
    assert ki_polygon.points[0] == ki_polygon.points[-1]
    assert ki_polygon.filled == fill


def test_polyline_no_fill_not_closed() -> None:
    (polygon,) = convert_ee_polylines(
        [_make_polyline("400,300 410,300 405,310", fill=False)], BBOX
    )
    assert not polygon.filled
    assert polygon.points == [(0.0, 0.0), (2.54, 0.0), (1.27, -2.54)]


@pytest.mark.parametrize(
    ("points", "message"),
    [
        ("", "with 0 point(s)"),
        ("400 300", "with 1 point(s)"),
        ("400 300 410", "odd number of coordinates"),
        ("400 300 x 310", "invalid number"),
    ],
)
def test_malformed_polyline_is_skipped(
    points: str, message: str, caplog: pytest.LogCaptureFixture
) -> None:
    assert convert_ee_polylines([_make_polyline(points)], BBOX) == []
    assert message in caplog.text


# ---- integrate_sub_units ----


def test_integrate_sub_units_empty() -> None:
    main = '(symbol "Foo_0_1" (pin) )'
    assert integrate_sub_units(main, [], "Foo") == main


def test_integrate_sub_units_no_match() -> None:
    main = '(symbol "Foo_0_1" (pin) )'
    sub = '(symbol "Bar_0_1" (pin) )'
    assert integrate_sub_units(main, [sub], "Foo") == main


def test_integrate_sub_units_replaces() -> None:
    main = '  (symbol "Foo_0_1"\n    (pin)\n  )\n'
    sub = '  (symbol "Foo_0_1"\n    (pin)\n  )\n'
    result = integrate_sub_units(main, [sub], "Foo")
    assert '"Foo_1_1"' in result
    assert '"Foo_0_1"' not in result
