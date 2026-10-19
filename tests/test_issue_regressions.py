"""Regression tests for conversion bugs reported in upstream issues.

Each test uses the original EasyEDA shape strings of the part named in the
issue, so no network access is needed.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pytest

from easyeda2kicad.easyeda.easyeda_importer import (
    EasyedaFootprintImporter,
    EasyedaSymbolImporter,
)
from easyeda2kicad.easyeda.parameters_easyeda import EeFootprintSolidRegion
from easyeda2kicad.kicad.export_kicad_footprint import (
    ExporterFootprintKicad,
    _convert_solid_region,
    _parse_solid_region_path,
)
from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad

KICAD_SYM_VERSION = 20231120


def _symbol_cad(name: str, shapes: list[str]) -> dict[str, Any]:
    """Minimal EasyEDA symbol data accepted by EasyedaSymbolImporter."""
    return {
        "dataStr": {"head": {"c_para": {"name": name, "pre": "U?"}}, "shape": shapes}
    }


def _export_symbol(name: str, shapes: list[str]) -> str:
    symbol = EasyedaSymbolImporter(_symbol_cad(name, shapes)).get_symbol()
    return ExporterSymbolKicad(symbol=symbol, version=KICAD_SYM_VERSION).export("fp")


def test_issue_211_name_with_space_is_not_added_twice(tmp_path: Path) -> None:
    """#211, C7421520: "2.54-3P TPGT" is stored as "2.54-3PTPGT"; a re-import
    must find it instead of appending another copy."""
    lib = tmp_path / "lib.kicad_sym"
    saved = []
    for overwrite in (False, False, True):
        symbol = EasyedaSymbolImporter(_symbol_cad("2.54-3P TPGT", [])).get_symbol()
        exporter = ExporterSymbolKicad(symbol=symbol, version=KICAD_SYM_VERSION)
        saved.append(exporter.save_to_lib(str(lib), "fp", overwrite=overwrite))

    assert saved == [True, False, True]  # second import must detect the symbol
    assert lib.read_text(encoding="utf-8").count('(symbol "2.54-3PTPGT"\n') == 1


def test_issue_208_bezier_path_keeps_its_shape_in_new_library(
    tmp_path: Path,
) -> None:
    """#208, C910846 (fuse): the S-curve consists of two cubic Béziers. A new
    library gets the oldest format, which has no (bezier ...); the curve must
    still not become a straight line."""
    cad = _symbol_cad(
        "BSMD2920",
        [
            "PL~415 300 420 300~#880000~1~0~none~gge17~0",
            "PT~M 395 300 C 400 290 405 300 405 300 C 410 310 415 300 415 300"
            "~#880000~1~0~none~gge18~0~",
            "PL~390 300 395 300~#880000~1~0~none~gge19~0",
        ],
    )
    symbol = EasyedaSymbolImporter(cad).get_symbol()
    new_lib = str(tmp_path / "new.kicad_sym")
    output = ExporterSymbolKicad(symbol=symbol, lib_path=new_lib).export("fp")
    y_values = {float(y) for y in re.findall(r"\(xy [-\d.]+ ([-\d.]+)\)", output)}
    assert len(y_values) > 1, "all points lie on one horizontal line"


def test_issue_207_vertical_text_stays_vertical() -> None:
    """#207, C5310972: "PMOS" is rotated by 270° in EasyEDA. Symbol text
    angles are tenths of a degree in KiCad."""
    output = _export_symbol(
        "FDS4435BZ",
        ["T~L~415~305~270~#FF0000~~9pt~~~~comment~PMOS~1~start~gge7~0~pinpart"],
    )
    angle = re.search(r'\(text "PMOS"\s+\(at [-\d.]+ [-\d.]+ ([-\d.]+)\)', output)
    assert angle is not None
    # 270° clockwise in EasyEDA is 90° counter-clockwise in KiCad
    assert angle.group(1) == "900"


def test_issue_206_solid_region_keeps_all_corners() -> None:
    """PR #206, C2651144 courtyard: "M a b L c d L e f Z" has two points per
    command; all four corners must be kept."""
    points = _parse_solid_region_path(
        "M397.04725 303.34645 402.95275 303.34645 "
        "L402.95275 303.34645 402.95275 296.65355 "
        "L402.95275 296.65355 397.04725 296.65355  Z",
        bbox_x_px=400,
        bbox_y_px=300,
    )
    assert len(set(points)) == 4, f"rectangle has {len(set(points))} corners"


def _path(path: str) -> list[tuple[float, float]]:
    return _parse_solid_region_path(path, bbox_x_px=0, bbox_y_px=0)


def test_solid_region_compact_numbers_follow_svg_syntax() -> None:
    assert _path("M0 0L10-5L.5.5Z") == _path("M 0 0 L 10 -5 L 0.5 0.5 Z")


def test_solid_region_arc_with_zero_radius_is_a_line() -> None:
    assert _path("M 0 0 A 0 5 0 0 1 10 0") == [(0.0, 0.0), (2.54, 0.0)]


def test_solid_region_arc_is_flattened_on_its_circle() -> None:
    points = _path("M 10 0 A 10 10 0 0 1 0 10")
    assert len(points) == 10  # 90° in segments of at most 10°
    assert all(
        math.hypot(x, y) == pytest.approx(10 * 0.254, abs=1e-6) for x, y in points
    )


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("L 0 0 L 1 0 L 0 1 Z", "exactly one subpath"),
        ("M 0 0 L 1 0 L 0 1 M 5 5 L 6 5 L 5 6", "exactly one subpath"),
        ("M 0 0 L 1 0 L 0 1 Z L 2 2", "Z must be the last"),
        ("M 0 0 l 1 0 l 0 1 Z", "'l' is not supported"),
        ("M 0 0 C 5 0 15 20 20 20 Z", "'C' is not supported"),
        ("M 0 0 L 1 0 1 L 0 1 Z", "multiple of 2 numbers, got 3"),
        ("M 0 0 L 1 0 A 1 1 0 0 1 0 Z", "multiple of 7 numbers, got 6"),
        ("M 0 0 L L 0 1 Z", "multiple of 2 numbers, got 0"),
        ("M 0 0 L 1 0 L 0 1 Z 5", "takes no arguments"),
        ("M 0 0 L 1 0 x 5 Z", "'x' is not supported"),
        ("M 0 0 L 1# 0 L 0 1 Z", "invalid number"),
        ("M 0 0 L 1e999 1 L 0 1 Z", "non-finite"),
        ("M 0 0 A 1 1 0 2 1 0 1 Z", "flags must be 0 or 1"),
        ("M 0 0 A 1 1 0 0 0.5 0 1 Z", "flags must be 0 or 1"),
    ],
)
def test_malformed_solid_region_is_rejected(path: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _path(path)


def test_malformed_solid_region_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    region = EeFootprintSolidRegion(
        layer_id=3, path="M 0 0 L 1 0 1 L 0 1 Z", region_type="solid"
    )
    assert _convert_solid_region(region, bbox_x=0, bbox_y=0) is None
    assert "skipping" in caplog.text


@pytest.mark.parametrize("path", ["M 0 0 L 10 0 L 10 10 Z", "M 0 0 L 10 0 L 10 10"])
def test_npth_region_becomes_closed_edge_cuts_contour(path: str) -> None:
    region = EeFootprintSolidRegion(layer_id=11, path=path, region_type="npth")
    cutout = _convert_solid_region(region, bbox_x=0, bbox_y=0)
    assert cutout is not None and cutout.layer == "Edge.Cuts"
    assert cutout.points == [(0.0, 0.0), (2.54, 0.0), (2.54, 2.54), (0.0, 0.0)]


def test_issue_209_nearly_closed_arc_becomes_circle() -> None:
    """PR #209, C221907: full circles are arcs ending 0.01 px off the start;
    they must become circles, not arcs with start == end, which KiCad drops."""
    output = _export_symbol(
        "PTS840",
        ["A~M 211 389 A 3 3 0 1 0 211 389.01~~#880000~1~0~none~gge59~0"],
    )
    assert "(arc" not in output
    circles = re.findall(
        r"\(circle\s+\(center ([-\d.]+) ([-\d.]+)\)\s+\(radius ([-\d.]+)\)", output
    )
    assert len(circles) == 1
    center_x, center_y, radius = (float(v) for v in circles[0])
    # Radius 3 px; center 3 px left of the start (211, 389), y flipped; 1 px = 0.254 mm
    assert center_x == pytest.approx(208 * 0.254, abs=0.01)
    assert center_y == pytest.approx(-389.005 * 0.254, abs=0.01)
    assert radius == pytest.approx(3 * 0.254, abs=0.01)


def test_issue_213_npth_cutout_becomes_edge_cuts(tmp_path: Path) -> None:
    """PR #213, C3646941: the slot under the LED is a SOLIDREGION of type
    "npth" on layer 11 and must appear on Edge.Cuts."""
    cad = {
        "packageDetail": {
            "title": "LED",
            "dataStr": {
                "head": {"x": 4046.5, "y": 2958, "c_para": {"package": "LED"}},
                "shape": [
                    "SOLIDREGION~11~~M 4050.0433 2954.2599 A 0.7874 0.7874 0 0 1"
                    " 4050.8307 2955.0473 L 4050.8307 2960.9528 A 0.7874 0.7874"
                    " 0 0 1 4050.0433 2961.7402 L 4042.9567 2961.7402 A 0.7874"
                    " 0.7874 0 0 1 4042.1693 2960.9528 L 4042.1693 2955.0473 A"
                    " 0.7874 0.7874 0 0 1 4042.9567 2954.2599 Z ~npth~gge82~~~~0"
                ],
            },
        }
    }
    footprint = EasyedaFootprintImporter(cad).get_footprint()
    path = tmp_path / "LED.kicad_mod"
    ExporterFootprintKicad(footprint).export(str(path), model_3d_path="")
    content = path.read_text(encoding="utf-8")

    segments = [
        ((float(x1), float(y1)), (float(x2), float(y2)))
        for x1, y1, x2, y2 in re.findall(
            r"\(fp_line \(start ([-\d.]+) ([-\d.]+)\) \(end ([-\d.]+) ([-\d.]+)\)"
            r" \(layer Edge\.Cuts\)",
            content,
        )
    ]
    # 4 straight sides + 4 quarter-circle corners of 9 segments each
    assert len(segments) == 4 + 4 * 9
    assert content.count("Edge.Cuts") == len(segments)  # no filled poly on Edge.Cuts
    for (_, end), (start, _) in zip(segments, segments[1:] + segments[:1]):
        assert end == start, "contour is not closed"
    xs = [x for segment in segments for x, _ in segment]
    ys = [y for segment in segments for _, y in segment]
    assert max(xs) - min(xs) == pytest.approx(8.6614 * 0.254, abs=0.01)
    assert max(ys) - min(ys) == pytest.approx(7.4803 * 0.254, abs=0.01)
    # Rounded, not chamfered: the contour keeps r·(√2 − 1) from the bbox corner
    radius = 0.7874 * 0.254
    corner_gap = min(math.hypot(max(xs) - x, min(ys) - y) for x, y in zip(xs, ys))
    assert corner_gap == pytest.approx(radius * (math.sqrt(2) - 1), abs=0.005)
