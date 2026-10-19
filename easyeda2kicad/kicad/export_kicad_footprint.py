from __future__ import annotations

# Global imports
import logging
from math import ceil, copysign, degrees, isnan
from pathlib import Path

# Local imports
from ..easyeda.parameters_easyeda import EeFootprint, EeFootprintSolidRegion
from ..easyeda.svg_path_parser import (
    SvgPathEllipticalArc,
    SvgPathMoveTo,
    parse_svg_args,
    parse_svg_path,
    split_svg_path,
    svg_arc_geometry,
)
from .parameters_kicad_footprint import (
    KI_ARC,
    KI_CIRCLE,
    KI_END_FILE,
    KI_FAB_REF,
    KI_FP_POLY,
    KI_FP_TYPE,
    KI_HOLE,
    KI_LAYERS,
    KI_LINE,
    KI_MODEL_3D,
    KI_MODULE_INFO,
    KI_PACKAGE_VALUE,
    KI_PAD,
    KI_PAD_LAYER,
    KI_PAD_LAYER_THT,
    KI_PAD_SHAPE,
    KI_REFERENCE,
    KI_TEXT,
    KI_VIA,
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
)

# ---------------------------------------


def compute_arc(
    start_x: float,
    start_y: float,
    radius_x: float,
    radius_y: float,
    angle: float,
    large_arc_flag: bool,
    sweep_flag: bool,
    end_x: float,
    end_y: float,
) -> tuple[float, float, float]:
    """Center and angular extent (degrees, KiCad orientation) of an SVG arc."""
    arc = svg_arc_geometry(
        sx=start_x,
        sy=start_y,
        ex=end_x,
        ey=end_y,
        rx=radius_x,
        ry=radius_y,
        x_rotation_deg=angle,
        large_arc=large_arc_flag,
        sweep=sweep_flag,
    )
    extent = degrees(arc.sweep_angle)
    return arc.center_x, arc.center_y, -copysign(abs(extent) % 360, extent)


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
) -> str:
    if hole_radius > 0 and hole_length != 0:
        max_distance_hole = max(hole_radius * 2, hole_length)
        pos_0 = pad_height - max_distance_hole
        pos_90 = pad_width - max_distance_hole
        max_distance = max(pos_0, pos_90)

        if max_distance == pos_0:
            return f"(drill oval {hole_radius * 2} {hole_length})"
        else:
            return f"(drill oval {hole_length} {hole_radius * 2})"
    if hole_radius > 0:
        return f"(drill {2 * hole_radius})"
    return ""


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


# ---------------------------------------

# Layers whose "solid" SOLIDREGIONs are imported ("npth" ones are board cutouts).
# Layer 5/6 (F.Paste/B.Paste) are skipped — paste areas belong to pad definitions, not graphics.
# Layer 99 (ComponentShapeLayer/LIBBODY) is not visible in EasyEDA; imported as F.CrtYd outline.
# Layer 100 is skipped — decorative lead shapes.
# Layer 101 is skipped — decorative pin circles.
_SOLID_REGION_LAYERS = {3, 4, 13, 14, 99}
_NPTH_REGION_LAYER = "Edge.Cuts"
# Regions on these layers are exported as outlines, all others as filled polygons.
_OUTLINE_REGION_LAYERS = ("F.CrtYd", _NPTH_REGION_LAYER)

# Maximum angle per line segment when flattening arcs
_ARC_SEGMENT_DEG = 10.0


