"""Placement of 3D models: the WRL model is centered on its own origin and the
EasyEDA placement becomes the footprint's model offset, which KiCad applies
after the model rotation."""

from __future__ import annotations

from pathlib import Path

from easyeda2kicad.easyeda.easyeda_importer import EasyedaFootprintImporter
from easyeda2kicad.easyeda.parameters_easyeda import Ee3dModel, Ee3dModelBase
from easyeda2kicad.kicad.export_kicad_3d_model import generate_wrl_model
from easyeda2kicad.kicad.export_kicad_footprint import ExporterFootprintKicad

# A 2 x 2 x 1 mm box spanning x 10..12, y 20..22, z -1..0
_OBJ = """newmtl m1
Ka 0.2 0.2 0.2
Kd 0.5 0.5 0.5
Ks 0 0 0
d 0
endmtl
v 10 20 -1
v 12 20 -1
v 12 22 -1
v 10 22 0
usemtl m1
f 1 2 3 4
"""


def _model(x: float, y: float, z: float, rotation_z: float = 0.0) -> Ee3dModel:
    return Ee3dModel(
        name="BOX",
        uuid="u1",
        translation=Ee3dModelBase(x=x, y=y, z=z),
        rotation=Ee3dModelBase(x=0, y=0, z=rotation_z),
        raw_obj=_OBJ,
    )


def test_wrl_model_is_centered_with_its_bottom_at_zero() -> None:
    wrl = generate_wrl_model(_model(0, 0, 0)).raw_wrl
    assert wrl is not None
    # (-1, -1, 0) mm and (1, 1, 1) mm in VRML units of 0.1 inch
    assert "-0.3937 -0.3937 0.0, 0.3937 -0.3937 0.0" in wrl
    assert "-0.3937 0.3937 0.3937" in wrl


def test_wrl_model_does_not_depend_on_the_placement() -> None:
    placed = generate_wrl_model(_model(3.65, -2.2, -4, rotation_z=270))
    assert placed.raw_wrl == generate_wrl_model(_model(0, 0, 0)).raw_wrl


def _export(tmp_path: Path, model: Ee3dModel) -> str:
    cad = {
        "packageDetail": {
            "title": "FP",
            "dataStr": {
                "head": {"x": 400, "y": 300, "c_para": {"package": "FP"}},
                "shape": ["HOLE~410~300~2~gge1~0"],
            },
        }
    }
    footprint = EasyedaFootprintImporter(cad).get_footprint()
    footprint.model_3d = model
    output = tmp_path / "FP.kicad_mod"
    ExporterFootprintKicad(footprint).export(str(output), model_3d_path="3d")
    return output.read_text(encoding="utf-8")


def test_footprint_carries_the_placement_as_model_offset(tmp_path: Path) -> None:
    content = _export(tmp_path, _model(-3.65, 2.2, -4.0, rotation_z=270))
    assert "(offset (xyz -3.650 2.200 -4.000))" in content
    assert "(rotate (xyz 0 0 90))" in content


def test_model_offset_has_no_negative_zero(tmp_path: Path) -> None:
    content = _export(tmp_path, _model(-0.0, -0.0001, 0.0))
    assert "(offset (xyz 0.000 0.000 0.000))" in content
