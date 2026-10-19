from __future__ import annotations

# Global imports
import logging
import math
import re
from pathlib import Path
from typing import NamedTuple, Sequence

# Local imports
from .._version import GENERATOR_URL
from ..easyeda.parameters_easyeda import (
    EasyedaPinType,
    EeSymbol,
    EeSymbolArc,
    EeSymbolBbox,
    EeSymbolCircle,
    EeSymbolEllipse,
    EeSymbolPath,
    EeSymbolPin,
    EeSymbolPolygon,
    EeSymbolPolyline,
    EeSymbolRectangle,
    EeSymbolText,
)
from ..easyeda.svg_path_parser import (
    Point,
    SvgArc,
    SvgCubic,
    SvgLine,
    SvgSubpath,
    is_zero_radius,
    parse_svg_path,
    parse_svg_points,
)
from .parameters_kicad_symbol import (
    KICAD_SYM_VERSIONS_SORTED,
    KiPinStyle,
    KiPinType,
    KiSymbol,
    KiSymbolArc,
    KiSymbolBezier,
    KiSymbolCircle,
    KiSymbolInfo,
    KiSymbolPin,
    KiSymbolPolygon,
    KiSymbolRectangle,
    KiSymbolText,
    overbar_active_low,
)

_SYM_LIB_REGEX = r'\n(\s*)\(symbol "{component_name}".*?\n\1\)(?=\n|$)'


def read_symbol_lib_version(lib_path: str | None) -> int:
    """Return the .kicad_sym format version to use for the given library file.

    - If lib_path is None or the file cannot be read: oldest known version.
    - If the file has no (version ...) field: oldest known version.
    - Otherwise: the largest known version that is <= the version in the file.
    """
    if lib_path is not None:
        try:
            with open(lib_path, encoding="utf-8") as f:
                content = f.read(512)  # version is always near the top
            match = re.search(r"\(version\s+(\d+)\)", content)
            if match:
                file_version = int(match.group(1))
                result = KICAD_SYM_VERSIONS_SORTED[0]
                for v in KICAD_SYM_VERSIONS_SORTED:
                    if v <= file_version:
                        result = v
                return result
        except OSError:
            pass
    return KICAD_SYM_VERSIONS_SORTED[0]


def id_already_in_symbol_lib(lib_path: str, component_name: str) -> bool:
    if not Path(lib_path).is_file():
        return False
    with open(lib_path, encoding="utf-8") as lib_file:
        current_lib = lib_file.read()
        component = re.findall(
            _SYM_LIB_REGEX.format(component_name=re.escape(component_name)),
            current_lib,
            flags=re.DOTALL,
        )
        if component != []:
            logging.warning(f"This id is already in {lib_path}")
            return True
    return False


def write_component_in_symbol_lib_file(
    lib_path: str,
    component_name: str,
    component_content: str,
    version: int = KICAD_SYM_VERSIONS_SORTED[0],
    generator: str = GENERATOR_URL,
) -> None:
    """Write a symbol into the library, replacing it if it already exists."""
    if not Path(lib_path).is_file():
        Path(lib_path).write_text(
            f"(kicad_symbol_lib\n  (version {version})\n  (generator {generator})\n)",
            encoding="utf-8",
        )
        logging.debug(f"Created symbol lib: {lib_path}")

    current = Path(lib_path).read_text(encoding="utf-8")

    pattern = _SYM_LIB_REGEX.format(component_name=re.escape(component_name))
    if re.search(pattern, current, flags=re.DOTALL):
        # Symbol exists — replace it
        new_lib = re.sub(
            pattern, component_content.rstrip("\n"), current, flags=re.DOTALL
        )
    else:
        # Symbol is new — insert before closing parenthesis
        last_paren_pos = current.rfind(")")
        if last_paren_pos == -1:
            raise ValueError("Invalid KiCad library file: no closing parenthesis found")
        sep = "" if component_content.endswith("\n") else "\n"
        new_lib = (
            current[:last_paren_pos]
            + component_content
            + sep
            + current[last_paren_pos:]
        )

    new_lib = new_lib.replace(
        "(generator kicad_symbol_editor)", f"(generator {GENERATOR_URL})"
    )
    Path(lib_path).write_text(new_lib, encoding="utf-8")