def _parse_solid_region_path(
    path: str, bbox_x_px: float, bbox_y_px: float
) -> list[tuple[float, float]]:
    """Convert an EasyEDA SVG path string to a list of (x, y) mm points.

    Supports what EasyEDA writes: one subpath of absolute M, L, H, V and A
    commands, each with any number of coordinate sets (extra pairs after M are
    line-tos, as in SVG), optionally closed by a final Z. Arcs are flattened
    into segments of at most _ARC_SEGMENT_DEG. Consecutive duplicate points
    are dropped.
    Subtracts bbox in pixel space before converting to mm to avoid
    float rounding artifacts from subtracting two independently rounded values.
    Raises ValueError for anything else: other or relative commands, several
    subpaths and the malformed arguments rejected by parse_svg_args.
    """
    points: list[tuple[float, float]] = []
    cur_x = cur_y = 0.0
    closed = False

    def add_point() -> None:
        point = (fp_to_ki(cur_x - bbox_x_px), fp_to_ki(cur_y - bbox_y_px))
        if not points or points[-1] != point:
            points.append(point)

    for cmd, text in split_svg_path(path):
        if cmd not in "MLHVAZ":
            raise ValueError(f"path command '{cmd}' is not supported")
        coordinate_sets = parse_svg_args(cmd, text)
        if closed:
            raise ValueError("Z must be the last path command")
        if (cmd == "M") == bool(points):
            raise ValueError("path must consist of exactly one subpath starting with M")
        if cmd == "Z":
            closed = True
        for values in coordinate_sets:
            if cmd == "H":
                cur_x = values[0]
            elif cmd == "V":
                cur_y = values[0]
            else:
                end_x, end_y = values[-2], values[-1]
                # SVG draws an arc with a zero radius as a straight line
                if cmd == "A" and values[0] and values[1]:
                    arc = svg_arc_geometry(
                        sx=cur_x,
                        sy=cur_y,
                        ex=end_x,
                        ey=end_y,
                        rx=values[0],
                        ry=values[1],
                        x_rotation_deg=values[2],
                        large_arc=bool(values[3]),
                        sweep=bool(values[4]),
                    )
                    # Rounding keeps float noise (90.0000001°) from adding a segment
                    steps = ceil(
                        round(abs(degrees(arc.sweep_angle)) / _ARC_SEGMENT_DEG, 6)
                    )
                    for step in range(1, steps):
                        cur_x, cur_y = arc.point(step / steps)
                        add_point()
                cur_x, cur_y = end_x, end_y
            add_point()

    return points


def _convert_solid_region(
    ee_region: EeFootprintSolidRegion, bbox_x: float, bbox_y: float
) -> KiFootprintSolidRegion | None:
    """Convert an EeFootprintSolidRegion to a closed KiFootprintSolidRegion.

    NPTH regions are board cutouts (slots, notches) and go to Edge.Cuts on
    any layer (EasyEDA uses 11, Multi-Layer). Returns None for regions on other
    layers, malformed paths and contours with < 3 points.
    """
    if ee_region.region_type == "npth":
        layer = _NPTH_REGION_LAYER
    elif (
        ee_region.region_type == "solid" and ee_region.layer_id in _SOLID_REGION_LAYERS
    ):
        layer = KI_LAYERS[ee_region.layer_id]
    else:
        return None

    try:
        points = _parse_solid_region_path(ee_region.path, bbox_x, bbox_y)
    except ValueError as e:
        logging.warning(f"SOLIDREGION on layer {ee_region.layer_id}: skipping, {e}")
        return None
    if len(points) < 3:
        logging.warning(
            f"SOLIDREGION on layer {ee_region.layer_id}: "
            f"skipping, only {len(points)} point(s) parsed"
        )
        return None

    if points[0] != points[-1]:
        points.append(points[0])
    return KiFootprintSolidRegion(layer=layer, points=points)


# ---------------------------------------


