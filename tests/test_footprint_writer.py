"""Unit tests for footprint writer helper functions."""

import math
import re
from pathlib import Path

import pytest

from easyeda2kicad.easyeda.easyeda_importer import (
    EasyedaFootprintImporter,
    _sanitize_footprint_name,
)
from easyeda2kicad.easyeda.svg_path_parser import BEZIER_SEGMENTS, Point, SvgArc
from easyeda2kicad.kicad.export_kicad_footprint import (
    ExporterFootprintKicad,
    _arc_angle,
    angle_to_ki,
    drill_to_ki,
    fp_to_ki,
)

# ---------------------------------------------------------------------------
# fp_to_ki
# ---------------------------------------------------------------------------


class TestFpToKi:
    def test_known_value(self) -> None:
        # 100 EasyEDA px = 100 * 10 * 0.0254 = 25.4 mm
        assert fp_to_ki(100) == pytest.approx(25.4, abs=1e-6)

    def test_empty_string(self) -> None:
        assert fp_to_ki("") == 0.0

    def test_none(self) -> None:
        assert fp_to_ki(None) == 0.0  # type: ignore[arg-type]  # explicit runtime guard

    def test_invalid_string(self) -> None:
        assert fp_to_ki("abc") == 0.0

    def test_zero(self) -> None:
        assert fp_to_ki(0) == 0.0

    def test_negative(self) -> None:
        assert fp_to_ki(-100) == pytest.approx(-25.4, abs=1e-6)

    def test_precision_6_decimals(self) -> None:
        # Result must not have more than 6 decimal places (1nm resolution)
        result = fp_to_ki(3)
        assert result == round(result, 6)


# ---------------------------------------------------------------------------
# drill_to_ki
# ---------------------------------------------------------------------------


class TestDrillToKi:
    def test_round_hole(self) -> None:
        result = drill_to_ki(
            hole_radius=0.5, hole_length=0, pad_height=1.0, pad_width=1.0
        )
        assert result == "(drill 1.0)"

    def test_no_hole(self) -> None:
        assert drill_to_ki(0, 0, 1.0, 1.0) == ""

    def test_oval_horizontal(self) -> None:
        # hole_length > hole_radius*2 → oval, longer in first dimension
        result = drill_to_ki(
            hole_radius=0.3, hole_length=1.0, pad_height=2.0, pad_width=2.0
        )
        assert "oval" in result

    def test_oval_orientation_depends_on_pad(self) -> None:
        # pad_height > pad_width → slot along height axis
        r1 = drill_to_ki(0.3, 1.0, pad_height=3.0, pad_width=1.0)
        r2 = drill_to_ki(0.3, 1.0, pad_height=1.0, pad_width=3.0)
        assert r1 != r2


# ---------------------------------------------------------------------------
# angle_to_ki
# ---------------------------------------------------------------------------


class TestAngleToKi:
    def test_zero(self) -> None:
        assert angle_to_ki(0) == 0.0

    def test_90(self) -> None:
        assert angle_to_ki(90) == 90.0

    def test_270_wraps(self) -> None:
        # 270 > 180 → -(360 - 270) = -90
        assert angle_to_ki(270) == pytest.approx(-90.0)

    def test_string_input(self) -> None:
        assert angle_to_ki("90") == 90.0

    def test_invalid_string(self) -> None:
        assert angle_to_ki("bad") == 0.0


# ---------------------------------------------------------------------------
# _arc_angle
# ---------------------------------------------------------------------------


def _angle(large_arc: bool, sweep: bool, start: Point, end: Point) -> float:
    return _arc_angle(
        SvgArc(1, 1, 0, large_arc, sweep, end).geometry(start).sweep_angle
    )


class TestArcAngle:
    def test_semicircle(self) -> None:
        assert abs(_angle(False, True, (-1, 0), (1, 0))) == pytest.approx(180)

    def test_large_arc_flag_selects_the_longer_arc(self) -> None:
        assert _angle(False, True, (1, 0), (0, 1)) == pytest.approx(-90)
        assert _angle(True, True, (1, 0), (0, 1)) == pytest.approx(-270)

    def test_sweep_flag_inverts_the_sign(self) -> None:
        assert _angle(False, False, (1, 0), (0, 1)) == pytest.approx(90)

    def test_full_circle_keeps_its_extent(self) -> None:
        assert _arc_angle(math.tau) == pytest.approx(-360)


# ---------------------------------------------------------------------------
# ARC export
# ---------------------------------------------------------------------------


def _export(tmp_path: Path, *shapes: str) -> str:
    cad = {
        "packageDetail": {
            "title": "FP",
            "dataStr": {
                "head": {"x": 400, "y": 300, "c_para": {"package": "FP"}},
                "shape": list(shapes),
            },
        }
    }
    footprint = EasyedaFootprintImporter(cad).get_footprint()
    output = tmp_path / "FP.kicad_mod"
    ExporterFootprintKicad(footprint).export(str(output), model_3d_path="")
    return output.read_text(encoding="utf-8")


def _export_arc(tmp_path: Path, path: str) -> str:
    return _export(tmp_path, f"ARC~1~3~~{path}~~gge1~0")


