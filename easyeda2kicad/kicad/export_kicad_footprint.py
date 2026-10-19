from __future__ import annotations

# Global imports
import logging
from collections.abc import Iterable
from math import ceil, cos, floor, isclose, isnan, radians, sin, tau
from pathlib import Path
from typing import Protocol, TypeVar

# Local imports
from ..easyeda.parameters_easyeda import (
    EeFootprint,
    EeFootprintSolidRegion,
    EeFootprintText,
)
from ..easyeda.svg_path_parser import (
    Point,
    SvgArc,
    SvgSubpath,
    parse_svg_path,
    parse_svg_points,
)
from .parameters_kicad_footprint import (
    KI_LAYERS,
    KI_PAD_LAYER,
    KI_PAD_LAYER_THT,
    KI_PAD_SHAPE,
    Ki3dModel,
    Ki3dModelBase,
    KiFootprint,
    KiFootprintArc,
    KiFootprintCircle,
    KiFootprintHole,
    KiFootprintInfo,
    KiFootprintPad,
    KiFootprintRectangle,
    KiFootprintSolidRegion,
    KiFootprintText,
    KiFootprintTrack,
    KiFootprintVia,
    ki_fab_reference,
    ki_footprint_header,
    ki_property,
    ki_string,
)

# ---------------------------------------


def fp_to_ki(dim: float | str) -> float:
    """Convert EasyEDA footprint dimension to KiCad mm. Returns 0.0 for empty/invalid input."""
    if dim in ("", None):
        return 0.0
    try:
        val = float(dim)
        return round(val * 10 * 0.0254, 6) if not isnan(val) else 0.0
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------


def drill_to_ki(
    hole_radius: float, hole_length: float, pad_height: float, pad_width: float
) -> tuple[float, float] | None:
    """Drill size along x and y; a slot runs along the longer pad side."""
    if hole_radius <= 0:
        return None
    diameter = hole_radius * 2
    if hole_length == 0:
        return diameter, diameter
    longest = max(diameter, hole_length)
    if pad_height - longest >= pad_width - longest:
        return diameter, hole_length
    return hole_length, diameter


# ---------------------------------------


def angle_to_ki(rotation: float | str) -> float:
    """Convert EasyEDA rotation angle to KiCad. Handles both float and string input."""
    try:
        rot_float = float(rotation) if isinstance(rotation, str) else rotation
        if isnan(rot_float) is False:
            return -(360 - rot_float) if rot_float > 180 else rot_float
    except (ValueError, TypeError):
        pass
    return 0.0


def _layer(layer_id: int) -> str:
    """KiCad layer of an EasyEDA layer id; F.Fab for unknown ids."""
    return KI_LAYERS.get(layer_id, "F.Fab")


def _track(points: list[Point], layers: str, stroke_width: float) -> KiFootprintTrack:
    """Track of line segments through the points (in mm)."""
    return KiFootprintTrack(
        points_start_x=[x for x, _ in points[:-1]],
        points_start_y=[y for _, y in points[:-1]],
        points_end_x=[x for x, _ in points[1:]],
        points_end_y=[y for _, y in points[1:]],
        stroke_width=stroke_width,
        layers=layers,
    )


# ---------------------------------------

# Bounding box: min x, min y, max x, max y
Extent = tuple[float, float, float, float]