class ExporterFootprintKicad:
    def __init__(self, footprint: EeFootprint):
        self.input = footprint
        if not isinstance(self.input, EeFootprint):
            logging.error("Unsupported conversion")
        else:
            self.generate_kicad_footprint()

    def generate_kicad_footprint(self) -> None:
        bbox_x_px = self.input.bbox.x_px
        bbox_y_px = self.input.bbox.y_px

        ki_info = KiFootprintInfo(
            name=self.input.info.name,
            fp_type=self.input.info.fp_type,
            lcsc_id=self.input.info.lcsc_id,
            manufacturer=self.input.info.manufacturer,
            mpn=self.input.info.mpn,
            description=self.input.info.description,
        )

        if self.input.model_3d is not None:
            ki_3d_model_info = Ki3dModel(
                name=self.input.model_3d.name,
                translation=Ki3dModelBase(
                    x=0.0,
                    y=0.0,
                    z=0.0,
                ),
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
            ki_pad = KiFootprintPad(
                type="thru_hole" if ee_pad.hole_radius > 0 else "smd",
                shape=(
                    KI_PAD_SHAPE[ee_pad.shape]
                    if ee_pad.shape in KI_PAD_SHAPE
                    else "custom"
                ),
                pos_x=ee_pad.center_x - self.input.bbox.x,
                pos_y=ee_pad.center_y - self.input.bbox.y,
                width=max(ee_pad.width, 0.01),
                height=max(ee_pad.height, 0.01),
                layers=(
                    KI_PAD_LAYER if ee_pad.hole_radius <= 0 else KI_PAD_LAYER_THT
                ).get(ee_pad.layer_id, ""),
                number=ee_pad.number,
                drill="",
                orientation=angle_to_ki(ee_pad.rotation),
                polygon="",
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

            # For custom polygon
            is_custom_shape = ki_pad.shape == "custom"
            point_list = [fp_to_ki(point) for point in ee_pad.points.split()]
            if is_custom_shape:
                if len(point_list) <= 0:
                    logging.warning(
                        f"PAD ${ee_pad.id} is a polygon, but has no points defined"
                    )
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

                    # Generate polygon with coordinates relative to the base pad's position.
                    path = "".join(
                        "(xy {:.6f} {:.6f})".format(
                            point_list[i] - self.input.bbox.x - ki_pad.pos_x,
                            point_list[i + 1] - self.input.bbox.y - ki_pad.pos_y,
                        )
                        for i in range(0, len(point_list), 2)
                    )
                    ki_pad.polygon = (
                        "\n\t\t(primitives \n\t\t\t(gr_poly \n\t\t\t\t(pts"
                        f" {path}\n\t\t\t\t) \n\t\t\t\t(width 0.1) \n\t\t\t)\n\t\t)\n\t"
                    )

            self.output.pads.append(ki_pad)

        # For tracks
        for ee_track in self.input.tracks:
            ki_track = KiFootprintTrack(
                layers=(
                    KI_LAYERS[ee_track.layer_id]
                    if ee_track.layer_id in KI_LAYERS
                    else "F.Fab"
                ),
                stroke_width=max(ee_track.stroke_width, 0.01),
            )

            # Generate line
            point_list = [fp_to_ki(point) for point in ee_track.points.split()]
            for i in range(0, len(point_list) - 2, 2):
                ki_track.points_start_x.append(point_list[i] - self.input.bbox.x)
                ki_track.points_start_y.append(point_list[i + 1] - self.input.bbox.y)
                ki_track.points_end_x.append(point_list[i + 2] - self.input.bbox.x)
                ki_track.points_end_y.append(point_list[i + 3] - self.input.bbox.y)

            self.output.tracks.append(ki_track)

        # For holes
        for ee_hole in self.input.holes:
            ki_hole = KiFootprintHole(
                pos_x=ee_hole.center_x - self.input.bbox.x,
                pos_y=ee_hole.center_y - self.input.bbox.y,
                size=ee_hole.radius * 2,
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

            self.output.vias.append(ki_via)

        # For circles
        for ee_circle in self.input.circles:
            ki_circle = KiFootprintCircle(
                cx=ee_circle.cx - self.input.bbox.x,
                cy=ee_circle.cy - self.input.bbox.y,
                end_x=0.0,
                end_y=0.0,
                layers=(
                    KI_LAYERS[ee_circle.layer_id]
                    if ee_circle.layer_id in KI_LAYERS
                    else "F.Fab"
                ),
                stroke_width=max(ee_circle.stroke_width, 0.01),
            )
            ki_circle.end_x = ki_circle.cx + ee_circle.radius
            ki_circle.end_y = ki_circle.cy
            self.output.circles.append(ki_circle)

        # For rectangles
        for ee_rectangle in self.input.rectangles:
            ki_rectangle = KiFootprintRectangle(
                layers=(
                    KI_LAYERS[ee_rectangle.layer_id]
                    if ee_rectangle.layer_id in KI_LAYERS
                    else "F.Fab"
                ),
                stroke_width=max(ee_rectangle.stroke_width, 0.01),
            )

            start_x = ee_rectangle.x - self.input.bbox.x
            start_y = ee_rectangle.y - self.input.bbox.y
            width = ee_rectangle.width
            height = ee_rectangle.height

            ki_rectangle.points_start_x = [
                start_x,
                start_x + width,
                start_x + width,
                start_x,
            ]
            ki_rectangle.points_start_y = [
                start_y,
                start_y,
                start_y + height,
                start_y + height,
            ]
            ki_rectangle.points_end_x = [
                start_x + width,
                start_x + width,
                start_x,
                start_x,
            ]
            ki_rectangle.points_end_y = [
                start_y,
                start_y + height,
                start_y + height,
                start_y,
            ]

            self.output.rectangles.append(ki_rectangle)

        # For arcs
        for ee_arc in self.input.arcs:
            arc_path = parse_svg_path(ee_arc.path)
            if (
                len(arc_path) < 2
                or not isinstance(arc_path[0], SvgPathMoveTo)
                or not isinstance(arc_path[1], SvgPathEllipticalArc)
            ):
                logging.warning("Skipping arc with unexpected SVG path structure")
                continue
            svg_start, svg_arc = arc_path[0], arc_path[1]
            start_x = fp_to_ki(svg_start.start_x) - self.input.bbox.x
            start_y = fp_to_ki(svg_start.start_y) - self.input.bbox.y
            end_x = fp_to_ki(svg_arc.end_x) - self.input.bbox.x
            end_y = fp_to_ki(svg_arc.end_y) - self.input.bbox.y
            rx = fp_to_ki(svg_arc.radius_x)
            ry = fp_to_ki(svg_arc.radius_y)
            layers = (
                KI_LAYERS[ee_arc.layer_id] if ee_arc.layer_id in KI_LAYERS else "F.Fab"
            )
            stroke_width = max(ee_arc.stroke_width, 0.01)

            # SVG draws an arc with a zero radius as a straight line
            if rx == 0 or ry == 0:
                self.output.tracks.append(
                    KiFootprintTrack(
                        points_start_x=[start_x],
                        points_start_y=[start_y],
                        points_end_x=[end_x],
                        points_end_y=[end_y],
                        stroke_width=stroke_width,
                        layers=layers,
                    )
                )
                continue
            cx, cy, extent = compute_arc(
                start_x,
                start_y,
                rx,
                ry,
                svg_arc.x_axis_rotation,
                svg_arc.flag_large_arc,
                svg_arc.flag_sweep,
                end_x,
                end_y,
            )

            ki_arc = KiFootprintArc(
                start_x=cx,
                start_y=cy,
                end_x=end_x,
                end_y=end_y,
                angle=extent,
                layers=layers,
                stroke_width=stroke_width,
            )
            self.output.arcs.append(ki_arc)

        # For texts
        for ee_text in self.input.texts:
            ki_text = KiFootprintText(
                pos_x=ee_text.center_x - self.input.bbox.x,
                pos_y=ee_text.center_y - self.input.bbox.y,
                orientation=angle_to_ki(ee_text.rotation),
                text=ee_text.text,
                layers=(
                    KI_LAYERS[ee_text.layer_id]
                    if ee_text.layer_id in KI_LAYERS
                    else "F.Fab"
                ),
                font_size=max(ee_text.font_size, 1),
                thickness=max(ee_text.stroke_width, 0.01),
                display=" hide" if ee_text.is_displayed is False else "",
                mirror="",
            )
            ki_text.layers = (
                ki_text.layers.replace(".SilkS", ".Fab")
                if ee_text.type == "N"
                else ki_text.layers
            )
            ki_text.mirror = " mirror" if ki_text.layers[0] == "B" else ""
            self.output.texts.append(ki_text)

        # For solid regions
        for ee_region in self.input.solid_regions:
            ki_region = _convert_solid_region(ee_region, bbox_x_px, bbox_y_px)
            if ki_region is not None:
                self.output.solid_regions.append(ki_region)

    def get_ki_footprint(self) -> KiFootprint:
        return self.output

    def export(
        self,
        footprint_full_path: str,
        model_3d_path: str,
        model_3d_extension: str = "wrl",
    ) -> None:
        ki = self.output
        ki_lib = ""

        ki_lib += KI_MODULE_INFO.format(
            package_lib="easyeda2kicad", package_name=ki.info.name, edit="5DC5F6A4"
        )
        if ki.info.description:
            ki_lib += f'\t(descr "{ki.info.description}")\n'

        if ki.info.fp_type:
            ki_lib += KI_FP_TYPE.format(
                component_type=("smd" if ki.info.fp_type == "smd" else "through_hole")
            )

        # Get y_min and y_max to place reference and value text
        y_low = min((pad.pos_y for pad in ki.pads), default=0)
        y_high = max((pad.pos_y for pad in ki.pads), default=0)

        ki_lib += KI_REFERENCE.format(pos_x=0.0, pos_y=y_low - 4)

        ki_lib += KI_PACKAGE_VALUE.format(
            package_name=ki.info.name, pos_x=0.0, pos_y=y_high + 4
        )
        ki_lib += KI_FAB_REF

        if ki.info.lcsc_id:
            ki_lib += f'\t(property "LCSC Part" "{ki.info.lcsc_id}")\n'
        if ki.info.manufacturer:
            ki_lib += f'\t(property "Manufacturer" "{ki.info.manufacturer}")\n'
        if ki.info.mpn:
            ki_lib += f'\t(property "MPN" "{ki.info.mpn}")\n'

        # ---------------------------------------

        for track in ki.tracks + ki.rectangles:
            for i in range(len(track.points_start_x)):
                ki_lib += KI_LINE.format(
                    start_x=track.points_start_x[i],
                    start_y=track.points_start_y[i],
                    end_x=track.points_end_x[i],
                    end_y=track.points_end_y[i],
                    layers=track.layers,
                    stroke_width=track.stroke_width,
                )

        for pad in ki.pads:
            ki_lib += KI_PAD.format(**vars(pad))

        for hole in ki.holes:
            ki_lib += KI_HOLE.format(**vars(hole))

        for via in ki.vias:
            ki_lib += KI_VIA.format(**vars(via))

        for circle in ki.circles:
            ki_lib += KI_CIRCLE.format(**vars(circle))

        for arc in ki.arcs:
            ki_lib += KI_ARC.format(**vars(arc))

        for text in ki.texts:
            ki_lib += KI_TEXT.format(**vars(text))

        for region in ki.solid_regions:
            if region.layer in _OUTLINE_REGION_LAYERS:
                pts = region.points
                for i in range(len(pts) - 1):
                    ki_lib += KI_LINE.format(
                        start_x=pts[i][0],
                        start_y=pts[i][1],
                        end_x=pts[i + 1][0],
                        end_y=pts[i + 1][1],
                        layers=region.layer,
                        stroke_width=0.05,
                    )
            else:
                pts_str = " ".join(f"(xy {x:.6f} {y:.6f})" for x, y in region.points)
                ki_lib += KI_FP_POLY.format(pts=pts_str, layer=region.layer)

        if ki.model_3d is not None:
            ki_lib += KI_MODEL_3D.format(
                file_3d=f"{model_3d_path}/{ki.model_3d.name}.{model_3d_extension}",
                pos_x=ki.model_3d.translation.x,
                pos_y=ki.model_3d.translation.y,
                pos_z=ki.model_3d.translation.z,
                rot_x=ki.model_3d.rotation.x,
                rot_y=ki.model_3d.rotation.y,
                rot_z=ki.model_3d.rotation.z,
            )

        ki_lib += KI_END_FILE

        Path(footprint_full_path).parent.mkdir(parents=True, exist_ok=True)
        with open(
            file=footprint_full_path,
            mode="w",
            encoding="utf-8",
        ) as my_lib:
            my_lib.write(ki_lib)