def test_pad_number_spaces_become_underscores(tmp_path: Path) -> None:
    pad = "PAD~RECT~410~300~10~10~1~~A 1~0~~0~gge1~0~~Y~0"
    assert "(pad A_1 smd rect" in _export(tmp_path, pad)


@pytest.mark.parametrize("points", ["400 300", ""])
def test_track_with_fewer_than_two_points_is_skipped(
    tmp_path: Path, points: str, caplog: pytest.LogCaptureFixture
) -> None:
    assert "(fp_line" not in _export(tmp_path, f"TRACK~1~3~~{points}~gge1~0")
    assert "Skipping track with" in caplog.text


class TestHoles:
    def test_hole_is_not_plated(self, tmp_path: Path) -> None:
        content = _export(tmp_path, "HOLE~410~300~2~gge1~0")
        assert '(pad "" np_thru_hole circle (at 2.54 0.00) (size 1.02 1.02)' in content

    @pytest.mark.parametrize(
        ("plated", "expected"),
        [
            (
                "Y",
                "(pad 1 thru_hole circle (at 2.54 0.00 0.00) (size 2.540 2.540) "
                "(layers *.Cu *.Mask)",
            ),
            (
                "N",
                "(pad 1 np_thru_hole circle (at 2.54 0.00 0.00) (size 2.540 2.540) "
                "(layers *.Mask)",
            ),
        ],
    )
    def test_pad_hole_follows_plating(
        self, tmp_path: Path, plated: str, expected: str
    ) -> None:
        pad = f"PAD~ELLIPSE~410~300~10~10~11~~1~2~~0~gge1~0~~{plated}~0"
        assert expected in _export(tmp_path, pad)


def _courtyard(content: str) -> list[str]:
    return re.findall(r"\(fp_line \(start ([-\d.]+ [-\d.]+)\).*F\.CrtYd", content)


class TestCourtyardAndFields:
    PAD = "PAD~RECT~410~300~10~10~1~~1~0~~0~gge1~0~~Y~0"
    BODY = "SOLIDREGION~99~~M 390 290 L 410 290 L 410 310 L 390 310 Z ~solid~gge2~~~~0"

    def test_courtyard_keeps_the_clearance_around_the_pads(
        self, tmp_path: Path
    ) -> None:
        content = _export(tmp_path, self.PAD)
        # Pad 1.27..3.81 x -1.27..1.27 mm grown by 0.25 mm
        assert _courtyard(content) == [
            "1.02 -1.52",
            "4.06 -1.52",
            "4.06 1.52",
            "1.02 1.52",
        ]
        assert "(fp_text reference REF** (at 2.540 -2.520)" in content
        assert "(at 2.540 2.520) (layer F.Fab)" in content
        assert "(fp_text user %R (at 2.540 0.000)" in content

    def test_rotated_oval_pad_is_enclosed_and_snapped_to_the_grid(
        self, tmp_path: Path
    ) -> None:
        # 1.016 x 2.54 mm turned by 90°: ±1.27 x ±0.508 mm
        pad = "PAD~OVAL~400~300~4~10~1~~1~0~~90~gge1~0~~Y~0"
        assert _courtyard(_export(tmp_path, pad))[:3] == [
            "-1.52 -0.76",
            "1.52 -0.76",
            "1.52 0.76",
        ]

    def test_body_becomes_fab_outline_inside_the_courtyard(
        self, tmp_path: Path
    ) -> None:
        content = _export(tmp_path, self.PAD, self.BODY)
        assert content.count("(layer F.Fab) (width 0.10)") == 4
        # Body ±2.54 mm, pad up to 3.81 mm
        assert _courtyard(content)[0] == "-2.79 -2.79"
        assert _courtyard(content)[2] == "4.06 2.79"

    @pytest.mark.parametrize(
        "body",
        [
            "TRACK~1~99~~390 290 410 290 410 310 390 310 390 290~gge3~0",
            "RECT~390~290~20~20~99~gge3~0~1",
        ],
    )
    def test_body_of_lines_bounds_the_courtyard_by_its_edge(
        self, tmp_path: Path, body: str
    ) -> None:
        # Body edge ±2.54 mm; its 0.254 mm line does not count
        content = _export(tmp_path, body)
        assert _courtyard(content)[0] == "-2.79 -2.79"
        assert _courtyard(content)[2] == "2.79 2.79"

    def test_custom_pad_includes_the_stroke_of_its_polygon(
        self, tmp_path: Path
    ) -> None:
        pad = (
            "PAD~POLYGON~400~300~10~10~1~~1~0~395 295 405 295 405 305 395 305"
            "~0~gge1~0~~Y~0"
        )
        # Polygon ±1.27 mm with a 0.1 mm wide stroke
        assert _courtyard(_export(tmp_path, pad))[0] == "-1.57 -1.57"

    def test_reference_clears_the_silkscreen(self, tmp_path: Path) -> None:
        # 0.254 mm wide silkscreen line at y = -5.08 mm, above the courtyard
        silk = "TRACK~1~3~~395 280 405 280~gge3~0"
        content = _export(tmp_path, self.BODY, silk)
        assert "(fp_text reference REF** (at 0.000 -6.207)" in content

    @pytest.mark.parametrize(("displayed", "reference_y"), [("", -6.715), ("0", -3.79)])
    def test_reference_clears_visible_silkscreen_texts(
        self, tmp_path: Path, displayed: str, reference_y: float
    ) -> None:
        # Glyph strokes up to y = -5.588 mm, 0.254 mm wide
        text = f"TEXT~L~395~285~1~0~0~3~~8~AB~M 395 278 L 405 285~{displayed}~gge4~~0"
        content = _export(tmp_path, self.BODY, text)
        assert f"(fp_text reference REF** (at 0.000 {reference_y:.3f})" in content

    @pytest.mark.parametrize("layer", ["100", "101"])
    def test_lead_and_polarity_shapes_are_skipped(
        self, tmp_path: Path, layer: str
    ) -> None:
        content = _export(tmp_path, f"CIRCLE~380~300~1~0.2~{layer}~gge5~0~~")
        assert "(fp_circle" not in content and not _courtyard(content)

    def test_footprint_without_shapes_has_no_courtyard(self, tmp_path: Path) -> None:
        content = _export(tmp_path)
        assert not _courtyard(content)
        assert "(fp_text reference REF** (at 0.000 -1.000)" in content


