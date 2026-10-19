from __future__ import annotations

# Global imports
from dataclasses import dataclass, field
from typing import Optional

from .._version import GENERATOR_URL, __version__

# ---------------------------- FOOTPRINT PART ----------------------------

# .kicad_mod file format of KiCad 8, read by KiCad 8 and later
KI_FOOTPRINT_VERSION = 20240108
# Font of the reference, the value and the ${REFERENCE} text on F.Fab
_FIELD_FONT = "(font (size 1 1) (thickness 0.15))"


def ki_number(value: float) -> str:
    """Number without trailing zeros, rounded to 4 decimals: 0.1 µm for
    lengths, far below any manufacturing tolerance, which drops the noise of
    converting EasyEDA's inch-based values (2.4803 × 0.254 mm = 0.629996 mm
    is 0.63 mm)."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


# Escape sequences of KiCad strings
_STRING_ESCAPES = str.maketrans(
    {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
)


def ki_string(text: str) -> str:
    """Quoted KiCad string."""
    return f'"{text.translate(_STRING_ESCAPES)}"'


def _xy(x: float, y: float) -> str:
    return f"{ki_number(x)} {ki_number(y)}"


def _at(x: float, y: float, angle: float = 0.0) -> str:
    """Position; the angle only if the element is rotated."""
    return f"(at {_xy(x, y)}{f' {ki_number(angle)}' if angle else ''})"


def _layer(layer: str) -> str:
    return f"(layer {ki_string(layer)})"


def _layers(layers: str) -> str:
    return f"(layers {' '.join(map(ki_string, layers.split()))})"


def _stroke(width: float) -> str:
    return f"(stroke (width {ki_number(width)}) (type solid))"


def ki_property(
    name: str, value: str, x: float, y: float, layer: str, hidden: bool
) -> str:
    """Footprint property such as Reference and Value."""
    return (
        f"\t(property {ki_string(name)} {ki_string(value)} {_at(x, y)}"
        f" {_layer(layer)}{' (hide yes)' if hidden else ''}"
        f" (effects {_FIELD_FONT}))\n"
    )


def ki_fab_reference(x: float, y: float) -> str:
    """Text on F.Fab showing the reference."""
    return (
        f'\t(fp_text user "${{REFERENCE}}" {_at(x, y)} {_layer("F.Fab")}'
        f" (effects {_FIELD_FONT}))\n"
    )


def ki_footprint_header(name: str) -> str:
    return (
        f"(footprint {ki_string(name)}\n"
        f"\t(version {KI_FOOTPRINT_VERSION})\n"
        f"\t(generator {ki_string(GENERATOR_URL)})\n"
        f"\t(generator_version {ki_string(__version__)})\n"
        f"\t{_layer('F.Cu')}\n"
    )


# ---------------------------------------

KI_PAD_SHAPE = {
    "ELLIPSE": "circle",
    "RECT": "rect",
    "OVAL": "oval",
    "POLYGON": "custom",
}
KI_PAD_LAYER = {
    1: "F.Cu F.Paste F.Mask",
    2: "B.Cu B.Paste B.Mask",
    3: "F.SilkS",
    11: "*.Cu *.Paste *.Mask",
    13: "F.Fab",
    15: "Dwgs.User",
}

KI_PAD_LAYER_THT = {
    1: "F.Cu F.Mask",
    2: "B.Cu B.Mask",
    3: "F.SilkS",
    11: "*.Cu *.Mask",
    13: "F.Fab",
    15: "Dwgs.User",
}

KI_LAYERS = {
    1: "F.Cu",
    2: "B.Cu",
    3: "F.SilkS",
    4: "B.SilkS",
    5: "F.Paste",
    6: "B.Paste",
    7: "F.Mask",
    8: "B.Mask",
    10: "Edge.Cuts",  # BoardOutLine
    # 11 = Multi-Layer (all copper) — not valid for graphical elements; handled by KI_PAD_LAYER for pads
    12: "Cmts.User",  # Document
    13: "F.Fab",  # TopAssembly
    14: "B.Fab",  # BottomAssembly
    15: "Dwgs.User",  # Mechanical
    99: "F.Fab",  # ComponentShapeLayer - package body, hidden in EasyEDA
}


# ---------------- PAD ----------------
@dataclass
class KiFootprintPad:
    type: str
    shape: str
    pos_x: float
    pos_y: float
    width: float
    height: float
    layers: str
    number: str
    # Drill size along x and y; a round hole if they are equal
    drill: Optional[tuple[float, float]]
    orientation: float
    # Copper polygon of custom pads, relative to the pad position
    polygon: list[tuple[float, float]] = field(default_factory=list)
    polygon_width: float = 0.0

    def export(self) -> str:
        drill = ""
        if self.drill is not None:
            drill_x, drill_y = self.drill
            drill = (
                f" (drill {ki_number(drill_x)})"
                if drill_x == drill_y
                else f" (drill oval {_xy(drill_x, drill_y)})"
            )
        primitives = ""
        if self.polygon:
            pts = " ".join(f"(xy {_xy(x, y)})" for x, y in self.polygon)
            primitives = (
                " (options (clearance outline) (anchor circle))"
                f" (primitives (gr_poly (pts {pts})"
                f" (width {ki_number(self.polygon_width)}) (fill yes)))"
            )
        return (
            f"\t(pad {ki_string(self.number)} {self.type} {self.shape}"
            f" {_at(self.pos_x, self.pos_y, self.orientation)}"
            f" (size {_xy(self.width, self.height)}){drill}"
            f" {_layers(self.layers)}{primitives})\n"
        )


# ---------------- TRACK ----------------
@dataclass
class KiFootprintTrack:
    points_start_x: list[float] = field(default_factory=list)
    points_start_y: list[float] = field(default_factory=list)
    points_end_x: list[float] = field(default_factory=list)
    points_end_y: list[float] = field(default_factory=list)
    stroke_width: float = 0
    layers: str = ""

    def export(self) -> str:
        return "".join(
            f"\t(fp_line (start {_xy(start_x, start_y)}) (end {_xy(end_x, end_y)})"
            f" {_stroke(self.stroke_width)} {_layer(self.layers)})\n"
            for start_x, start_y, end_x, end_y in zip(
                self.points_start_x,
                self.points_start_y,
                self.points_end_x,
                self.points_end_y,
            )
        )


# ---------------- HOLE ----------------
@dataclass
class KiFootprintHole:
    pos_x: float
    pos_y: float
    size: float

    def export(self) -> str:
        return (
            f'\t(pad "" np_thru_hole circle {_at(self.pos_x, self.pos_y)}'
            f" (size {_xy(self.size, self.size)}) (drill {ki_number(self.size)})"
            f" {_layers('*.Cu *.Mask')})\n"
        )


# ---------------- CIRCLE ----------------
@dataclass
class KiFootprintCircle:
    cx: float
    cy: float
    end_x: float
    end_y: float
    layers: str
    stroke_width: float

    def export(self) -> str:
        return (
            f"\t(fp_circle (center {_xy(self.cx, self.cy)})"
            f" (end {_xy(self.end_x, self.end_y)}) {_stroke(self.stroke_width)}"
            f" (fill none) {_layer(self.layers)})\n"
        )


# ---------------- RECTANGLE ----------------
@dataclass
class KiFootprintRectangle:
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    layers: str
    stroke_width: float

    def export(self) -> str:
        return (
            f"\t(fp_rect (start {_xy(self.start_x, self.start_y)})"
            f" (end {_xy(self.end_x, self.end_y)}) {_stroke(self.stroke_width)}"
            f" (fill none) {_layer(self.layers)})\n"
        )


# ---------------- ARC ----------------
@dataclass
class KiFootprintArc:
    start_x: float
    start_y: float
    mid_x: float
    mid_y: float
    end_x: float
    end_y: float
    layers: str
    stroke_width: float

    def export(self) -> str:
        return (
            f"\t(fp_arc (start {_xy(self.start_x, self.start_y)})"
            f" (mid {_xy(self.mid_x, self.mid_y)}) (end {_xy(self.end_x, self.end_y)})"
            f" {_stroke(self.stroke_width)} {_layer(self.layers)})\n"
        )


# ---------------- TEXT ----------------
@dataclass
class KiFootprintText:
    pos_x: float
    pos_y: float
    orientation: float
    text: str
    layers: str
    font_size: float
    thickness: float
    hidden: bool
    mirrored: bool

    def export(self) -> str:
        size = _xy(self.font_size, self.font_size)
        return (
            f"\t(fp_text user {ki_string(self.text)}"
            f" {_at(self.pos_x, self.pos_y, self.orientation)}"
            f" {_layer(self.layers)}{' (hide yes)' if self.hidden else ''}"
            f" (effects (font (size {size}) (thickness {ki_number(self.thickness)}))"
            f" (justify left{' mirror' if self.mirrored else ''})))\n"
        )


# ---------------- VIA ----------------
@dataclass
class KiFootprintVia:
    pos_x: float
    pos_y: float
    size: float
    diameter: float

    def export(self) -> str:
        return (
            f'\t(pad "" thru_hole circle {_at(self.pos_x, self.pos_y)}'
            f" (size {_xy(self.diameter, self.diameter)})"
            f" (drill {ki_number(self.size)}) {_layers('*.Cu *.Paste *.Mask')})\n"
        )


# ---------------- SOLID REGION ----------------
# EasyEDA SOLIDREGION: filled polygon on silkscreen or fab layer
# (e.g. pin-1 dot, polarity mark).
@dataclass
class KiFootprintSolidRegion:
    layer: str
    points: list[tuple[float, float]]

    def export(self) -> str:
        pts = " ".join(f"(xy {_xy(x, y)})" for x, y in self.points)
        return (
            f"\t(fp_poly (pts {pts}) {_stroke(0)} (fill solid) {_layer(self.layer)})\n"
        )


# ---------------- COPPER AREA ----------------
# EasyEDA COPPERAREA: copper fill zone (ground plane, thermal relief area).
# In KiCad this maps to a zone. Not yet implemented.
@dataclass
class KiFootprintCopperArea:
    name: str = ""


# ---------------- FOOTPRINT INFO ----------------
@dataclass
class KiFootprintInfo:
    name: str
    fp_type: str
    lcsc_id: str = ""
    manufacturer: str = ""
    mpn: str = ""
    description: str = ""


# ---------------- 3D MODEL ----------------
@dataclass
class Ki3dModelBase:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def xyz(self) -> str:
        return f"(xyz {_xy(self.x, self.y)} {ki_number(self.z)})"


@dataclass
class Ki3dModel:
    name: str
    translation: Ki3dModelBase
    rotation: Ki3dModelBase
    raw_wrl: Optional[str] = None

    def export(self, path: str) -> str:
        return (
            f"\t(model {ki_string(path)} (offset {self.translation.xyz()})"
            f" (scale (xyz 1 1 1)) (rotate {self.rotation.xyz()}))\n"
        )


# ---------------- FOOTPRINT  ----------------
@dataclass
class KiFootprint:
    info: KiFootprintInfo
    model_3d: Optional[Ki3dModel]
    pads: list[KiFootprintPad] = field(default_factory=list)
    tracks: list[KiFootprintTrack] = field(default_factory=list)
    vias: list[KiFootprintVia] = field(default_factory=list)
    holes: list[KiFootprintHole] = field(default_factory=list)
    circles: list[KiFootprintCircle] = field(default_factory=list)
    arcs: list[KiFootprintArc] = field(default_factory=list)
    rectangles: list[KiFootprintRectangle] = field(default_factory=list)
    texts: list[KiFootprintText] = field(default_factory=list)
    solid_regions: list[KiFootprintSolidRegion] = field(default_factory=list)
    reference_pos: tuple[float, float] = (0.0, 0.0)
    value_pos: tuple[float, float] = (0.0, 0.0)
    fab_reference_pos: tuple[float, float] = (0.0, 0.0)
    copper_areas: list[KiFootprintCopperArea] = field(default_factory=list)
