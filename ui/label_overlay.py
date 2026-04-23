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
# imagePath resolution (M2)
# ---------------------------------------------------------------------------

def _resolve_image_path(raw_path: str, json_path: Path) -> Optional[Path]:
    """
    Resolve an imagePath string from a LabelMe annotation to an existing file.

    LabelMe bakes an absolute path into its JSON at save time. After a pack
    round-trip, that path may be stale (different machine) or relative
    (./stem.jpg, the portable form written by create_pack). This function
    tries both interpretations without raising.

    Resolution order:
    1. The path as-is — covers absolute paths valid on the current machine.
    2. Name-only resolution relative to the JSON's parent directory — covers
       both ./stem.jpg and any other relative form, regardless of prefix.
    3. None if neither candidate resolves to an existing file.

    Args:
        raw_path:  The imagePath string from the LabelMe JSON.
        json_path: Path to the annotation .json file on disk.

    Returns:
        A Path that exists on disk, or None.
    """
    if not raw_path:
        return None

    candidate = Path(raw_path)

    if candidate.is_absolute() and candidate.exists():
        return candidate

    # Strip any directory prefix and look next to the annotation file.
    # This handles both "./stem.jpg" (portable) and stale absolute paths
    # whose basename still matches a sibling raster.
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
    """

    def __init__(
        self,
        shapes: list,
        image_w: int,
        image_h: int,
        image_path: Optional[Path] = None,
    ) -> None:
        self._shapes     = shapes
        self._image_w    = image_w
        self._image_h    = image_h
        self._image_path = image_path
        self._color_map: dict = {}
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
        """
        Resolved path to the raster image associated with this annotation.

        Returns an existing Path when resolution succeeded in load_label_overlay,
        or None when the imagePath field was absent, empty, or unresolvable on
        the current machine (e.g. stale path from a foreign machine whose pack
        extraction patching failed).
        """
        return self._image_path

    # ------------------------------------------------------------------
    # Colour assignment
    # ------------------------------------------------------------------

    def _assign_colors(self) -> None:
        labels = sorted({s["label"] for s in self._shapes})
        for i, label in enumerate(labels):
            self._color_map[label] = _PALETTE_RGB[i % len(_PALETTE_RGB)]

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

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
            color_fill    = color + (_FILL_ALPHA,)
            color_outline = color + (220,)

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

            top_pt = min(pts, key=lambda p: p[1])
            tx = int(top_pt[0]) + 6
            ty = int(top_pt[1]) - _FONT_SIZE - 6

            # Shadow
            top_draw.text((tx + 1, ty + 1), label, font=font, fill=(0, 0, 0, 220))
            top_draw.text((tx, ty),          label, font=font, fill=color_outline)

        # Composite: base → fill layer → outline/text layer
        base_rgba = base.convert("RGBA")
        base_rgba = Image.alpha_composite(base_rgba, fill_layer)
        base_rgba = Image.alpha_composite(base_rgba, top_layer)

        return np.array(base_rgba.convert("RGB"), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_label_overlay(dcm_path: Path) -> Optional[LabelOverlay]:
    """
    Look for a LabelMe JSON in Labeled/ matching the given DICOM file.
    Returns a LabelOverlay if found and valid, None otherwise.

    Handles labelme's optional numeric timestamp prefix:
        e.g. '1776400910652_<stem>.json'

    The annotation's imagePath is resolved to an existing file via
    _resolve_image_path() and stored on the returned LabelOverlay. If the
    path cannot be resolved (stale absolute path from a foreign machine,
    missing raster, or empty field), image_path will be None — this is
    non-fatal because the overlay renderer operates on the DICOM pixel
    data passed to draw(), not on the raster file.
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

        image_path_str = data.get("imagePath", "")
        resolved_path  = _resolve_image_path(image_path_str, json_path)

        if image_path_str and resolved_path is None:
            log.debug(
                "imagePath '%s' in '%s' does not resolve to an existing file.",
                image_path_str, json_path.name,
            )

        log.info(
            "Loaded %d polygon(s) from '%s' (imagePath resolved=%s).",
            len(polygon_shapes), json_path.name, resolved_path is not None,
        )
        return LabelOverlay(polygon_shapes, image_w, image_h, image_path=resolved_path)

    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        log.warning("Could not parse label JSON '%s': %s", json_path.name, exc)
        return None