# EasyEDA uses a 5px grid (= 1.27mm = 50mil). Snapping bbox coordinates to this
# boundary ensures pin coordinates land on the KiCad grid after subtraction.
_EASYEDA_SYMBOL_GRID_PX = 5

ee_pin_type_to_ki_pin_type = {
    EasyedaPinType.unspecified: KiPinType.unspecified,
    EasyedaPinType._input: KiPinType._input,
    EasyedaPinType.output: KiPinType.output,
    EasyedaPinType.bidirectional: KiPinType.bidirectional,
    EasyedaPinType.power: KiPinType.power_in,
}


def px_to_mm(dim: int | float | str) -> float:
    return 10.0 * float(dim) * 0.0254


def px_to_mm_grid(dim: int | float | str, grid: float = 1.27) -> float:
    """Convert EasyEDA pixels to KiCad mm and snap to grid (default 50mil = 1.27mm)."""
    mm_value = 10.0 * float(dim) * 0.0254
    return round(mm_value / grid) * grid


def snap_bbox(
    ee_bbox: EeSymbolBbox, grid_px: int = _EASYEDA_SYMBOL_GRID_PX
) -> tuple[float, float]:
    """Round bbox origin to the nearest grid_px boundary (5px = 1.27mm = 50mil).

    EasyEDA bbox coordinates often contain sub-pixel values. Snapping to the
    nearest 5px boundary ensures that pin coordinates (which are typically
    integer multiples of 5px in absolute space) land on the KiCad 1.27mm grid
    after subtraction, without per-coordinate rounding.
    """
    snapped_x = round(float(ee_bbox.x) / grid_px) * grid_px
    snapped_y = round(float(ee_bbox.y) / grid_px) * grid_px
    return snapped_x, snapped_y


def _ki_point(point: Point, ee_bbox: EeSymbolBbox) -> Point:
    """EasyEDA symbol pixel point → KiCad mm, relative to the bbox, y up."""
    return px_to_mm(point[0] - ee_bbox.x), -px_to_mm(point[1] - ee_bbox.y)


def _pin_length(path: str) -> float:
    """Length in px of the pin line drawn by the pin path (M x y h length)."""
    try:
        subpaths = parse_svg_path(path)
    except ValueError as e:
        logging.warning(f"Pin path {path!r}: {e}; using length 0")
        return 0.0
    if not subpaths or not subpaths[0].segments:
        logging.warning(f"Pin path {path!r} draws no line; using length 0")
        return 0.0
    return math.dist(subpaths[0].start, subpaths[0].segments[-1].end)


def convert_ee_pins(
    ee_pins: list[EeSymbolPin], ee_bbox: EeSymbolBbox
) -> list[KiSymbolPin]:
    kicad_pins = []
    for ee_pin in ee_pins:
        pin_length = _pin_length(ee_pin.pin_path.path)

        ki_pin = KiSymbolPin(
            # KiCad pin names and numbers cannot contain spaces; the overbar
            # needs them as word boundaries
            name=overbar_active_low(ee_pin.name.text).replace(" ", "_"),
            number=ee_pin.settings.spice_pin_number.replace(" ", "_"),
            style=KiPinStyle.line,
            length=px_to_mm_grid(pin_length),
            type=ee_pin_type_to_ki_pin_type[ee_pin.settings.type],
            orientation=ee_pin.settings.rotation,
            pos_x=px_to_mm_grid(ee_pin.settings.pos_x - ee_bbox.x),
            pos_y=-px_to_mm_grid(ee_pin.settings.pos_y - ee_bbox.y),
        )

        if ee_pin.dot.is_displayed and ee_pin.clock.is_displayed:
            ki_pin.style = KiPinStyle.inverted_clock
        elif ee_pin.dot.is_displayed:
            ki_pin.style = KiPinStyle.inverted
        elif ee_pin.clock.is_displayed:
            ki_pin.style = KiPinStyle.clock

        kicad_pins.append(ki_pin)

    return kicad_pins


