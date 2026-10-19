"""Unit tests for the SVG path parser."""

from __future__ import annotations

import logging

import pytest

from easyeda2kicad.easyeda.svg_path_parser import (
    SvgPathClosePath,
    SvgPathEllipticalArc,
    SvgPathLineTo,
    SvgPathMoveTo,
    parse_svg_args,
    parse_svg_path,
    split_svg_path,
)


def test_split_keeps_exponents_in_numbers() -> None:
    assert split_svg_path("M1e2 0L.5-5Z") == [("M", "1e2 0"), ("L", ".5-5"), ("Z", "")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10-5", [[10.0, -5.0]]),
        (".5.5 1e1,2", [[0.5, 0.5], [10.0, 2.0]]),
    ],
)
def test_parse_args_follows_svg_number_syntax(
    text: str, expected: list[list[float]]
) -> None:
    assert parse_svg_args("L", text) == expected


@pytest.mark.parametrize(
    ("cmd", "text", "message"),
    [
        ("l", "1 2", "'l' is not supported"),
        ("L", "1# 2", "invalid number"),
        ("L", "1e999 2", "non-finite"),
        ("L", "1 2 3", "multiple of 2 numbers, got 3"),
        ("Z", "1", "takes no arguments"),
        ("A", "1 1 0 -1 1 5 5", "flags must be 0 or 1"),
    ],
)
def test_parse_args_rejects_malformed_arguments(
    cmd: str, text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_svg_args(cmd, text)


def test_parse_path() -> None:
    assert parse_svg_path("M400-300A3 3 0 0 1 410-300L1 2 3 4Z") == [
        SvgPathMoveTo(start_x=400, start_y=-300),
        SvgPathEllipticalArc(
            radius_x=3,
            radius_y=3,
            x_axis_rotation=0,
            flag_large_arc=False,
            flag_sweep=True,
            end_x=410,
            end_y=-300,
        ),
        SvgPathLineTo(pos_x=1, pos_y=2),
        SvgPathLineTo(pos_x=3, pos_y=4),
        SvgPathClosePath(),
    ]


def test_parse_path_treats_extra_move_pairs_as_lines() -> None:
    assert parse_svg_path("M 0 0 5 5") == [
        SvgPathMoveTo(start_x=0, start_y=0),
        SvgPathLineTo(pos_x=5, pos_y=5),
    ]


def test_parse_path_skips_malformed_commands(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        path = parse_svg_path("M 0 0 C 1 1 2 2 3 3 A 1 1 0 2 1 5 5 L 1")
    assert path == [SvgPathMoveTo(start_x=0, start_y=0)]
    assert "'C' is not supported" in caplog.text
    assert "flags must be 0 or 1" in caplog.text
    assert "multiple of 2 numbers" in caplog.text
