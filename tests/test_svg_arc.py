"""Unit tests for svg_arc_geometry, checked at the mid-point of the arc."""

import math
from typing import Any

import pytest

from easyeda2kicad.easyeda.svg_path_parser import svg_arc_geometry


def _svg_arc_mid_point(**kwargs: Any) -> tuple[float, float]:
    return svg_arc_geometry(**kwargs).point(0.5)


# Tolerance for floating-point comparisons (in SVG pixel units)
TOL = 1e-9


def on_ellipse(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    x: float,
    y: float,
    tol: float = 1e-6,
) -> bool:
    """Return True if (x, y) lies on the ellipse centred at (cx, cy)."""
    return abs(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 - 1.0) < tol


class TestSvgArcMidPoint:
    """The mid-point must lie on the source ellipse
    and be geometrically between start and end on the correct arc segment."""

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _arc_center(
        sx: float,
        sy: float,
        ex: float,
        ey: float,
        rx: float,
        ry: float,
        large_arc: bool,
        sweep: bool,
    ) -> tuple[float, float]:
        """Compute arc center from start/end via the SVG spec (no rotation)."""
        dx2 = (sx - ex) / 2.0
        dy2 = (sy - ey) / 2.0
        rx_sq, ry_sq = rx * rx, ry * ry
        x1_sq, y1_sq = dx2 * dx2, dy2 * dy2
        sign = -1.0 if large_arc == sweep else 1.0
        num = max(0.0, rx_sq * ry_sq - rx_sq * y1_sq - ry_sq * x1_sq)
        den = rx_sq * y1_sq + ry_sq * x1_sq
        coef = sign * math.sqrt(num / den) if den > 0 else 0.0
        cx1 = coef * (rx * dy2 / ry)
        cy1 = coef * -(ry * dx2 / rx)
        return cx1 + (sx + ex) / 2.0, cy1 + (sy + ey) / 2.0

    # ------------------------------------------------------------------
    # Basic geometry
    # ------------------------------------------------------------------

    def test_quarter_circle_small_sweep(self) -> None:
        """90° arc (small, CW sweep) on a unit circle: mid-point at 45°."""
        # Arc from (1,0) to (0,1) on unit circle, sweep=True, large_arc=False
        # Center is at (0,0). Mid-point should be at 45°: (√2/2, √2/2)
        mx, my = _svg_arc_mid_point(
            sx=1.0,
            sy=0.0,
            ex=0.0,
            ey=1.0,
            rx=1.0,
            ry=1.0,
            x_rotation_deg=0.0,
            large_arc=False,
            sweep=True,
        )
        expected_x = math.cos(math.radians(45))
        expected_y = math.sin(math.radians(45))
        assert abs(mx - expected_x) < 1e-6
        assert abs(my - expected_y) < 1e-6

    def test_half_circle_sweep_cw(self) -> None:
        """180° CW arc (-1,0)→(1,0): in SVG Y-down CW passes through (0,-1).

        SVG Y-down: CW goes 180°→270°→360°, mid at 270° = (0,-1).
        This corresponds to the visually upper half-circle on screen.
        """
        mx, my = _svg_arc_mid_point(
            sx=-1.0,
            sy=0.0,
            ex=1.0,
            ey=0.0,
            rx=1.0,
            ry=1.0,
            x_rotation_deg=0.0,
            large_arc=False,
            sweep=True,
        )
        assert abs(mx - 0.0) < 1e-6
        assert abs(my - (-1.0)) < 1e-6

    def test_half_circle_sweep_ccw(self) -> None:
        """180° CCW arc (-1,0)→(1,0): in SVG Y-down CCW passes through (0,1).

        SVG Y-down: CCW goes 180°→90°→0°, mid at 90° = (0,1).
        This corresponds to the visually lower half-circle on screen.
        """
        mx, my = _svg_arc_mid_point(
            sx=-1.0,
            sy=0.0,
            ex=1.0,
            ey=0.0,
            rx=1.0,
            ry=1.0,
            x_rotation_deg=0.0,
            large_arc=False,
            sweep=False,
        )
        assert abs(mx - 0.0) < 1e-6
        assert abs(my - 1.0) < 1e-6

    # ------------------------------------------------------------------
    # large_arc flag
    # ------------------------------------------------------------------

    def test_large_arc_vs_small_arc_differ(self) -> None:
        """large_arc=True and large_arc=False must produce different mid-points."""
        kwargs = dict(
            sx=1.0,
            sy=0.0,
            ex=-1.0,
            ey=0.0,
            rx=2.0,
            ry=1.0,
            x_rotation_deg=0.0,
            sweep=True,
        )
        mx_small, my_small = _svg_arc_mid_point(**kwargs, large_arc=False)
        mx_large, my_large = _svg_arc_mid_point(**kwargs, large_arc=True)
        assert (mx_small, my_small) != pytest.approx((mx_large, my_large), abs=1e-6)

    def test_large_arc_midpoint_on_ellipse(self) -> None:
        """large_arc=True: mid-point must still lie on the source ellipse."""
        cx, cy = self._arc_center(
            sx=1.0,
            sy=0.0,
            ex=-1.0,
            ey=0.0,
            rx=2.0,
            ry=1.0,
            large_arc=True,
            sweep=True,
        )
        mx, my = _svg_arc_mid_point(
            sx=1.0,
            sy=0.0,
            ex=-1.0,
            ey=0.0,
            rx=2.0,
            ry=1.0,
            x_rotation_deg=0.0,
            large_arc=True,
            sweep=True,
        )
        assert on_ellipse(cx, cy, rx=2.0, ry=1.0, x=mx, y=my)

    # ------------------------------------------------------------------
    # sweep flag
    # ------------------------------------------------------------------

    def test_sweep_flag_produces_opposite_halves(self) -> None:
        """Flipping sweep on a symmetric 180° arc gives opposite mid-points.

        (1,0)→(-1,0) on a unit circle: CW goes through (0,1), CCW through (0,-1)
        in SVG Y-down coordinates. Both mid-points must lie on the unit circle.
        """
        mx_cw, my_cw = _svg_arc_mid_point(
            sx=1.0,
            sy=0.0,
            ex=-1.0,
            ey=0.0,
            rx=1.0,
            ry=1.0,
            x_rotation_deg=0.0,
            large_arc=False,
            sweep=True,
        )
        mx_ccw, my_ccw = _svg_arc_mid_point(
            sx=1.0,
            sy=0.0,
            ex=-1.0,
            ey=0.0,
            rx=1.0,
            ry=1.0,
            x_rotation_deg=0.0,
            large_arc=False,
            sweep=False,
        )
        # Both mid-points must lie on the unit circle
        assert abs(mx_cw**2 + my_cw**2 - 1.0) < 1e-6
        assert abs(mx_ccw**2 + my_ccw**2 - 1.0) < 1e-6
        # CW → (0,1), CCW → (0,-1) in SVG Y-down
        assert abs(mx_cw - 0.0) < 1e-6
        assert abs(my_cw - 1.0) < 1e-6
        assert abs(mx_ccw - 0.0) < 1e-6
        assert abs(my_ccw - (-1.0)) < 1e-6

    # ------------------------------------------------------------------
    # Ellipse (rx ≠ ry)
    # ------------------------------------------------------------------

    def test_ellipse_midpoint_on_ellipse(self) -> None:
        """Mid-point of an elliptical arc lies on the ellipse."""
        rx, ry = 3.0, 1.5
        cx, cy = self._arc_center(
            sx=3.0,
            sy=0.0,
            ex=0.0,
            ey=1.5,
            rx=rx,
            ry=ry,
            large_arc=False,
            sweep=True,
        )
        mx, my = _svg_arc_mid_point(
            sx=3.0,
            sy=0.0,
            ex=0.0,
            ey=1.5,
            rx=rx,
            ry=ry,
            x_rotation_deg=0.0,
            large_arc=False,
            sweep=True,
        )
        assert on_ellipse(cx, cy, rx=rx, ry=ry, x=mx, y=my)

    # ------------------------------------------------------------------
    # Rotation
    # ------------------------------------------------------------------

    @staticmethod
    def _rotated_ellipse_point(theta_deg: float) -> tuple[float, float]:
        """Point at parameter angle theta on the ellipse rx=2, ry=1 centred at
        (3, -2) and rotated by 30°."""
        theta, phi = math.radians(theta_deg), math.radians(30.0)
        lx, ly = 2.0 * math.cos(theta), 1.0 * math.sin(theta)
        return (
            3.0 + math.cos(phi) * lx - math.sin(phi) * ly,
            -2.0 + math.sin(phi) * lx + math.cos(phi) * ly,
        )

    @pytest.mark.parametrize(
        ("large_arc", "sweep", "start_deg", "sweep_deg"),
        [(False, True, 0.0, 90.0), (True, False, 0.0, -270.0)],
    )
    def test_rotated_arc_geometry(
        self, large_arc: bool, sweep: bool, start_deg: float, sweep_deg: float
    ) -> None:
        """30°-rotated ellipse from θ=0° to θ=90°: center, radii, angles and
        every point along the arc match the rotated ellipse."""
        sx, sy = self._rotated_ellipse_point(0.0)
        ex, ey = self._rotated_ellipse_point(90.0)
        arc = svg_arc_geometry(
            sx=sx,
            sy=sy,
            ex=ex,
            ey=ey,
            rx=2.0,
            ry=1.0,
            x_rotation_deg=30.0,
            large_arc=large_arc,
            sweep=sweep,
        )
        assert (arc.center_x, arc.center_y) == pytest.approx((3.0, -2.0))
        assert (arc.radius_x, arc.radius_y) == pytest.approx((2.0, 1.0))
        assert math.degrees(arc.start_angle) == pytest.approx(start_deg, abs=1e-9)
        assert math.degrees(arc.sweep_angle) == pytest.approx(sweep_deg)
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            expected = self._rotated_ellipse_point(start_deg + sweep_deg * fraction)
            assert arc.point(fraction) == pytest.approx(expected)

    # ------------------------------------------------------------------
    # Degenerate / edge cases
    # ------------------------------------------------------------------

    def test_coincident_start_end_returns_finite(self) -> None:
        """When start == end the function must return finite values (no crash)."""
        mx, my = _svg_arc_mid_point(
            sx=1.0,
            sy=0.0,
            ex=1.0,
            ey=0.0,
            rx=1.0,
            ry=1.0,
            x_rotation_deg=0.0,
            large_arc=False,
            sweep=True,
        )
        assert math.isfinite(mx) and math.isfinite(my)

    def test_tiny_radii_scale_up(self) -> None:
        """Radii too small for the chord distance must be scaled up (no crash)."""
        # Distance between start and end is 2; rx=ry=0.1 → must be scaled
        mx, my = _svg_arc_mid_point(
            sx=-1.0,
            sy=0.0,
            ex=1.0,
            ey=0.0,
            rx=0.1,
            ry=0.1,
            x_rotation_deg=0.0,
            large_arc=False,
            sweep=True,
        )
        assert math.isfinite(mx) and math.isfinite(my)

    def test_zero_rotation_equivalent_to_no_rotation(self) -> None:
        """x_rotation_deg=0 and x_rotation_deg=360 must yield the same result."""
        args = dict(
            sx=1.0,
            sy=0.0,
            ex=0.0,
            ey=1.0,
            rx=1.0,
            ry=1.0,
            large_arc=False,
            sweep=True,
        )
        mx0, my0 = _svg_arc_mid_point(**args, x_rotation_deg=0.0)
        mx360, my360 = _svg_arc_mid_point(**args, x_rotation_deg=360.0)
        assert abs(mx0 - mx360) < TOL
        assert abs(my0 - my360) < TOL