def convert_ee_rectangles(
    ee_rectangles: list[EeSymbolRectangle],
    ee_bbox: EeSymbolBbox,
) -> list[KiSymbolRectangle]:
    kicad_rectangles = []
    for ee_rectangle in ee_rectangles:
        ki_rectangle = KiSymbolRectangle(
            pos_x0=px_to_mm(ee_rectangle.pos_x - ee_bbox.x),
            pos_y0=-px_to_mm(ee_rectangle.pos_y - ee_bbox.y),
        )
        ki_rectangle.pos_x1 = px_to_mm(ee_rectangle.width) + ki_rectangle.pos_x0
        ki_rectangle.pos_y1 = -px_to_mm(ee_rectangle.height) + ki_rectangle.pos_y0

        kicad_rectangles.append(ki_rectangle)

    return kicad_rectangles


def convert_ee_circles(
    ee_circles: list[EeSymbolCircle],
    ee_bbox: EeSymbolBbox,
) -> list[KiSymbolCircle]:
    return [
        KiSymbolCircle(
            pos_x=px_to_mm(ee_circle.center_x - ee_bbox.x),
            pos_y=-px_to_mm(ee_circle.center_y - ee_bbox.y),
            radius=px_to_mm(ee_circle.radius),
            background_filling=ee_circle.fill_color,
        )
        for ee_circle in ee_circles
    ]


def convert_ee_ellipses(
    ee_ellipses: list[EeSymbolEllipse],
    ee_bbox: EeSymbolBbox,
) -> KiSymbolShapes:
    """Convert ellipses (E) to circles, or to polylines when not circular."""
    shapes = KiSymbolShapes([], [], [], [])
    for ee_ellipse in ee_ellipses:
        center = (ee_ellipse.center_x, ee_ellipse.center_y)
        rx, ry = ee_ellipse.radius_x, ee_ellipse.radius_y
        if is_zero_radius(rx) or is_zero_radius(ry):
            logging.warning(f"Skipping ellipse {ee_ellipse.id} with zero radius")
            continue
        if rx == ry:
            pos_x, pos_y = _ki_point(center, ee_bbox)
            shapes.circles.append(
                KiSymbolCircle(
                    pos_x=pos_x,
                    pos_y=pos_y,
                    radius=px_to_mm(rx),
                    background_filling=ee_ellipse.fill_color,
                )
            )
            continue
        right, left = (center[0] + rx, center[1]), (center[0] - rx, center[1])
        ellipse = SvgSubpath(
            start=right,
            segments=[
                SvgArc(rx, ry, 0.0, False, True, left),
                SvgArc(rx, ry, 0.0, False, True, right),
            ],
            closed=True,
        )
        _add_subpath(shapes, ellipse, ee_ellipse.fill_color, ee_bbox)
    return shapes


def convert_ee_polylines(
    ee_polylines: Sequence[EeSymbolPolyline | EeSymbolPolygon],
    ee_bbox: EeSymbolBbox,
) -> list[KiSymbolPolygon]:
    """Convert polylines (PL) and polygons (PG); polygons and filled polylines
    with more than two points are closed. Malformed point lists and ones with
    fewer than two points are skipped with a warning."""
    kicad_polygons = []
    for ee_polyline in ee_polylines:
        try:
            points = [
                _ki_point(point, ee_bbox)
                for point in parse_svg_points(ee_polyline.points)
            ]
        except ValueError as e:
            logging.warning(f"Skipping polyline: {e}")
            continue
        if len(points) < 2:
            logging.warning(f"Skipping polyline with {len(points)} point(s)")
            continue
        closes = isinstance(ee_polyline, EeSymbolPolygon) or ee_polyline.fill_color
        if closes and len(points) > 2:
            points.append(points[0])
        kicad_polygons.append(_polygon(points, ee_polyline.fill_color))
    return kicad_polygons


