"""Unit tests for footprint writer helper functions."""

import math
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
