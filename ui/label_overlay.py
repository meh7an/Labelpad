"""
ui/label_overlay.py
Reads a LabelMe JSON annotation file and draws polygon and circle overlays
onto a numpy RGB image array using Pillow only (no OpenCV dependency).
"""

import json
import logging
import math
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core.paths import LABELED_DIR

log = logging.getLogger(__name__)

_LABELED_DIR = LABELED_DIR

# High-contrast palette for dark backgrounds (RGB tuples)
_PALETTE_RGB = [
    (255, 100, 100),   # soft red
    (100, 220, 100),   # soft green
    (100, 160, 255),   # sky blue
    (255, 210,  80),   # amber
    (200, 100, 255),   # violet
    ( 80, 220, 220),   # cyan
    (255, 160,  80),   # orange
    (180, 255, 100),   # lime
    (255, 100, 200),   # pink
    (100, 200, 160),   # teal
]

_OUTLINE_WIDTH = 8
_POINT_RADIUS  = 6
_FILL_ALPHA    = 60    # 0-255, polygon fill opacity
_FONT_SIZE     = 28

_SUPPORTED_SHAPE_TYPES = {"polygon", "circle"}


# ---------------------------------------------------------------------------
# imagePath resolution (M2)
# ---------------------------------------------------------------------------

def _resolve_image_path(raw_path: str, json_path: Path) -> Optional[Path]:
    """
    Resolve an imagePath string from a LabelMe annotation to an existing file.

    Resolution order:
    1. The path as-is — covers absolute paths valid on the current machine.
    2. Name-only resolution relative to the JSON's parent directory — covers
       both ./stem.jpg and any other relative form, regardless of prefix.
    3. None if neither candidate resolves to an existing file.
    """
    if not raw_path:
        return None

    candidate = Path(raw_path)

    if candidate.is_absolute() and candidate.exists():
        return candidate

    sibling = (json_path.parent / candidate.name).resolve()
    if sibling.exists():
        return sibling

    return None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class LabelOverlay:
    """
    Holds all parsed shapes from a single LabelMe JSON file and renders
    them onto a numpy RGB uint8 array using Pillow.

    Supported shape types:
        - polygon  — arbitrary closed polygon.
        - circle   — LabelMe encoding: points[0] = center,
                     points[1] = any point on the circumference.
                     Radius is derived as the Euclidean distance between them.

    Args:
        mandatory_labels: Labels that every file in the parent folder must
                          carry to reach Labeled status.  Used to compute
                          missing_labels and mandatory_progress_text.
    """

    def __init__(
        self,
        shapes:           list,
        image_w:          int,
        image_h:          int,
        image_path:       Optional[Path]      = None,
        mandatory_labels: tuple[str, ...]     = (),
    ) -> None:
        self._shapes           = shapes
        self._image_w          = image_w
        self._image_h          = image_h
        self._image_path       = image_path
        self._mandatory_labels = mandatory_labels
        self._color_map: dict  = {}
        self._assign_colors()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def label_count(self) -> int:
        return len(self._shapes)

    @property
    def label_names(self) -> list:
        return sorted({s["label"] for s in self._shapes})

    @property
    def image_path(self) -> Optional[Path]:
        return self._image_path

    @property
    def color_map(self) -> dict:
        """Shallow copy of the label → RGB tuple mapping."""
        return dict(self._color_map)

    @property
    def missing_labels(self) -> set[str]:
        """
        Mandatory labels not yet present in this annotation.
        Returns an empty set when no mandatory labels are defined.
        """
        if not self._mandatory_labels:
            return set()
        present = {s["label"] for s in self._shapes if "label" in s}
        return set(self._mandatory_labels) - present

    @property
    def mandatory_progress_text(self) -> str:
        """
        Human-readable progress fraction, e.g. '2\u202f/\u202f4'.
        Returns an empty string when no mandatory labels are defined.
        """
        if not self._mandatory_labels:
            return ""
        n_total = len(self._mandatory_labels)
        n_done  = n_total - len(self.missing_labels)
        return f"{n_done}\u202f/\u202f{n_total}"

    # ------------------------------------------------------------------
    # Colour assignment
    # ------------------------------------------------------------------

    def _assign_colors(self) -> None:
        labels = sorted({s["label"] for s in self._shapes})
        for i, label in enumerate(labels):
            self._color_map[label] = _PALETTE_RGB[i % len(_PALETTE_RGB)]

    # ------------------------------------------------------------------
    # Shape-specific drawing helpers
    # ------------------------------------------------------------------

    def _draw_polygon(
        self,
        fill_draw: ImageDraw.ImageDraw,
        top_draw:  ImageDraw.ImageDraw,
        pts:       list,
        color_fill:    tuple,
        color_outline: tuple,
        font,
        label: str,
    ) -> None:
        poly_pts = [tuple(p) for p in pts]

        fill_draw.polygon(poly_pts, fill=color_fill)
        top_draw.line(poly_pts + [poly_pts[0]], fill=color_outline, width=_OUTLINE_WIDTH)

        for px, py in poly_pts:
            r = _POINT_RADIUS
            top_draw.ellipse(
                [(px - r, py - r), (px + r, py + r)],
                fill=color_outline,
                outline=(0, 0, 0, 220),
                width=2,
            )

        top_pt = min(pts, key=lambda p: p[1])
        self._draw_label(top_draw, font, label, top_pt, color_outline)

    def _draw_circle(
        self,
        fill_draw: ImageDraw.ImageDraw,
        top_draw:  ImageDraw.ImageDraw,
        pts:       list,
        color_fill:    tuple,
        color_outline: tuple,
        font,
        label: str,
    ) -> None:
        """
        LabelMe circle encoding:
            pts[0] = (cx, cy)  — centre
            pts[1] = (ex, ey)  — any point on the circumference
        Radius = Euclidean distance between the two stored points.
        """
        if len(pts) < 2:
            log.warning("Circle shape for label '%s' has fewer than 2 points; skipped.", label)
            return

        cx, cy = pts[0]
        ex, ey = pts[1]
        radius = math.hypot(ex - cx, ey - cy)

        bbox = [(cx - radius, cy - radius), (cx + radius, cy + radius)]

        fill_draw.ellipse(bbox, fill=color_fill)
        top_draw.ellipse(bbox, outline=color_outline, width=_OUTLINE_WIDTH)

        # Draw centre crosshair
        ch = _POINT_RADIUS
        top_draw.line([(cx - ch, cy), (cx + ch, cy)], fill=color_outline, width=3)
        top_draw.line([(cx, cy - ch), (cx, cy + ch)], fill=color_outline, width=3)

        self._draw_label(top_draw, font, label, (cx, cy - radius), color_outline)

    @staticmethod
    def _draw_label(
        draw:       ImageDraw.ImageDraw,
        font,
        label:      str,
        anchor_pt:  tuple,
        color_outline: tuple,
    ) -> None:
        tx = int(anchor_pt[0]) + 6
        ty = int(anchor_pt[1]) - _FONT_SIZE - 6
        draw.text((tx + 1, ty + 1), label, font=font, fill=(0, 0, 0, 220))
        draw.text((tx, ty),          label, font=font, fill=color_outline)

    # ------------------------------------------------------------------
    # Main render entry point
    # ------------------------------------------------------------------

    def draw(self, rgb: np.ndarray) -> np.ndarray:
        """
        Draw all supported shapes onto a copy of rgb.

        Args:
            rgb: H x W x 3 uint8 numpy array (RGB).

        Returns:
            New H x W x 3 uint8 array with overlays painted on.
        """
        base = Image.fromarray(rgb, mode="RGB")

        fill_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        fill_draw  = ImageDraw.Draw(fill_layer)

        top_layer = base.convert("RGBA")
        top_draw  = ImageDraw.Draw(top_layer)

        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", _FONT_SIZE)
        except (IOError, OSError):
            font = ImageFont.load_default()

        for shape in self._shapes:
            shape_type = shape.get("shape_type")
            if shape_type not in _SUPPORTED_SHAPE_TYPES:
                continue

            label         = shape["label"]
            pts           = shape["points"]
            color         = self._color_map.get(label, (255, 255, 255))
            color_fill    = color + (_FILL_ALPHA,)
            color_outline = color + (220,)

            if shape_type == "polygon":
                self._draw_polygon(
                    fill_draw, top_draw, pts,
                    color_fill, color_outline, font, label,
                )
            elif shape_type == "circle":
                self._draw_circle(
                    fill_draw, top_draw, pts,
                    color_fill, color_outline, font, label,
                )

        base_rgba = base.convert("RGBA")
        base_rgba = Image.alpha_composite(base_rgba, fill_layer)
        base_rgba = Image.alpha_composite(base_rgba, top_layer)

        return np.array(base_rgba.convert("RGB"), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_label_overlay(
    dcm_path:         Path,
    mandatory_labels: tuple[str, ...] = (),
) -> Optional[LabelOverlay]:
    """
    Look for a LabelMe JSON in Labeled/ matching the given DICOM file.
    Returns a LabelOverlay if found and valid, None otherwise.

    Handles labelme's optional numeric timestamp prefix:
        e.g. '1776400910652_<stem>.json'

    Args:
        dcm_path:         Path to the .dcm file (stem used for lookup).
        mandatory_labels: Forwarded to LabelOverlay for progress tracking.
    """
    stem = dcm_path.stem

    candidates = [
        p for p in _LABELED_DIR.glob("*.json")
        if p.stem == stem or p.stem.endswith(f"_{stem}")
    ]

    if not candidates:
        return None

    json_path = candidates[0]

    try:
        data    = json.loads(json_path.read_text(encoding="utf-8"))
        shapes  = data.get("shapes", [])
        image_w = int(data.get("imageWidth",  0))
        image_h = int(data.get("imageHeight", 0))

        supported_shapes = [
            s for s in shapes
            if s.get("shape_type") in _SUPPORTED_SHAPE_TYPES
        ]
        if not supported_shapes:
            return None

        image_path_str = data.get("imagePath", "")
        resolved_path  = _resolve_image_path(image_path_str, json_path)

        if image_path_str and resolved_path is None:
            log.debug(
                "imagePath '%s' in '%s' does not resolve to an existing file.",
                image_path_str, json_path.name,
            )

        shape_summary = {
            t: sum(1 for s in supported_shapes if s.get("shape_type") == t)
            for t in _SUPPORTED_SHAPE_TYPES
        }
        log.info(
            "Loaded %s from '%s' (imagePath resolved=%s).",
            ", ".join(f"{v} {k}(s)" for t, v in shape_summary.items() if (k := t) and v > 0),
            json_path.name,
            resolved_path is not None,
        )
        return LabelOverlay(
            supported_shapes, image_w, image_h,
            image_path=resolved_path,
            mandatory_labels=mandatory_labels,
        )

    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        log.warning("Could not parse label JSON '%s': %s", json_path.name, exc)
        return None