# EasyEDA stores full circles as arcs whose end lies just off the start; an end
# closer than this share of the radius marks such a circle
_FULL_CIRCLE_ARC_GAP = 0.01


def _convert_arc(
    start: Point,
    center: Point,
    sweep: float,
    fill: bool,
    ee_bbox: EeSymbolBbox,
) -> KiSymbolArc | KiSymbolCircle:
    """Convert a circular arc to a KiCad arc, or to a circle if nearly closed."""
    radius = math.dist(center, start)
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])

    def point(angle: float) -> Point:
        return _ki_point(
            (
                center[0] + radius * math.cos(start_angle + angle),
                center[1] + radius * math.sin(start_angle + angle),
            ),
            ee_bbox,
        )

    # 2 |sin(sweep / 2)| is the distance between start and end per radius
    if abs(sweep) > math.pi and 2 * abs(math.sin(sweep / 2)) <= _FULL_CIRCLE_ARC_GAP:
        pos_x, pos_y = _ki_point(center, ee_bbox)
        return KiSymbolCircle(
            pos_x=pos_x, pos_y=pos_y, radius=px_to_mm(radius), background_filling=fill
        )
    # The y flip mirrors the arc and so reverses its direction; swapping start
    # and end keeps the mid point on the correct side of the chord
    (start_x, start_y), (mid_x, mid_y), (end_x, end_y) = (
        point(sweep),
        point(sweep / 2),
        point(0.0),
    )
    return KiSymbolArc(
        start_x=start_x,
        start_y=start_y,
        middle_x=mid_x,
        middle_y=mid_y,
        end_x=end_x,
        end_y=end_y,
        # angle_start != angle_end (default 0.0) disables background fill
        angle_start=1.0,
        angle_end=0.0,
    )


def _polygon(points: list[Point], fill: bool) -> KiSymbolPolygon:
    """Polyline through the points, filled if it has a fill and is closed."""
    return KiSymbolPolygon(points=points, filled=fill and points[0] == points[-1])


class KiSymbolShapes(NamedTuple):
    polygons: list[KiSymbolPolygon]
    beziers: list[KiSymbolBezier]
    arcs: list[KiSymbolArc]
    circles: list[KiSymbolCircle]


def _merge(*shapes: KiSymbolShapes) -> KiSymbolShapes:
    return KiSymbolShapes(*(sum(lists, []) for lists in zip(*shapes)))


def _add_subpath(
    shapes: KiSymbolShapes, subpath: SvgSubpath, fill: bool, ee_bbox: EeSymbolBbox
) -> None:
    """Add a subpath as KiCad shapes.

    KiCad fills single shapes only, so a filled closed subpath with curves or
    arcs becomes one polyline along its flattened outline. Otherwise
    consecutive lines become one polyline, cubic curves Béziers and circular
    arcs arcs, or circles when nearly closed; other arcs join the polyline
    flattened.
    """
    closed = bool(subpath.segments) and subpath.segments[-1].end == subpath.start
    if fill and closed and not all(isinstance(s, SvgLine) for s in subpath.segments):
        outline = [_ki_point(point, ee_bbox) for point in subpath.flatten()]
        shapes.polygons.append(_polygon(outline, fill))
        return

    polyline = [_ki_point(subpath.start, ee_bbox)]
    for start, segment in subpath.walk():
        end = _ki_point(segment.end, ee_bbox)
        circle = None
        if isinstance(segment, SvgArc):
            circle = segment.geometry(start).circle()
        if not isinstance(segment, SvgCubic) and circle is None:
            line = SvgSubpath(start, [segment]).flatten()[1:]
            polyline += [_ki_point(point, ee_bbox) for point in line]
            continue
        if len(polyline) > 1:
            shapes.polygons.append(_polygon(polyline, fill))
        if isinstance(segment, SvgCubic):
            shapes.beziers.append(
                KiSymbolBezier(
                    points=[
                        _ki_point(start, ee_bbox),
                        _ki_point(segment.control1, ee_bbox),
                        _ki_point(segment.control2, ee_bbox),
                        end,
                    ]
                )
            )
        elif circle is not None:
            shape = _convert_arc(start, *circle, fill, ee_bbox)
            if isinstance(shape, KiSymbolCircle):
                shapes.circles.append(shape)
            else:
                shapes.arcs.append(shape)
        polyline = [end]
    if len(polyline) > 1:
        shapes.polygons.append(_polygon(polyline, fill))