class TestArcExport:
    def test_compact_numbers(self, tmp_path: Path) -> None:
        compact = _export_arc(tmp_path, "M400-300A10 10 0 0 1 410-290")
        spaced = _export_arc(tmp_path, "M 400 -300 A 10 10 0 0 1 410 -290")
        assert "(fp_arc" in compact
        assert compact == spaced

    def test_zero_radius_is_a_line(self, tmp_path: Path) -> None:
        # SVG draws an arc with a zero radius as a straight line
        content = _export_arc(tmp_path, "M 400 300 A 0 10 0 0 1 410 300")
        assert "(fp_arc" not in content
        assert "(fp_line (start 0.00 0.00) (end 2.54 0.00) (layer F.SilkS)" in content

    def test_relative_path(self, tmp_path: Path) -> None:
        relative = _export_arc(tmp_path, "M 400 -300 a 10 10 0 0 1 10 10")
        absolute = _export_arc(tmp_path, "M 400 -300 A 10 10 0 0 1 410 -290")
        assert relative == absolute

    def test_lines_and_curves_in_the_path_become_tracks(self, tmp_path: Path) -> None:
        content = _export_arc(
            tmp_path, "M 400 300 A 10 10 0 0 1 410 310 L 420 310 Q 425 315 430 310"
        )
        assert content.count("(fp_arc") == 1
        assert "(fp_line (start 2.54 2.54) (end 5.08 2.54)" in content
        assert content.count("(fp_line") == 1 + BEZIER_SEGMENTS

    def test_elliptical_arc_becomes_a_track(self, tmp_path: Path) -> None:
        content = _export_arc(tmp_path, "M 400 300 A 10 5 0 0 1 420 300")
        assert "(fp_arc" not in content
        assert content.count("(fp_line") == 18

    def test_tiny_radius_is_scaled_up_to_the_chord(self, tmp_path: Path) -> None:
        content = _export_arc(tmp_path, "M 400 300 A 1e-6 1e-6 0 0 1 410 300")
        assert "(fp_arc (start 1.27 0.00) (end 2.54 0.00) (angle -180.00)" in content

    def test_path_without_segments_is_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        content = _export_arc(tmp_path, "M 400 300")
        assert "(fp_arc" not in content and "(fp_line" not in content
        assert "without segments" in caplog.text

    @pytest.mark.parametrize(
        "path", ["M 400 300 A 10 10 0 2 1 410 300", "M 400 300 A 10 10 0 0 1 410"]
    )
    def test_malformed_arc_is_skipped(
        self, tmp_path: Path, path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        content = _export_arc(tmp_path, path)
        assert "(fp_arc" not in content
        assert "Skipping arc" in caplog.text


# ---------------------------------------------------------------------------
# _sanitize_footprint_name
# ---------------------------------------------------------------------------


class TestSanitizeFootprintName:
    def test_clean_name_unchanged(self) -> None:
        assert _sanitize_footprint_name("SOT-23") == "SOT-23"

    def test_forward_slash_replaced(self) -> None:
        assert _sanitize_footprint_name("USB/UART") == "USB_UART"

    def test_backslash_replaced(self) -> None:
        assert _sanitize_footprint_name("A\\B") == "A_B"

    def test_colon_replaced(self) -> None:
        assert _sanitize_footprint_name("lib:name") == "lib_name"

    def test_angle_brackets_replaced(self) -> None:
        assert _sanitize_footprint_name("A<->B") == "A_-_B"

    def test_quote_replaced(self) -> None:
        assert _sanitize_footprint_name('say"hi"') == "say_hi_"

    def test_spaces_preserved(self) -> None:
        assert _sanitize_footprint_name("SOT 23") == "SOT 23"

    def test_mixed_illegal_chars(self) -> None:
        assert _sanitize_footprint_name("USB/Serial:1\\2") == "USB_Serial_1_2"
