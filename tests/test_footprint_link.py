from __future__ import annotations

from pathlib import Path

from easyeda2kicad.__main__ import valid_arguments
from easyeda2kicad.kicad.export_kicad_symbol import tune_footprint_ref_path
from easyeda2kicad.kicad.parameters_kicad_symbol import KiSymbol, KiSymbolInfo


def _make_symbol(package: str = "QFN-32") -> KiSymbol:
    return KiSymbol(
        info=KiSymbolInfo(
            name="TestPart",
            prefix="U",
            package=package,
            manufacturer="",
            datasheet="",
            lcsc_id="C2040",
        )
    )


def test_generated_footprint_link_prefixes_library() -> None:
    symbol = _make_symbol()

    tune_footprint_ref_path(symbol, footprint_lib_name="MyLib")

    assert symbol.info.package == "MyLib:QFN-32"


def test_explicit_footprint_link_replaces_package() -> None:
    symbol = _make_symbol()

    tune_footprint_ref_path(
        symbol,
        footprint_lib_name="MyLib",
        footprint_link_mode="explicit",
        footprint_link_value="CorpLib:CustomPart",
    )

    assert symbol.info.package == "CorpLib:CustomPart"


def test_none_footprint_link_clears_package() -> None:
    symbol = _make_symbol()

    tune_footprint_ref_path(
        symbol,
        footprint_lib_name="MyLib",
        footprint_link_mode="none",
    )

    assert symbol.info.package == ""


def test_explicit_footprint_link_requires_value(tmp_path: Path) -> None:
    arguments = {
        "lcsc_id": ["C2040"],
        "symbol": True,
        "footprint": False,
        "3d": False,
        "full": False,
        "output": str(tmp_path / "test_lib"),
        "overwrite": False,
        "project_relative": False,
        "footprint_link_mode": "explicit",
        "footprint_link": None,
    }

    assert not valid_arguments(arguments)


def test_non_explicit_rejects_footprint_link_value(tmp_path: Path) -> None:
    arguments = {
        "lcsc_id": ["C2040"],
        "symbol": True,
        "footprint": False,
        "3d": False,
        "full": False,
        "output": str(tmp_path / "test_lib"),
        "overwrite": False,
        "project_relative": False,
        "footprint_link_mode": "generated",
        "footprint_link": "CorpLib:CustomPart",
    }

    assert not valid_arguments(arguments)