def convert_ee_paths(
    ee_paths: Sequence[EeSymbolPath | EeSymbolArc],
    ee_bbox: EeSymbolBbox,
) -> KiSymbolShapes:
    """Convert path (PT) and arc (A) shapes to KiCad shapes (see _add_subpath).
    Malformed paths are skipped with a warning."""
    shapes = KiSymbolShapes([], [], [], [])
    for ee_path in ee_paths:
        try:
            subpaths = parse_svg_path(ee_path.path)
        except ValueError as e:
            logging.warning(f"Skipping symbol path: {e}")
            continue
        if not any(subpath.segments for subpath in subpaths):
            logging.warning(f"Skipping symbol path {ee_path.path!r} without segments")
            continue
        for subpath in subpaths:
            _add_subpath(shapes, subpath, ee_path.fill_color, ee_bbox)
    return shapes


def convert_ee_texts(
    ee_texts: list[EeSymbolText],
    ee_bbox: EeSymbolBbox,
) -> list[KiSymbolText]:
    return [
        KiSymbolText(
            text=t.text,
            pos_x=px_to_mm(t.pos_x - ee_bbox.x),
            pos_y=-px_to_mm(t.pos_y - ee_bbox.y),
            # EasyEDA rotates clockwise (y down), KiCad counter-clockwise (y up).
            rotation=-t.rotation % 360,
            font_size=t.font_size,
        )
        for t in ee_texts
    ]


def convert_to_kicad(
    ee_symbol: EeSymbol, custom_fields: dict[str, str] | None = None
) -> KiSymbol:
    ki_info = KiSymbolInfo(
        name=ee_symbol.info.name,
        prefix=ee_symbol.info.prefix.replace("?", ""),
        package=ee_symbol.info.package,
        manufacturer=ee_symbol.info.manufacturer,
        mpn=ee_symbol.info.mpn,
        datasheet=ee_symbol.info.datasheet,
        lcsc_id=ee_symbol.info.lcsc_id,
        keywords=ee_symbol.info.keywords,
        description=ee_symbol.info.description,
        custom_fields=dict(custom_fields or {}),
    )

    # Snap bbox to the 5px grid (= 1.27mm) so that pin coordinates, which are
    # typically integer multiples of 5px in absolute EasyEDA space, land on the
    # KiCad grid after subtraction — without per-coordinate rounding.
    snapped_x, snapped_y = snap_bbox(ee_symbol.bbox)
    snapped_bbox = EeSymbolBbox(x=snapped_x, y=snapped_y)

    path_shapes = _merge(
        convert_ee_paths(
            ee_paths=[*ee_symbol.paths, *ee_symbol.arcs], ee_bbox=snapped_bbox
        ),
        convert_ee_ellipses(ee_ellipses=ee_symbol.ellipses, ee_bbox=snapped_bbox),
    )
    return KiSymbol(
        info=ki_info,
        pins=convert_ee_pins(ee_pins=ee_symbol.pins, ee_bbox=snapped_bbox),
        rectangles=convert_ee_rectangles(
            ee_rectangles=ee_symbol.rectangles, ee_bbox=snapped_bbox
        ),
        circles=[
            *convert_ee_circles(ee_circles=ee_symbol.circles, ee_bbox=snapped_bbox),
            *path_shapes.circles,
        ],
        arcs=path_shapes.arcs,
        polygons=[
            *path_shapes.polygons,
            *convert_ee_polylines(
                ee_polylines=[*ee_symbol.polylines, *ee_symbol.polygons],
                ee_bbox=snapped_bbox,
            ),
        ],
        beziers=path_shapes.beziers,
        texts=convert_ee_texts(ee_texts=ee_symbol.texts, ee_bbox=snapped_bbox),
    )


