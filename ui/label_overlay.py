"""
ui/label_overlay.py
Reads a LabelMe JSON annotation file and draws polygon overlays
onto a numpy RGB image array using Pillow only (no OpenCV dependency).
"""

import json
import logging
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


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class LabelOverlay:
    """
    Holds all parsed shapes from a single LabelMe JSON file and renders
    them onto a numpy RGB uint8 array using Pillow.
    """

    def __init__(self, shapes: list, image_w: int, image_h: int) -> None:
        self._shapes  = shapes
        self._image_w = image_w
        self._image_h = image_h
        self._color_map: dict = {}
        self._assign_colors()

    def _assign_colors(self) -> None:
        labels = sorted({s["label"] for s in self._shapes})
        for i, label in enumerate(labels):
            self._color_map[label] = _PALETTE_RGB[i % len(_PALETTE_RGB)]

    def draw(self, rgb: np.ndarray) -> np.ndarray:
        """
        Draw all polygon shapes onto a copy of rgb.

        Args:
            rgb: H x W x 3 uint8 numpy array (RGB).

        Returns:
            New H x W x 3 uint8 array with overlays painted on.
        """
        # Base image
        base = Image.fromarray(rgb, mode="RGB")

        # Separate RGBA layer for semi-transparent fills
        fill_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        fill_draw  = ImageDraw.Draw(fill_layer)

        # Outline + text drawn directly on an RGBA copy of base
        top_layer = base.convert("RGBA")
        top_draw  = ImageDraw.Draw(top_layer)

        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", _FONT_SIZE)
        except (IOError, OSError):
            font = ImageFont.load_default()

        for shape in self._shapes:
            if shape.get("shape_type") != "polygon":
                continue

            label = shape["label"]
            pts   = shape["points"]
            color = self._color_map.get(label, (255, 255, 255))
            color_fill    = color + (_FILL_ALPHA,)        # RGBA with alpha
            color_outline = color + (220,)                # near-opaque outline

            # Convert points to flat tuple list for Pillow
            poly_pts = [tuple(p) for p in pts]

            # Semi-transparent fill
            fill_draw.polygon(poly_pts, fill=color_fill)

            # Solid outline
            top_draw.line(poly_pts + [poly_pts[0]], fill=color_outline, width=_OUTLINE_WIDTH)

            # Vertex points
            for px, py in poly_pts:
                r = _POINT_RADIUS
                top_draw.ellipse(
                    [(px - r, py - r), (px + r, py + r)],
                    fill=color_outline,
                    outline=(0, 0, 0, 220),
                    width=2,
                )

            # Label text at topmost point
            top_pt  = min(pts, key=lambda p: p[1])
            tx = int(top_pt[0]) + 6
            ty = int(top_pt[1]) - _FONT_SIZE - 6

            # Shadow
            top_draw.text((tx + 1, ty + 1), label, font=font, fill=(0, 0, 0, 220))
            # Label
            top_draw.text((tx, ty), label, font=font, fill=color_outline)

        # Composite: base → fill layer → outline/text layer
        base_rgba = base.convert("RGBA")
        base_rgba = Image.alpha_composite(base_rgba, fill_layer)
        base_rgba = Image.alpha_composite(base_rgba, top_layer)

        return np.array(base_rgba.convert("RGB"), dtype=np.uint8)

    @property
    def label_count(self) -> int:
        return len(self._shapes)

    @property
    def label_names(self) -> list:
        return sorted({s["label"] for s in self._shapes})


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_label_overlay(dcm_path: Path) -> Optional[LabelOverlay]:
    """
    Look for a LabelMe JSON in Labeled/ matching the given DICOM file.
    Returns a LabelOverlay if found and valid, None otherwise.

    Handles labelme's optional numeric timestamp prefix:
        e.g. '1776400910652_<stem>.json'
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

        polygon_shapes = [s for s in shapes if s.get("shape_type") == "polygon"]
        if not polygon_shapes:
            return None

        log.info("Loaded %d polygon(s) from '%s'.", len(polygon_shapes), json_path.name)
        return LabelOverlay(polygon_shapes, image_w, image_h)

    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        log.warning("Could not parse label JSON '%s': %s", json_path.name, exc)
        return None