def _extent(points: Iterable[Point]) -> Extent | None:
    """Bounding box of the points; None without points."""
    points = list(points)
    if not points:
        return None
    xs, ys = [x for x, _ in points], [y for _, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def _disc(center: Point, radius: float) -> list[Point]:
    """Points of a circle farthest in -x, +x, -y and +y direction."""
    x, y = center
    return [(x - radius, y), (x + radius, y), (x, y - radius), (x, y + radius)]


def _rotate(point: Point, angle: float) -> Point:
    """Point rotated about the origin by a KiCad angle in degrees, which turns
    counterclockwise on the screen (y down)."""
    x, y = point
    c, s = cos(radians(angle)), sin(radians(angle))
    return x * c + y * s, y * c - x * s


def _pad_extremes(pad: KiFootprintPad) -> list[Point]:
    """Points whose bounding box is that of the pad shape."""
    x, y = pad.pos_x, pad.pos_y
    half_w, half_h = pad.width / 2, pad.height / 2
    if pad.shape == "circle":
        return _disc((x, y), half_w)
    if pad.shape == "oval":
        # Two half circles at the ends of the longer side
        radius = min(half_w, half_h)
        dx, dy = _rotate((half_w - radius, half_h - radius), pad.orientation)
        return _disc((x + dx, y + dy), radius) + _disc((x - dx, y - dy), radius)
    corners = [
        (-half_w, -half_h),
        (half_w, -half_h),
        (half_w, half_h),
        (-half_w, half_h),
    ]
    return [
        (x + dx, y + dy)
        for dx, dy in (_rotate(corner, pad.orientation) for corner in corners)
    ]


# KiCad library convention (KLC F5.3): the courtyard keeps 0.25 mm from pads
# and body, lies on a 0.01 mm grid and is drawn with 0.05 mm lines
_COURTYARD_CLEARANCE = 0.25
_COURTYARD_GRID = 0.01
_COURTYARD_WIDTH = 0.05
# Distance from the courtyard and silkscreen to the center of the 1 mm high
# reference above and value below
_FIELD_OFFSET = 1.0


def _courtyard(extent: Extent) -> Extent:
    """Extent grown by the courtyard clearance and rounded out to its grid."""
    min_x, min_y, max_x, max_y = extent
    clearance, grid = _COURTYARD_CLEARANCE, _COURTYARD_GRID
    # Rounding the quotient first keeps values on the grid from moving a step
    return (
        floor(round((min_x - clearance) / grid, 6)) * grid,
        floor(round((min_y - clearance) / grid, 6)) * grid,
        ceil(round((max_x + clearance) / grid, 6)) * grid,
        ceil(round((max_y + clearance) / grid, 6)) * grid,
    )


# ---------------------------------------

# EasyEDA describes the physical component on layers of its own: 99 the
# package body, 100 the leads and 101 the polarity mark. Body regions become
# F.Fab outlines with KiCad's 0.1 mm fab line width (KLC F5.2); all shapes on
# the body layer bound the courtyard by their geometry, as KLC measures from
# the body edge, not from the line drawing it. Leads and polarity marks are
# not imported.
_BODY_LAYER = 99
_BODY_OUTLINE_WIDTH = 0.1
_SKIPPED_LAYERS = {100, 101}
# Line width of the polygon of custom pads
_CUSTOM_PAD_STROKE = 0.1
# Layers whose "solid" SOLIDREGIONs are imported as filled polygons; paste
# areas (5/6) belong to the pads
_FILLED_REGION_LAYERS = {3, 4, 13, 14}
# "npth" regions on any layer are board cutouts
_NPTH_REGION_LAYER = "Edge.Cuts"
_NPTH_OUTLINE_WIDTH = 0.05


def _parse_solid_region_path(
    path: str, bbox_x_px: float, bbox_y_px: float
) -> list[Point]:
    """Contour of a region path in mm, with curves and arcs flattened and
    consecutive duplicate points dropped.

    Subtracts bbox in pixel space before converting to mm to avoid
    float rounding artifacts from subtracting two independently rounded values.
    Raises ValueError for malformed paths and paths with several subpaths.
    """
    subpaths = parse_svg_path(path)
    if len(subpaths) != 1:
        raise ValueError(f"path must consist of one subpath, got {len(subpaths)}")
    points: list[Point] = []
    for x, y in subpaths[0].flatten():
        point = (fp_to_ki(x - bbox_x_px), fp_to_ki(y - bbox_y_px))
        if not points or points[-1] != point:
            points.append(point)
    return points


def _convert_solid_region(
    ee_region: EeFootprintSolidRegion, bbox_x: float, bbox_y: float
) -> KiFootprintSolidRegion | KiFootprintTrack | None:
    """Convert an EeFootprintSolidRegion to a filled polygon or a closed outline.

    NPTH regions are board cutouts (slots, notches) and become Edge.Cuts
    outlines on any layer (EasyEDA uses 11, Multi-Layer); the package body
    becomes an F.Fab outline. Returns None for regions on other layers,
    malformed paths and contours with < 3 points.
    """
    outline_width = None
    if ee_region.region_type == "npth":
        layer, outline_width = _NPTH_REGION_LAYER, _NPTH_OUTLINE_WIDTH
    elif ee_region.region_type != "solid":
        return None
    elif ee_region.layer_id == _BODY_LAYER:
        layer, outline_width = KI_LAYERS[_BODY_LAYER], _BODY_OUTLINE_WIDTH
    elif ee_region.layer_id in _FILLED_REGION_LAYERS:
        layer = KI_LAYERS[ee_region.layer_id]
    else:
        return None

    try:
        points = _parse_solid_region_path(ee_region.path, bbox_x, bbox_y)
    except ValueError as e:
        logging.warning(f"Skipping SOLIDREGION on layer {ee_region.layer_id}: {e}")
        return None
    if len(points) < 3:
        logging.warning(
            f"Skipping SOLIDREGION on layer {ee_region.layer_id} "
            f"with {len(points)} point(s)"
        )
        return None

    if points[0] != points[-1]:
        points.append(points[0])
    if outline_width is not None:
        return _track(points, layer, outline_width)
    return KiFootprintSolidRegion(layer=layer, points=points)


class _OnLayer(Protocol):
    layer_id: int


class _Element(Protocol):
    def export(self) -> str: ...


_Shape = TypeVar("_Shape", bound=_OnLayer)


def _imported(shapes: list[_Shape]) -> list[_Shape]:
    """The shapes except those on skipped layers."""
    return [shape for shape in shapes if shape.layer_id not in _SKIPPED_LAYERS]


# ---------------------------------------


class ExporterFootprintKicad:
    def __init__(self, footprint: EeFootprint):
        self.input = footprint
        if not isinstance(self.input, EeFootprint):
            logging.error("Unsupported conversion")
        else:
            self.generate_kicad_footprint()

    def _ki_point(self, point: Point) -> Point:
        """EasyEDA footprint pixel point → KiCad mm, relative to the bbox."""
        return (
            fp_to_ki(point[0]) - self.input.bbox.x,
            fp_to_ki(point[1]) - self.input.bbox.y,
        )

    def _add_extent(
        self,
        ee_layer_id: int,
        layers: str,
        points: Iterable[Point],
        stroke_width: float = 0.0,
    ) -> None:
        """Record the extent of a shape drawn through the points: on its KiCad
        layers including the stroke, and on the body layer as part of the
        package body, whose edge the points describe."""
        points = list(points)
        extent = self._extents.setdefault(layers, [])
        for point in points:
            extent += _disc(point, stroke_width / 2)
        if ee_layer_id == _BODY_LAYER:
            self._component_points += points

    def _add_track(self, ee_layer_id: int, track: KiFootprintTrack) -> None:
        """Add a track to the footprint and record its extent."""
        points = zip(
            track.points_start_x + track.points_end_x,
            track.points_start_y + track.points_end_y,
        )
        self._add_extent(ee_layer_id, track.layers, points, track.stroke_width)
        self.output.tracks.append(track)

    def _text_extremes(self, ee_text: EeFootprintText) -> list[Point]:
        """Extreme points of the glyph strokes EasyEDA renders for the text,
        or its anchor if the strokes are missing or malformed."""
        try:
            subpaths = parse_svg_path(ee_text.text_path)
        except ValueError as e:
            logging.debug(f"Text {ee_text.text!r} without usable strokes: {e}")
            subpaths = []
        points = [point for subpath in subpaths for point in subpath.extremes()]
        anchor = (
            ee_text.center_x - self.input.bbox.x,
            ee_text.center_y - self.input.bbox.y,
        )
        return [self._ki_point(point) for point in points] or [anchor]

    def _place_courtyard_and_fields(self) -> None:
        """Courtyard around pads, holes, vias and package body; reference
        above and value below it and the silkscreen, ${REFERENCE} in its center."""
        component = _extent(self._component_points)
        courtyard = None
        if component is not None:
            courtyard = _courtyard(component)
            corner, opposite = courtyard[:2], courtyard[2:]
            self.output.rectangles.append(
                KiFootprintRectangle(*corner, *opposite, "F.CrtYd", _COURTYARD_WIDTH)
            )
            self._extents["F.CrtYd"] = [corner, opposite]
        fields = _extent(
            self._extents.get("F.CrtYd", []) + self._extents.get("F.SilkS", [])
        ) or (0.0, 0.0, 0.0, 0.0)
        min_x, min_y, max_x, max_y = courtyard or fields
        center = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        self.output.reference_pos = (center[0], fields[1] - _FIELD_OFFSET)
        self.output.value_pos = (center[0], fields[3] + _FIELD_OFFSET)
        self.output.fab_reference_pos = center

    def generate_kicad_footprint(self) -> None:
        bbox_x_px = self.input.bbox.x_px
        bbox_y_px = self.input.bbox.y_px
        # Points spanning the shapes per KiCad layer
        self._extents: dict[str, list[Point]] = {}
        # Points spanning the copper of pads, holes, vias and the package body
        self._component_points: list[Point] = []

        ki_info = KiFootprintInfo(
            name=self.input.info.name,
            fp_type=self.input.info.fp_type,
            lcsc_id=self.input.info.lcsc_id,
            manufacturer=self.input.info.manufacturer,
            mpn=self.input.info.mpn,
            description=self.input.info.description,
        )

        if self.input.model_3d is not None:
            # The model offset is in mm with y up, like the EasyEDA translation
            offset = self.input.model_3d.translation
            ki_3d_model_info = Ki3dModel(
                name=self.input.model_3d.name,
                translation=Ki3dModelBase(x=offset.x, y=offset.y, z=offset.z),
                rotation=Ki3dModelBase(
                    x=(360 - self.input.model_3d.rotation.x) % 360,
                    y=(360 - self.input.model_3d.rotation.y) % 360,
                    z=(360 - self.input.model_3d.rotation.z) % 360,
                ),
                raw_wrl=None,
            )
        else:
            ki_3d_model_info = None

        self.output = KiFootprint(info=ki_info, model_3d=ki_3d_model_info)

        # For pads
        for ee_pad in self.input.pads:
            if ee_pad.hole_radius <= 0:
                pad_type, layers = "smd", KI_PAD_LAYER.get(ee_pad.layer_id, "")
            elif ee_pad.is_plated:
                pad_type = "thru_hole"
                layers = KI_PAD_LAYER_THT.get(ee_pad.layer_id, "")
            else:
                # A non-plated hole has no copper
                pad_type, layers = "np_thru_hole", "*.Mask"
            ki_pad = KiFootprintPad(
                type=pad_type,
                shape=(
                    KI_PAD_SHAPE[ee_pad.shape]
                    if ee_pad.shape in KI_PAD_SHAPE
                    else "custom"
                ),
                pos_x=ee_pad.center_x - self.input.bbox.x,
                pos_y=ee_pad.center_y - self.input.bbox.y,
                width=max(ee_pad.width, 0.01),
                height=max(ee_pad.height, 0.01),
                layers=layers,
                # KiCad pad numbers cannot contain spaces
                number=ee_pad.number.replace(" ", "_"),
                drill=None,
                orientation=angle_to_ki(ee_pad.rotation),
            )

            ki_pad.drill = drill_to_ki(
                ee_pad.hole_radius, ee_pad.hole_length, ki_pad.height, ki_pad.width
            )
            # EasyEDA sometimes encodes pad numbers as "name(number)" (e.g. "A(1)").
            # Extract the part inside the parentheses as the canonical pad number.
            if "(" in ki_pad.number and ")" in ki_pad.number:
                normalized = ki_pad.number.split("(")[1].split(")")[0]
                logging.debug(
                    f"PAD: normalized pad number '{ki_pad.number}' → '{normalized}'"
                )
                ki_pad.number = normalized

            outline = _pad_extremes(ki_pad)
            if ki_pad.shape == "custom":
                try:
                    points = parse_svg_points(ee_pad.points)
                    if not points:
                        raise ValueError("no points defined")
                except ValueError as e:
                    logging.warning(f"Skipping polygon of pad {ee_pad.id}: {e}")
                else:
                    # Set the pad width and height to the smallest value allowed by KiCad.
                    # KiCad tries to draw a pad that forms the base of the polygon,
                    # but this is often unnecessary and should be disabled.
                    ki_pad.width = 0.005
                    ki_pad.height = 0.005

                    # Polygon points already carry baked-in rotation (EasyEDA applies
                    # the pad rotation to the point coordinates before serialization,
                    # unlike RECT pads where rotation is stored separately).
                    ki_pad.orientation = 0

                    outline = [self._ki_point(point) for point in points]
                    ki_pad.polygon = [
                        (x - ki_pad.pos_x, y - ki_pad.pos_y) for x, y in outline
                    ]
                    ki_pad.polygon_width = _CUSTOM_PAD_STROKE
                    # The polygon's stroke is copper too
                    outline = [
                        disc_point
                        for point in outline
                        for disc_point in _disc(point, _CUSTOM_PAD_STROKE / 2)
                    ]

            self._component_points += outline
            self.output.pads.append(ki_pad)

        # For tracks
        for ee_track in _imported(self.input.tracks):
            try:
                points = parse_svg_points(ee_track.points)
            except ValueError as e:
                logging.warning(f"Skipping track: {e}")
                continue
            if len(points) < 2:
                logging.warning(f"Skipping track with {len(points)} point(s)")
                continue
            self._add_track(
                ee_track.layer_id,
                _track(
                    [self._ki_point(point) for point in points],
                    _layer(ee_track.layer_id),
                    max(ee_track.stroke_width, 0.01),
                ),
            )

        # For holes
        for ee_hole in self.input.holes:
            ki_hole = KiFootprintHole(
                pos_x=ee_hole.center_x - self.input.bbox.x,
                pos_y=ee_hole.center_y - self.input.bbox.y,
                size=ee_hole.radius * 2,
            )
            self._component_points += _disc(
                (ki_hole.pos_x, ki_hole.pos_y), ee_hole.radius
            )
            self.output.holes.append(ki_hole)

        # For Vias
        for ee_via in self.input.vias:
            ki_via = KiFootprintVia(
                pos_x=ee_via.center_x - self.input.bbox.x,
                pos_y=ee_via.center_y - self.input.bbox.y,
                size=ee_via.radius * 2,
                diameter=ee_via.diameter,
            )
            self._component_points += _disc(
                (ki_via.pos_x, ki_via.pos_y), ee_via.diameter / 2
            )
            self.output.vias.append(ki_via)

        # For circles
        for ee_circle in _imported(self.input.circles):
            ki_circle = KiFootprintCircle(
                cx=ee_circle.cx - self.input.bbox.x,
                cy=ee_circle.cy - self.input.bbox.y,
                end_x=0.0,
                end_y=0.0,
                layers=_layer(ee_circle.layer_id),
                stroke_width=max(ee_circle.stroke_width, 0.01),
            )
            ki_circle.end_x = ki_circle.cx + ee_circle.radius
            ki_circle.end_y = ki_circle.cy
            self._add_extent(
                ee_circle.layer_id,
                ki_circle.layers,
                _disc((ki_circle.cx, ki_circle.cy), ee_circle.radius),
                ki_circle.stroke_width,
            )
            self.output.circles.append(ki_circle)

        # For rectangles
        for ee_rectangle in _imported(self.input.rectangles):
            corner = (
                ee_rectangle.x - self.input.bbox.x,
                ee_rectangle.y - self.input.bbox.y,
            )
            opposite = (
                corner[0] + ee_rectangle.width,
                corner[1] + ee_rectangle.height,
            )
            ki_rectangle = KiFootprintRectangle(
                *corner,
                *opposite,
                _layer(ee_rectangle.layer_id),
                max(ee_rectangle.stroke_width, 0.01),
            )
            self._add_extent(
                ee_rectangle.layer_id,
                ki_rectangle.layers,
                [corner, opposite],
                ki_rectangle.stroke_width,
            )
            self.output.rectangles.append(ki_rectangle)

        # For arcs
        for ee_arc in _imported(self.input.arcs):
            try:
                subpaths = parse_svg_path(ee_arc.path)
            except ValueError as e:
                logging.warning(f"Skipping arc: {e}")
                continue
            if not any(subpath.segments for subpath in subpaths):
                logging.warning(f"Skipping arc {ee_arc.path!r} without segments")
                continue
            layers = _layer(ee_arc.layer_id)
            stroke_width = max(ee_arc.stroke_width, 0.01)
            for subpath in subpaths:
                self._add_extent(
                    ee_arc.layer_id,
                    layers,
                    map(self._ki_point, subpath.extremes()),
                    stroke_width,
                )
                # Segments between circular arcs, each run flattened to a track
                runs = [[subpath.start]]
                for start, segment in subpath.walk():
                    geometry = circle = None
                    if isinstance(segment, SvgArc):
                        geometry = segment.geometry(start)
                        circle = geometry.circle()
                    if geometry is None or circle is None:
                        runs[-1] += SvgSubpath(start, [segment]).flatten()[1:]
                        continue
                    center, sweep = circle
                    if isclose(abs(sweep), tau):
                        # KiCad arcs need distinct ends
                        self.output.circles.append(
                            KiFootprintCircle(
                                *self._ki_point(center),
                                *self._ki_point(start),
                                layers,
                                stroke_width,
                            )
                        )
                    else:
                        # The circular arc through start, middle and end
                        self.output.arcs.append(
                            KiFootprintArc(
                                *self._ki_point(start),
                                *self._ki_point(geometry.point(0.5)),
                                *self._ki_point(segment.end),
                                layers,
                                stroke_width,
                            )
                        )
                    runs.append([segment.end])
                self.output.tracks += [
                    _track([self._ki_point(p) for p in run], layers, stroke_width)
                    for run in runs
                    if len(run) > 1
                ]

        # For texts
        for ee_text in _imported(self.input.texts):
            layers = _layer(ee_text.layer_id)
            if ee_text.type == "N":
                layers = layers.replace(".SilkS", ".Fab")
            ki_text = KiFootprintText(
                pos_x=ee_text.center_x - self.input.bbox.x,
                pos_y=ee_text.center_y - self.input.bbox.y,
                orientation=angle_to_ki(ee_text.rotation),
                text=ee_text.text,
                layers=layers,
                font_size=max(ee_text.font_size, 1),
                thickness=max(ee_text.stroke_width, 0.01),
                hidden=not ee_text.is_displayed,
                mirrored=layers.startswith("B."),
            )
            if ee_text.is_displayed:
                self._add_extent(
                    ee_text.layer_id,
                    ki_text.layers,
                    self._text_extremes(ee_text),
                    ki_text.thickness,
                )
            self.output.texts.append(ki_text)

        # For solid regions
        for ee_region in self.input.solid_regions:
            ki_region = _convert_solid_region(ee_region, bbox_x_px, bbox_y_px)
            if isinstance(ki_region, KiFootprintSolidRegion):
                self._add_extent(ee_region.layer_id, ki_region.layer, ki_region.points)
                self.output.solid_regions.append(ki_region)
            elif ki_region is not None:
                self._add_track(ee_region.layer_id, ki_region)

        self._place_courtyard_and_fields()

    def get_ki_footprint(self) -> KiFootprint:
        return self.output

    def export(
        self,
        footprint_full_path: str,
        model_3d_path: str,
        model_3d_extension: str = "wrl",
    ) -> None:
        ki = self.output
        ki_lib = ki_footprint_header(ki.info.name)
        if ki.info.description:
            ki_lib += f"\t(descr {ki_string(ki.info.description)})\n"

        ki_lib += ki_property("Reference", "REF**", *ki.reference_pos, "F.SilkS", False)
        ki_lib += ki_property("Value", ki.info.name, *ki.value_pos, "F.Fab", False)
        for name, value in (
            ("LCSC Part", ki.info.lcsc_id),
            ("Manufacturer", ki.info.manufacturer),
            ("MPN", ki.info.mpn),
        ):
            if value:
                ki_lib += ki_property(name, value, 0, 0, "F.Fab", True)

        if ki.info.fp_type:
            attr = "smd" if ki.info.fp_type == "smd" else "through_hole"
            ki_lib += f"\t(attr {attr})\n"

        ki_lib += ki_fab_reference(*ki.fab_reference_pos)
        elements: list[_Element] = [
            *ki.tracks,
            *ki.rectangles,
            *ki.circles,
            *ki.arcs,
            *ki.solid_regions,
            *ki.texts,
            *ki.pads,
            *ki.holes,
            *ki.vias,
        ]
        ki_lib += "".join(element.export() for element in elements)

        if ki.model_3d is not None:
            ki_lib += ki.model_3d.export(
                f"{model_3d_path}/{ki.model_3d.name}.{model_3d_extension}"
            )
        ki_lib += ")\n"

        Path(footprint_full_path).parent.mkdir(parents=True, exist_ok=True)
        with open(
            file=footprint_full_path,
            mode="w",
            encoding="utf-8",
        ) as my_lib:
            my_lib.write(ki_lib)