def tune_footprint_ref_path(ki_symbol: KiSymbol, footprint_lib_name: str) -> None:
    ki_symbol.info.package = f"{footprint_lib_name}:{ki_symbol.info.package}"


def integrate_sub_units(
    main_symbol: str,
    sub_symbols: list[str],
    component_name: str,
) -> str:
    """Integrate sub-unit symbols into a multi-unit KiCad symbol string.

    Extracts the _0_1 body block from each sub_symbol, renames it to _1_1,
    _2_1, ... and replaces the placeholder _0_1 block in main_symbol.
    Returns main_symbol unchanged if sub_symbols is empty or no match is found.
    """
    if not sub_symbols:
        return main_symbol

    name = re.escape(component_name)
    sub_units = []
    for i, sub_content in enumerate(sub_symbols, 1):
        match = re.search(
            rf'( +)\(symbol "{name}_0_1".*?\n\1\)(?=\n)', sub_content, re.DOTALL
        )
        if match:
            sub_units.append(
                match.group(0).replace(
                    f'"{component_name}_0_1"', f'"{component_name}_{i}_1"'
                )
            )

    if not sub_units:
        return main_symbol

    return re.sub(
        rf'( *)\(symbol "{name}_0_1".*?\n\1\)',
        "\n".join(sub_units),
        main_symbol,
        count=1,
        flags=re.DOTALL,
    )


class ExporterSymbolKicad:
    def __init__(
        self,
        symbol: EeSymbol,
        lib_path: str | None = None,
        version: int | None = None,
        custom_fields: dict[str, str] | None = None,
    ) -> None:
        self.input: EeSymbol = symbol
        self.version = (
            version if version is not None else read_symbol_lib_version(lib_path)
        )
        self.output = convert_to_kicad(
            ee_symbol=self.input, custom_fields=custom_fields
        )

    def export(self, footprint_lib_name: str) -> str:
        tune_footprint_ref_path(
            ki_symbol=self.output,
            footprint_lib_name=footprint_lib_name,
        )
        main_content = self.output.export(version=self.version)

        if not self.input.sub_symbols:
            return main_content

        sub_contents = [
            ExporterSymbolKicad(symbol=sub, version=self.version).export(
                footprint_lib_name=footprint_lib_name
            )
            for sub in self.input.sub_symbols
        ]
        return integrate_sub_units(
            main_symbol=main_content,
            sub_symbols=sub_contents,
            component_name=self.output.lib_id,
        )

    def save_to_lib(
        self, lib_path: str, footprint_lib_name: str, overwrite: bool
    ) -> bool:
        """Export the symbol and write it into the .kicad_sym library file.

        Returns False if the symbol already exists and overwrite is False.
        """
        already_exists = id_already_in_symbol_lib(
            lib_path=lib_path, component_name=self.output.lib_id
        )
        if already_exists and not overwrite:
            return False

        content = self.export(footprint_lib_name=footprint_lib_name)
        write_component_in_symbol_lib_file(
            lib_path=lib_path,
            component_name=self.output.lib_id,
            component_content=content,
            version=self.version,
        )
        return True
