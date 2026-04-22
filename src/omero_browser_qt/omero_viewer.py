#!/usr/bin/env python
"""
omero_viewer.py — Full-featured multi-channel OMERO viewer.
"""

from __future__ import annotations

import math
import sys
from collections import OrderedDict
from functools import lru_cache
from typing import Any

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGraphicsObject,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSlider,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
from .widgets import ArrowComboBox

# Optional 3D volume rendering via vispy
try:
    from vispy import scene as vispy_scene
    from vispy.color import BaseColormap
    from vispy.visuals.transforms import STTransform

    _HAS_VISPY = True
except ImportError:
    _HAS_VISPY = False

# ======================================================================
# Colour helpers
# ======================================================================

_FALLBACK_PALETTE = [
    (0, 255, 0),
    (255, 0, 255),
    (0, 255, 255),
    (255, 0, 0),
    (0, 0, 255),
    (255, 255, 0),
]
_RGB_CHANNEL_NAMES = ("R", "G", "B")
_RGB_CHANNEL_COLORS = (
    (220, 68, 68),
    (56, 184, 104),
    (72, 136, 255),
)

_PROJECTION_MODES = [
    "Slice",
    "MIP",
    "SUM",
    "Mean",
    "Median",
    "Extended Focus",
    "Local Contrast",
]
_PROGRESS_Z_STEP = 5
_VOLUME_METHODS = [
    "mip",
    "attenuated_mip",
    "minip",
    "translucent",
    "average",
    "iso",
    "additive",
]
_VOLUME_METHOD_LABELS = {
    "mip": "MIP",
    "attenuated_mip": "Attenuated MIP",
    "minip": "MinIP",
    "translucent": "Translucent",
    "average": "Average",
    "iso": "Isosurface",
    "additive": "Additive",
}
_VOLUME_METHOD_UI = {
    "mip": {"label": "Gain:", "range": (1, 200), "default": 100, "role": "gain"},
    "attenuated_mip": {"label": "Atten.:", "range": (1, 300), "default": 100, "role": "attenuation"},
    "minip": {"label": "Cutoff:", "range": (0, 100), "default": 100, "role": "minip_cutoff"},
    "translucent": {"label": "Gain:", "range": (1, 500), "default": 200, "role": "gain"},
    "average": {"label": "Gain:", "range": (1, 600), "default": 180, "role": "gain"},
    "iso": {"label": "Threshold:", "range": (0, 100), "default": 22, "role": "threshold"},
    "additive": {"label": "Gain:", "range": (1, 200), "default": 28, "role": "gain"},
}
_INTERPOLATION_TOGGLE_METHODS = set(_VOLUME_METHODS) - {"iso"}
_THREE_D_REFRESH_DEBOUNCE_MS = 150


class _RenderCancelled(RuntimeError):
    """Raised when an in-flight render becomes stale."""


def _emission_to_rgb(nm: float) -> tuple[int, int, int]:
    """Convert an emission wavelength (380-780 nm) to an approximate RGB."""
    if nm < 380:
        nm = 380.0
    if nm > 780:
        nm = 780.0
    if nm < 440:
        r, g, b = -(nm - 440) / (440 - 380), 0.0, 1.0
    elif nm < 490:
        r, g, b = 0.0, (nm - 440) / (490 - 440), 1.0
    elif nm < 510:
        r, g, b = 0.0, 1.0, -(nm - 510) / (510 - 490)
    elif nm < 580:
        r, g, b = (nm - 510) / (580 - 510), 1.0, 0.0
    elif nm < 645:
        r, g, b = 1.0, -(nm - 645) / (645 - 580), 0.0
    else:
        r, g, b = 1.0, 0.0, 0.0
    # Intensity fall-off at edges
    if nm < 420:
        f = 0.3 + 0.7 * (nm - 380) / (420 - 380)
    elif nm > 700:
        f = 0.3 + 0.7 * (780 - nm) / (780 - 700)
    else:
        f = 1.0
    return (int(r * f * 255), int(g * f * 255), int(b * f * 255))


def _resolve_channel_colors(
    channels: list[dict],
) -> list[tuple[int, int, int]]:
    """Return a list of RGB tuples for each channel."""
    colors = []
    for ch in channels:
        em = ch.get("emission_wavelength")
        col = ch.get("color")
        if col and col != (255, 255, 255):
            colors.append(col)
        elif em and em > 0:
            colors.append(_emission_to_rgb(em))
        else:
            colors.append(None)

    # Fill missing from palette, deduplicate
    used = set()
    for i, c in enumerate(colors):
        if c is None:
            for p in _FALLBACK_PALETTE:
                if p not in used:
                    colors[i] = p
                    break
            else:
                colors[i] = _FALLBACK_PALETTE[i % len(_FALLBACK_PALETTE)]
        used.add(colors[i])

    # If all are the same, swap to distinct palette
    if len(set(colors)) == 1 and len(colors) > 1:
        colors = [_FALLBACK_PALETTE[i % len(_FALLBACK_PALETTE)] for i in range(len(colors))]
    return colors


def _channels_look_like_rgb(channels: list[dict]) -> bool:
    """Heuristic: detect plain RGB images imported as 3 separate channels."""
    if len(channels) != 3:
        return False

    names = [str(ch.get("name", "")).strip().lower() for ch in channels]
    if names in (["r", "g", "b"], ["red", "green", "blue"]):
        return True

    rgb_like = {"", "rgb", "red", "green", "blue", "r", "g", "b"}
    if any(name not in rgb_like for name in names):
        return False

    if all(name in {"", "rgb"} for name in names):
        return True

    colors = [ch.get("color") for ch in channels]
    canonical = set(_RGB_CHANNEL_COLORS)
    normalized = {
        tuple(col) for col in colors
        if isinstance(col, tuple) and len(col) == 3 and tuple(col) in canonical
    }
    return len(normalized) == 3


def _channels_look_fluorescence_like(channels: list[dict]) -> bool:
    """Heuristic: identify fluorescence-style channels for 3D UI defaults."""
    if not channels or _channels_look_like_rgb(channels):
        return False

    fluor_markers = (
        "dapi", "fitc", "gfp", "yfp", "cfp", "rfp", "mcherry", "tdtomato",
        "tritc", "cy3", "cy5", "alexa", "hoechst", "far red",
    )
    for ch in channels:
        emission = ch.get("emission_wavelength")
        if emission is not None:
            try:
                if float(emission) > 0:
                    return True
            except (TypeError, ValueError):
                pass

        name = str(ch.get("name", "")).strip().lower()
        if any(marker in name for marker in fluor_markers):
            return True

        color = ch.get("color")
        if isinstance(color, tuple) and len(color) == 3 and len(set(color)) > 1:
            return True

    return False


# ======================================================================
# Application icon
# ======================================================================

def _make_app_icon() -> QIcon:
    """Create a microscopy-inspired app icon programmatically."""
    size = 128
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Dark circular background
    grad = QRadialGradient(size / 2, size / 2, size / 2)
    grad.setColorAt(0.0, QColor(50, 60, 80))
    grad.setColorAt(1.0, QColor(20, 25, 35))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, size - 4, size - 4)

    # Three overlapping coloured circles (fluorescence channels)
    cx, cy = size / 2, size / 2
    r = size * 0.22
    off = size * 0.12
    channels = [
        (cx - off, cy + off * 0.6, QColor(0, 200, 80, 140)),   # green
        (cx + off, cy + off * 0.6, QColor(220, 0, 80, 140)),   # magenta
        (cx, cy - off * 0.8, QColor(0, 120, 255, 140)),        # blue
    ]
    p.setPen(Qt.PenStyle.NoPen)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    for x, y, col in channels:
        g = QRadialGradient(x, y, r)
        g.setColorAt(0.0, col)
        g.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 0))
        p.setBrush(QBrush(g))
        p.drawEllipse(int(x - r), int(y - r), int(2 * r), int(2 * r))

    # Thin ring (lens outline)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    p.setPen(QPen(QColor(180, 200, 220, 160), 2.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    ring = size * 0.32
    p.drawEllipse(int(cx - ring), int(cy - ring), int(2 * ring), int(2 * ring))

    p.end()
    return QIcon(pix)


# ======================================================================
# Compositing
# ======================================================================

def _composite_to_pixmap(
    slices: list[tuple[np.ndarray, tuple[int, int, int], tuple[float, float]]],
    width: int = 0,
) -> QPixmap:
    """Composite multiple channels into a single RGB QPixmap.

    Parameters
    ----------
    slices
        List of ``(2D_array, (R,G,B), (lo_val, hi_val))`` tuples.
    width
        If > 0 the result is scaled to this width (keeping aspect).
    """
    if not slices:
        return QPixmap()

    h, w = slices[0][0].shape[:2]
    canvas = np.zeros((h, w, 3), dtype=np.float64)

    for arr, (cr, cg, cb), (lo, hi) in slices:
        if hi <= lo:
            hi = lo + 1.0
        norm = (arr.astype(np.float64) - lo) / (hi - lo)
        np.clip(norm, 0.0, 1.0, out=norm)
        canvas[..., 0] += norm * (cr / 255.0)
        canvas[..., 1] += norm * (cg / 255.0)
        canvas[..., 2] += norm * (cb / 255.0)

    np.clip(canvas, 0.0, 1.0, out=canvas)
    rgb = (canvas * 255).astype(np.uint8)
    rgb_contiguous = np.ascontiguousarray(rgb)

    qimg = QImage(
        rgb_contiguous.data, w, h, 3 * w, QImage.Format.Format_RGB888
    )
    # QImage doesn't own the buffer — copy to decouple from numpy
    pix = QPixmap.fromImage(qimg.copy())
    if width > 0 and w > 0:
        pix = pix.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
    return pix

def _neighbor_sum(arr: np.ndarray) -> np.ndarray:
    """Return the 3x3 neighborhood sum using edge padding."""
    pad = np.pad(arr, 1, mode="edge")
    return (
        pad[:-2, :-2] + pad[:-2, 1:-1] + pad[:-2, 2:]
        + pad[1:-1, :-2] + pad[1:-1, 1:-1] + pad[1:-1, 2:]
        + pad[2:, :-2] + pad[2:, 1:-1] + pad[2:, 2:]
    )


def _laplacian_metric(arr: np.ndarray) -> np.ndarray:
    pad = np.pad(arr, 1, mode="edge")
    return np.abs(
        -4.0 * pad[1:-1, 1:-1]
        + pad[:-2, 1:-1]
        + pad[2:, 1:-1]
        + pad[1:-1, :-2]
        + pad[1:-1, 2:]
    )


def _local_contrast_metric(arr: np.ndarray) -> np.ndarray:
    local_mean = _neighbor_sum(arr) / 9.0
    local_mean_sq = _neighbor_sum(arr * arr) / 9.0
    variance = np.maximum(local_mean_sq - local_mean * local_mean, 0.0)
    return np.sqrt(variance)


def _focus_fuse(stack: np.ndarray, *, metric: str, progress=None) -> np.ndarray:
    """Fuse a (Z, Y, X) stack using a per-plane sharpness metric."""
    if stack.shape[0] == 1:
        return stack[0].astype(np.float64, copy=False)

    stack_f = stack.astype(np.float64, copy=False)
    metric_fn = _laplacian_metric if metric == "laplacian" else _local_contrast_metric
    total = stack_f.shape[0] + 1
    measures = []
    for i, plane in enumerate(stack_f, start=1):
        measures.append(metric_fn(plane))
        if progress is not None and (i % _PROGRESS_Z_STEP == 0 or i == stack_f.shape[0]):
            progress(i, total)
    measures = np.stack(measures, axis=0)
    best_idx = np.argmax(measures, axis=0, keepdims=True)
    if progress is not None:
        progress(total, total)
    return np.take_along_axis(stack_f, best_idx, axis=0)[0]


def _project_stack(stack: np.ndarray, mode: str, z_index: int, progress=None) -> np.ndarray:
    """Project a (Z, Y, X) stack to a single (Y, X) plane."""
    if stack.ndim != 3:
        raise ValueError(f"Expected a 3-D stack, got shape {stack.shape}")

    if mode == "Slice":
        z = max(0, min(z_index, stack.shape[0] - 1))
        return stack[z]
    if mode == "MIP":
        return stack.max(axis=0)
    if mode == "SUM":
        return stack.sum(axis=0).astype(np.float64)
    if mode == "Mean":
        acc = np.zeros(stack.shape[1:], dtype=np.float64)
        total = stack.shape[0]
        for i, plane in enumerate(stack, start=1):
            acc += plane
            if progress is not None and (i % _PROGRESS_Z_STEP == 0 or i == total):
                progress(i, total)
        return acc / total
    if mode == "Median":
        total = 3
        if progress is not None:
            progress(1, total)
        stack_f = stack.astype(np.float64, copy=False)
        if progress is not None:
            progress(2, total)
        result = np.median(stack_f, axis=0)
        if progress is not None:
            progress(3, total)
        return result
    if mode == "Extended Focus":
        return _focus_fuse(stack, metric="laplacian", progress=progress)
    if mode == "Local Contrast":
        return _focus_fuse(stack, metric="local_contrast", progress=progress)
    raise ValueError(f"Unknown projection mode: {mode}")


def _projection_step_count(stack: np.ndarray, mode: str) -> int:
    if stack.ndim != 3:
        return 1
    if mode == "Mean":
        return max(stack.shape[0], 1)
    if mode in {"Extended Focus", "Local Contrast"}:
        return max(stack.shape[0] + 1, 1)
    if mode == "Median":
        return 3
    return 1


# ======================================================================
# ZoomableImageView
# ======================================================================

class ZoomableImageView(QGraphicsView):
    """Pannable, zoomable image viewer widget."""

    cursorMoved = pyqtSignal(float, float, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pix_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pix_item)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._scale_bar_um_per_pixel: float | None = None
        self.setStyleSheet(
            "QGraphicsView {"
            "background: transparent;"
            "border: 1px solid #43484d; border-radius: 18px; }"
            "QGraphicsView QWidget {"
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #141618, stop:1 #23272a);"
            "border-radius: 17px; }"
        )

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pix_item.setPixmap(pix)
        self._scene.setSceneRect(QRectF(pix.rect()))
        self.viewport().update()

    def set_scale_bar_um_per_pixel(self, value: float | None) -> None:
        self._scale_bar_um_per_pixel = value
        self.viewport().update()

    def fit_in_view(self) -> None:
        if self._pix_item.isVisible() and not self._pix_item.pixmap().isNull():
            target = self._pix_item.sceneBoundingRect()
        else:
            target = self._scene.itemsBoundingRect()
            if target.isNull() or target.width() <= 0 or target.height() <= 0:
                target = self._scene.sceneRect()
        self.fit_rect(target)

    def fit_rect(self, target: QRectF) -> None:
        """Fit an explicit scene rect in the view."""
        self.resetTransform()
        if target.width() > 0 and target.height() > 0:
            self.fitInView(target, Qt.AspectRatioMode.KeepAspectRatio)
            self.centerOn(target.center())
        self.viewport().update()

    def actual_size(self) -> None:
        self.resetTransform()
        if self._pix_item.isVisible() and not self._pix_item.pixmap().isNull():
            self.centerOn(self._pix_item.sceneBoundingRect().center())
        else:
            target = self._scene.itemsBoundingRect()
            if target.width() > 0 and target.height() > 0:
                self.centerOn(target.center())
        self.viewport().update()

    def wheelEvent(self, event):  # noqa: N802
        factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(factor, factor)
        else:
            self.scale(1 / factor, 1 / factor)
        self.viewport().update()

    def mouseMoveEvent(self, event):  # noqa: N802
        scene_pos = self.mapToScene(event.position().toPoint())
        self._emit_cursor(scene_pos)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.cursorMoved.emit(-1.0, -1.0, False)
        super().leaveEvent(event)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.viewport().update()

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        self._draw_scale_bar()

    def _emit_cursor(self, scene_pos: QPointF) -> None:
        rect = self._scene.sceneRect()
        inside = rect.contains(scene_pos)
        self.cursorMoved.emit(scene_pos.x(), scene_pos.y(), inside)

    def _draw_scale_bar(self) -> None:
        from omero_browser_qt import compute_scale_bar

        spec = compute_scale_bar(
            self._scale_bar_um_per_pixel,
            self.transform().m11(),
        )
        if spec is None:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = 18
        bar_y = self.viewport().height() - margin
        bar_x = margin
        label = spec.label

        text_rect = painter.fontMetrics().boundingRect(label)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(2, 6, 23, 190))
        painter.drawRoundedRect(
            bar_x - 8,
            bar_y - 34,
            int(max(spec.screen_pixels + 16, text_rect.width() + 20)),
            38,
            8,
            8,
        )

        painter.setPen(QPen(QColor("#f8fafc"), 2))
        painter.drawLine(
            int(bar_x),
            int(bar_y - 10),
            int(bar_x + spec.screen_pixels),
            int(bar_y - 10),
        )
        painter.drawLine(int(bar_x), int(bar_y - 14), int(bar_x), int(bar_y - 6))
        painter.drawLine(
            int(bar_x + spec.screen_pixels),
            int(bar_y - 14),
            int(bar_x + spec.screen_pixels),
            int(bar_y - 6),
        )
        painter.drawText(
            int(bar_x),
            int(bar_y - 30),
            max(int(spec.screen_pixels), text_rect.width() + 4),
            16,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            label,
        )
        painter.end()


# ======================================================================
# Tiled pyramid rendering
# ======================================================================


class _TileFetchWorker(QThread):
    """Background worker that fetches a batch of tiles from the server."""

    done = pyqtSignal()

    def __init__(self, provider, requests):
        super().__init__()
        self._provider = provider
        self._requests = requests  # list of (level, c, z, t, tx, ty)

    def run(self):
        for req in self._requests:
            try:
                self._provider.get_tile(*req)
            except Exception:
                pass
        self.done.emit()


class TiledImageItem(QGraphicsObject):
    """QGraphicsItem that renders a pyramidal OMERO image with progressive
    tile loading.  Shows a low-res overview immediately, then fills in
    higher-resolution tiles for the visible area on demand.
    """

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self._prov = provider
        self._fw, self._fh = provider.full_size()

        nc = provider.metadata["size_c"]
        self._active: list[int] = list(range(nc))
        self._colors: list[tuple[int, int, int]] = [(255, 255, 255)] * nc
        self._contrast: dict[int, tuple[float, float]] = {}
        self._z = 0
        self._t = 0
        self._mode = "Slice"

        # Composited overview pixmap (level 0, all channels)
        self._overview_pix: QPixmap = QPixmap()
        # Composited tile cache: (level, tx, ty) -> QPixmap
        self._tile_cache: OrderedDict = OrderedDict()
        self._max_comp_tiles = 500

        self._worker: _TileFetchWorker | None = None
        self._pending_requests: set[tuple[int, int, int, int, int, int]] = set()
        self._worker_requests: set[tuple[int, int, int, int, int, int]] = set()

    def boundingRect(self):
        return QRectF(0, 0, self._fw, self._fh)

    def set_overview(self, pix: QPixmap):
        self._overview_pix = pix

    def set_display(self, active, colors, contrast, z, t, mode):
        self._active = active
        self._colors = colors
        self._contrast = contrast
        self._z = z
        self._t = t
        self._mode = mode
        self._tile_cache.clear()
        self.update()

    def paint(self, painter, option, widget=None):
        if not self.scene() or not self.scene().views():
            return
        view = self.scene().views()[0]

        # Draw overview as background
        if not self._overview_pix.isNull():
            painter.drawPixmap(
                QRectF(0, 0, self._fw, self._fh),
                self._overview_pix,
                QRectF(0, 0, self._overview_pix.width(), self._overview_pix.height()),
            )

        # Visible rect in image (full-res) coordinates
        vp = view.viewport().rect()
        scene_rect = view.mapToScene(vp).boundingRect()
        visible = self.mapFromScene(scene_rect).boundingRect()
        visible = visible.intersected(self.boundingRect())
        if visible.isEmpty():
            return

        # Current scale: screen pixels per full-res image pixel
        scale = vp.width() / scene_rect.width() if scene_rect.width() > 0 else 1.0
        level = self._prov.best_level_for_scale(scale)

        # Level 0 is already drawn as the overview — skip redundant tiles
        if level == 0:
            return

        lw, lh = self._prov.level_size(level)
        tw, th = self._prov.tile_size(level)
        fx, fy = lw / self._fw, lh / self._fh

        # Visible rect in level-pixel coordinates
        vx0 = max(0, int(visible.left() * fx))
        vy0 = max(0, int(visible.top() * fy))
        vx1 = min(lw, int(math.ceil(visible.right() * fx)))
        vy1 = min(lh, int(math.ceil(visible.bottom() * fy)))

        # Tile index ranges
        tx0, ty0 = vx0 // tw, vy0 // th
        tx1 = min((vx1 + tw - 1) // tw, (lw + tw - 1) // tw)
        ty1 = min((vy1 + th - 1) // th, (lh + th - 1) // th)

        missing: list[tuple[int, int, int, int, int, int]] = []
        visible_tiles: set[tuple[int, int]] = set()
        for tyi in range(ty0, ty1):
            for txi in range(tx0, tx1):
                visible_tiles.add((txi, tyi))
                pix = self._get_composite_tile(level, txi, tyi)
                ix = txi * tw / fx
                iy = tyi * th / fy
                iw = min(tw, lw - txi * tw) / fx
                ih = min(th, lh - tyi * th) / fy
                target_rect = QRectF(ix, iy, iw, ih)

                if pix is not None and not pix.isNull():
                    painter.drawPixmap(
                        target_rect,
                        pix,
                        QRectF(0, 0, pix.width(), pix.height()),
                    )
                else:
                    fallback = self._try_fallback_composite(level, txi, tyi, target_rect)
                    if fallback is not None:
                        fb_pix, src_rect = fallback
                        painter.drawPixmap(target_rect, fb_pix, src_rect)

        missing.extend(
            self._prefetch_requests_for_region(level, tx0, ty0, tx1, ty1, visible_tiles)
        )
        if missing and (self._worker is None or not self._worker.isRunning()):
            batch = missing[:96]
            self._worker_requests = set(batch)
            self._pending_requests.update(self._worker_requests)
            self._worker = _TileFetchWorker(self._prov, batch)
            self._worker.done.connect(self._on_worker_done)
            self._worker.start()

    def _try_composite(self, level, tx, ty):
        """Composite a tile from all active channels using cached data only."""
        if not self._active:
            return QPixmap()

        lw, lh = self._prov.level_size(level)
        tw, th = self._prov.tile_size(level)
        x0, y0 = tx * tw, ty * th
        aw, ah = min(tw, lw - x0), min(th, lh - y0)
        if aw <= 0 or ah <= 0:
            return QPixmap()

        tiles = []
        for c in self._active:
            planes = []
            for z in self._z_indices():
                arr = self._prov.get_cached_tile(level, c, z, self._t, tx, ty)
                if arr is None:
                    return None  # Not all required tiles cached yet
                planes.append(arr)
            if not planes:
                continue
            projected = _project_stack(np.stack(planes, axis=0), self._mode, self._z)
            tiles.append((projected, c))

        canvas = np.zeros((ah, aw, 3), dtype=np.float64)
        for arr, c in tiles:
            lo, hi = self._contrast.get(c, (float(arr.min()), float(arr.max())))
            if hi <= lo:
                hi = lo + 1
            r, g, b = self._colors[c] if c < len(self._colors) else (255, 255, 255)
            norm = (arr.astype(np.float64) - lo) / (hi - lo)
            np.clip(norm, 0, 1, out=norm)
            canvas[..., 0] += norm * (r / 255)
            canvas[..., 1] += norm * (g / 255)
            canvas[..., 2] += norm * (b / 255)

        np.clip(canvas, 0, 1, out=canvas)
        rgb = (canvas * 255).astype(np.uint8)
        rgb = np.ascontiguousarray(rgb)
        qimg = QImage(rgb.data, aw, ah, 3 * aw, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())

    def _get_composite_tile(self, level, tx, ty):
        comp_key = (level, tx, ty)
        if comp_key in self._tile_cache:
            self._tile_cache.move_to_end(comp_key)
            return self._tile_cache[comp_key]
        pix = self._try_composite(level, tx, ty)
        if pix is not None:
            while len(self._tile_cache) >= self._max_comp_tiles:
                self._tile_cache.popitem(last=False)
            self._tile_cache[comp_key] = pix
        return pix

    def _try_fallback_composite(self, level, tx, ty, target_rect: QRectF):
        """Use a cached coarser tile as an interim preview for a missing sharper tile."""
        target_full = self._tile_full_res_rect(level, tx, ty)
        for fallback_level in range(level - 1, 0, -1):
            rect = self._level_rect_for_full_res_rect(fallback_level, target_full)
            ftx0, fty0, ftx1, fty1 = self._tile_index_bounds_for_level_rect(fallback_level, rect)
            if ftx1 - ftx0 != 1 or fty1 - fty0 != 1:
                continue
            fb_pix = self._get_composite_tile(fallback_level, ftx0, fty0)
            if fb_pix is None or fb_pix.isNull():
                continue
            tile_rect = self._tile_level_rect(fallback_level, ftx0, fty0)
            sx = fb_pix.width() / tile_rect.width()
            sy = fb_pix.height() / tile_rect.height()
            src = QRectF(
                (rect.left() - tile_rect.left()) * sx,
                (rect.top() - tile_rect.top()) * sy,
                rect.width() * sx,
                rect.height() * sy,
            )
            if src.width() > 0 and src.height() > 0:
                return fb_pix, src
        return None

    def _z_indices(self) -> list[int]:
        if self._mode == "Slice":
            return [self._z]
        return list(range(int(self._prov.metadata.get("size_z", 1))))

    def _prefetch_requests_for_region(
        self,
        level: int,
        tx0: int,
        ty0: int,
        tx1: int,
        ty1: int,
        visible_tiles: set[tuple[int, int]],
    ) -> list[tuple[int, int, int, int, int, int]]:
        lw, lh = self._prov.level_size(level)
        tw, th = self._prov.tile_size(level)
        max_tx = max((lw + tw - 1) // tw, 1)
        max_ty = max((lh + th - 1) // th, 1)
        pad = 1
        qx0 = max(0, tx0 - pad)
        qy0 = max(0, ty0 - pad)
        qx1 = min(max_tx, tx1 + pad)
        qy1 = min(max_ty, ty1 + pad)
        cx = (tx0 + tx1 - 1) / 2.0
        cy = (ty0 + ty1 - 1) / 2.0

        target_tiles: list[tuple[int, int]] = []
        ring_tiles: list[tuple[int, int]] = []
        for tyi in range(qy0, qy1):
            for txi in range(qx0, qx1):
                if (txi, tyi) in visible_tiles:
                    continue
                ring_tiles.append((txi, tyi))
        target_tiles.extend(
            sorted(
                visible_tiles,
                key=lambda tile: (abs(tile[1] - cy) + abs(tile[0] - cx), tile[1], tile[0]),
            )
        )
        target_tiles.extend(
            sorted(
                ring_tiles,
                key=lambda tile: (abs(tile[1] - cy) + abs(tile[0] - cx), tile[1], tile[0]),
            )
        )

        tiles_by_level: list[tuple[int, list[tuple[int, int]]]] = []
        coarse_level = max(1, level - 1)
        if coarse_level != level:
            coarse_tiles = self._map_tiles_to_level(coarse_level, visible_tiles, level)
            coarse_cx = sum(tile[0] for tile in coarse_tiles) / max(len(coarse_tiles), 1)
            coarse_cy = sum(tile[1] for tile in coarse_tiles) / max(len(coarse_tiles), 1)
            tiles_by_level.append(
                (
                    coarse_level,
                    sorted(
                        coarse_tiles,
                        key=lambda tile: (
                            abs(tile[1] - coarse_cy) + abs(tile[0] - coarse_cx),
                            tile[1],
                            tile[0],
                        ),
                    ),
                )
            )
        tiles_by_level.append((level, target_tiles))

        requests: list[tuple[int, int, int, int, int, int]] = []
        seen: set[tuple[int, int, int, int, int, int]] = set()
        z_indices = self._z_indices()
        for req_level, tiles in tiles_by_level:
            for txi, tyi in tiles:
                for c in self._active:
                    for z in z_indices:
                        req = (req_level, c, z, self._t, txi, tyi)
                        if (
                            req in seen
                            or req in self._pending_requests
                            or self._prov.has_tile(req_level, c, z, self._t, txi, tyi)
                        ):
                            continue
                        seen.add(req)
                        requests.append(req)
        return requests

    def _tile_full_res_rect(self, level: int, tx: int, ty: int) -> QRectF:
        lw, lh = self._prov.level_size(level)
        tw, th = self._prov.tile_size(level)
        fx, fy = lw / self._fw, lh / self._fh
        x0 = tx * tw / fx
        y0 = ty * th / fy
        w = min(tw, lw - tx * tw) / fx
        h = min(th, lh - ty * th) / fy
        return QRectF(x0, y0, w, h)

    def _level_rect_for_full_res_rect(self, level: int, full_rect: QRectF) -> QRectF:
        lw, lh = self._prov.level_size(level)
        fx, fy = lw / self._fw, lh / self._fh
        return QRectF(
            full_rect.left() * fx,
            full_rect.top() * fy,
            full_rect.width() * fx,
            full_rect.height() * fy,
        )

    def _tile_level_rect(self, level: int, tx: int, ty: int) -> QRectF:
        lw, lh = self._prov.level_size(level)
        tw, th = self._prov.tile_size(level)
        x0 = tx * tw
        y0 = ty * th
        return QRectF(x0, y0, min(tw, lw - x0), min(th, lh - y0))

    def _tile_index_bounds_for_level_rect(self, level: int, rect: QRectF) -> tuple[int, int, int, int]:
        lw, lh = self._prov.level_size(level)
        tw, th = self._prov.tile_size(level)
        rx0 = max(0, int(rect.left()))
        ry0 = max(0, int(rect.top()))
        rx1 = min(lw, int(math.ceil(rect.right())))
        ry1 = min(lh, int(math.ceil(rect.bottom())))
        tx0 = rx0 // tw
        ty0 = ry0 // th
        tx1 = min((rx1 + tw - 1) // tw, (lw + tw - 1) // tw)
        ty1 = min((ry1 + th - 1) // th, (lh + th - 1) // th)
        return tx0, ty0, tx1, ty1

    def _map_tiles_to_level(
        self,
        target_level: int,
        source_tiles: set[tuple[int, int]],
        source_level: int,
    ) -> set[tuple[int, int]]:
        mapped: set[tuple[int, int]] = set()
        for tx, ty in source_tiles:
            full_rect = self._tile_full_res_rect(source_level, tx, ty)
            level_rect = self._level_rect_for_full_res_rect(target_level, full_rect)
            tx0, ty0, tx1, ty1 = self._tile_index_bounds_for_level_rect(target_level, level_rect)
            for mty in range(ty0, ty1):
                for mtx in range(tx0, tx1):
                    mapped.add((mtx, mty))
        return mapped

    def _on_worker_done(self) -> None:
        self._pending_requests.difference_update(self._worker_requests)
        self._worker_requests.clear()
        self.update()


# ======================================================================
# Main window
# ======================================================================

class ViewerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OMERO Viewer")
        self.setWindowIcon(_make_app_icon())
        self.resize(1000, 700)

        # State
        self._volumes: list[np.ndarray] = []  # optional eager-loaded channel volumes
        self._metadata: dict[str, Any] = {}
        self._channel_colors: list[tuple[int, int, int]] = []
        self._channel_buttons: list[QPushButton] = []
        self._pct_cache: dict = {}
        self._overview_cache: dict[tuple[int, int], list[np.ndarray]] = {}
        self._regular_provider = None
        self._selection_context = None
        self._view_request_id = 0
        self._rendering = False
        self._pending_render = False
        self._vol_method_values = {
            method: spec["default"] for method, spec in _VOLUME_METHOD_UI.items()
        }
        self._available_volume_methods = list(_VOLUME_METHODS)
        self._active_vol_method = _VOLUME_METHODS[0]
        self._vol_slider_dragging = False
        self._vol_linear_interpolation = True
        self._vol_camera_ranges: tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None = None

        # Tiled pyramid mode
        self._tiled_item: TiledImageItem | None = None
        self._tile_provider = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_lay = QVBoxLayout(central)
        main_lay.setContentsMargins(18, 18, 18, 12)
        main_lay.setSpacing(14)
        self.setStyleSheet(
            "QMainWindow { background: #111315; color: #eceff1; }"
            "QFrame#panel {"
            "background: transparent;"
            "border: none; border-radius: 0; }"
            "QLabel { color: #d5d9dd; }"
            "QLabel#title { color: #f3f4f6; font-size: 22px; font-weight: 700; }"
            "QLabel#section { color: #d5d9dd; font-size: 11px; font-weight: 700; letter-spacing: 0.08em; }"
            "QLabel#value { color: #eceff1; font-size: 12px; font-weight: 600; }"
            "QLabel#hint { color: #8f969d; font-size: 12px; }"
            "QCheckBox { color: #d5d9dd; spacing: 6px; }"
            "QPushButton {"
            "background: #1e293b; color: #e2e8f0; border: 1px solid #334155;"
            "border-radius: 6px; padding: 6px 10px; font-weight: 600; }"
            "QPushButton:hover { background: #273449; border-color: #475569; }"
            "QPushButton:pressed { background: #0f172a; }"
            "QPushButton#primary { background: #0ea5e9; color: #082f49; border-color: #38bdf8; }"
            "QPushButton#primary:hover { background: #38bdf8; }"
            "QPushButton#spin_step { min-width: 18px; max-width: 18px; min-height: 12px; max-height: 12px; padding: 0; border-radius: 3px; font-size: 8px; }"
            "QComboBox, QDoubleSpinBox {"
            "background: #1b1e21; color: #eceff1; border: 1px solid #43484d;"
            "border-radius: 6px; padding: 4px 8px; min-height: 18px; }"
            "QComboBox { padding-right: 24px; }"
            "QComboBox::drop-down {"
            "subcontrol-origin: padding; subcontrol-position: top right;"
            "width: 26px; background: #25292d; border-left: 1px solid #43484d;"
            "border-top-right-radius: 6px; border-bottom-right-radius: 6px; }"
            "QSlider::groove:horizontal { background: #262a2e; height: 6px; border-radius: 3px; }"
            "QSlider::sub-page:horizontal { background: #8f969d; border-radius: 3px; }"
            "QSlider::handle:horizontal {"
            "background: #f3f4f6; width: 16px; margin: -6px 0; border-radius: 8px; }"
            "QSlider::groove:vertical { background: #262a2e; width: 6px; border-radius: 3px; }"
            "QSlider::sub-page:vertical { background: #8f969d; border-radius: 3px; }"
            "QSlider::handle:vertical {"
            "background: #f3f4f6; height: 16px; margin: 0 -6px; border-radius: 8px; }"
            "QStatusBar { background: #0d0f11; color: #8f969d; }"
        )

        top_panel = self._make_panel()
        top_lay = QVBoxLayout(top_panel)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("OMERO Viewer")
        title.setObjectName("title")
        self._path_label = QLabel("")
        self._path_label.setObjectName("hint")
        self._path_label.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(self._path_label)
        title_row.addLayout(title_box, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(QLabel("Lo"))
        self._lo_spin = QDoubleSpinBox()
        self._lo_spin.setRange(0.0, 50.0)
        self._lo_spin.setValue(0.1)
        self._lo_spin.setSingleStep(0.001)
        self._lo_spin.setDecimals(3)
        self._lo_spin.setFixedWidth(72)
        self._lo_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._lo_spin.valueChanged.connect(self._on_contrast_changed)
        actions.addWidget(self._spin_with_buttons(self._lo_spin))
        actions.addWidget(QLabel("Hi"))
        self._hi_spin = QDoubleSpinBox()
        self._hi_spin.setRange(50.0, 100.0)
        self._hi_spin.setValue(100.0)
        self._hi_spin.setSingleStep(0.001)
        self._hi_spin.setDecimals(3)
        self._hi_spin.setFixedWidth(72)
        self._hi_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._hi_spin.valueChanged.connect(self._on_contrast_changed)
        actions.addWidget(self._spin_with_buttons(self._hi_spin))
        self._actual_btn = QPushButton("100%")
        self._fit_btn = QPushButton("Fit to View")
        self._open_btn = QPushButton("Open from OMERO")
        self._open_btn.setObjectName("primary")
        self._open_btn.clicked.connect(self._open_omero)
        actions.addWidget(self._actual_btn)
        actions.addWidget(self._fit_btn)
        self._3d_btn = QPushButton("3D")
        self._3d_btn.setEnabled(False)
        self._3d_btn.setToolTip("Open 3D volume viewer (requires vispy)")
        self._3d_btn.clicked.connect(self._open_3d_viewer)
        if not _HAS_VISPY:
            self._3d_btn.setToolTip("vispy not installed — pip install vispy")
        actions.addWidget(self._3d_btn)
        actions.addWidget(self._open_btn)
        title_row.addLayout(actions)
        top_lay.addLayout(title_row)

        self._ch_row = QHBoxLayout()
        self._ch_row.setSpacing(8)
        self._ch_row.addStretch()
        top_lay.addLayout(self._ch_row)

        body_row = QHBoxLayout()
        body_row.setSpacing(12)

        viewer_panel = self._make_panel()
        viewer_lay = QVBoxLayout(viewer_panel)
        viewer_lay.setContentsMargins(12, 12, 12, 12)
        viewer_lay.setSpacing(10)

        self._viewer = ZoomableImageView()
        self._viewer.cursorMoved.connect(self._update_cursor_readout)

        # Stacked widget: index 0 = 2D viewer, index 1 = 3D vispy canvas
        self._view_stack = QStackedWidget()
        self._view_stack.addWidget(self._viewer)  # index 0
        if _HAS_VISPY:
            self._vispy_canvas = vispy_scene.SceneCanvas(
                keys="interactive", show=False, bgcolor="#000000",
            )
            self._vispy_view = self._vispy_canvas.central_widget.add_view()
            self._vispy_view.camera = vispy_scene.ArcballCamera(
                fov=60, distance=0,
            )
            self._view_stack.addWidget(self._vispy_canvas.native)  # index 1
        self._vispy_volumes: list = []
        self._vol_raw_stacks: list = []  # cached (stack, color, ch_idx) for contrast refresh
        self._3d_debounce = QTimer(self)
        self._3d_debounce.setSingleShot(True)
        self._3d_debounce.setInterval(_THREE_D_REFRESH_DEBOUNCE_MS)
        self._3d_debounce.timeout.connect(self._debounced_3d_refresh)
        self._view_stack.setCurrentIndex(0)
        viewer_lay.addWidget(self._view_stack, 1)

        # 3D toolbar (hidden until 3D mode is active)
        self._3d_bar = QWidget()
        bar3_lay = QHBoxLayout(self._3d_bar)
        bar3_lay.setContentsMargins(0, 0, 0, 0)
        bar3_lay.setSpacing(8)
        bar3_lay.addWidget(QLabel("Render:"))
        self._vol_method_combo = ArrowComboBox()
        self._vol_method_combo.currentIndexChanged.connect(self._on_vol_method_changed)
        bar3_lay.addWidget(self._vol_method_combo)
        self._vol_slider_label = QLabel("Gain:")
        bar3_lay.addWidget(self._vol_slider_label)
        self._vol_thresh_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_thresh_slider.setRange(1, 200)
        self._vol_thresh_slider.setValue(100)
        self._vol_thresh_slider.setFixedWidth(140)
        self._vol_thresh_slider.sliderPressed.connect(self._on_vol_slider_pressed)
        self._vol_thresh_slider.valueChanged.connect(self._on_vol_threshold_changed)
        self._vol_thresh_slider.sliderReleased.connect(self._on_vol_slider_released)
        bar3_lay.addWidget(self._vol_thresh_slider)
        self._vol_slider_val = QLabel("1.00")
        self._vol_slider_val.setFixedWidth(32)
        bar3_lay.addWidget(self._vol_slider_val)
        bar3_lay.addWidget(QLabel("Downsample:"))
        self._vol_ds_combo = ArrowComboBox()
        self._vol_ds_combo.addItems(["1x", "2x", "4x"])
        self._vol_ds_combo.currentIndexChanged.connect(self._on_vol_downsample_changed)
        bar3_lay.addWidget(self._vol_ds_combo)
        self._vol_interp_check = QCheckBox("Smooth")
        self._vol_interp_check.setChecked(True)
        self._vol_interp_check.setToolTip("Use linear interpolation instead of nearest voxel sampling.")
        self._vol_interp_check.toggled.connect(self._on_vol_interpolation_toggled)
        bar3_lay.addWidget(self._vol_interp_check)
        self._vol_reset_btn = QPushButton("Reset View")
        self._vol_reset_btn.clicked.connect(self._reset_vol_camera)
        bar3_lay.addWidget(self._vol_reset_btn)
        bar3_lay.addStretch()
        self._refresh_volume_method_options()
        self._3d_bar.setVisible(False)
        viewer_lay.addWidget(self._3d_bar)

        body_row.addWidget(viewer_panel, 1)

        z_panel = self._make_panel()
        z_panel.setFixedWidth(64)
        z_lay = QVBoxLayout(z_panel)
        z_lay.setContentsMargins(0, 0, 0, 0)
        z_lay.setSpacing(4)
        z_lbl = QLabel("Z")
        z_lbl.setObjectName("hint")
        z_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        z_lay.addWidget(z_lbl)
        self._z_slider = QSlider(Qt.Orientation.Vertical)
        self._z_slider.setMinimum(0)
        self._z_slider.setMaximum(0)
        self._z_slider.valueChanged.connect(self._request_view_update)
        self._z_slider.setFixedWidth(22)
        z_lay.addWidget(self._z_slider, 1, Qt.AlignmentFlag.AlignHCenter)
        self._z_label = QLabel("0/0")
        self._z_label.setObjectName("hint")
        self._z_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._z_label.setFixedWidth(64)
        z_lay.addWidget(self._z_label, 0, Qt.AlignmentFlag.AlignHCenter)
        body_row.addWidget(z_panel, 0)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        time_panel = self._make_panel()
        time_lay = QHBoxLayout(time_panel)
        time_lay.setContentsMargins(0, 0, 0, 0)
        time_lay.setSpacing(8)
        t_lbl = QLabel("T")
        t_lbl.setObjectName("hint")
        time_lay.addWidget(t_lbl)
        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(32, 24)
        self._play_btn.setStyleSheet("font-size: 21px;")
        self._play_btn.setCheckable(True)
        self._play_btn.toggled.connect(self._on_play_toggled)
        time_lay.addWidget(self._play_btn)
        self._speed_combo = ArrowComboBox()
        self._speed_combo.addItems(["1x", "2x", "3x", "fast"])
        self._speed_combo.setFixedWidth(92)
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        time_lay.addWidget(self._speed_combo)
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(1000)
        self._play_timer.timeout.connect(self._on_play_tick)
        self._t_slider = QSlider(Qt.Orientation.Horizontal)
        self._t_slider.setMinimum(0)
        self._t_slider.setMaximum(0)
        self._t_slider.valueChanged.connect(self._request_view_update)
        time_lay.addWidget(self._t_slider, 1)
        self._t_label = QLabel("0/0")
        self._t_label.setObjectName("hint")
        time_lay.addWidget(self._t_label)
        bottom_row.addWidget(time_panel, 1)

        projection_panel = self._make_panel()
        projection_panel.setFixedWidth(150)
        projection_lay = QVBoxLayout(projection_panel)
        projection_lay.setContentsMargins(0, 0, 0, 0)
        projection_lay.setSpacing(0)
        self._proj_combo = ArrowComboBox()
        self._proj_combo.addItems(_PROJECTION_MODES)
        self._proj_combo.currentIndexChanged.connect(self._request_view_update)
        projection_lay.addWidget(self._proj_combo)
        bottom_row.addWidget(projection_panel, 0)

        main_lay.addWidget(top_panel)
        main_lay.addLayout(body_row, 1)
        main_lay.addLayout(bottom_row)
        self._actual_btn.clicked.connect(self._viewer.actual_size)
        self._fit_btn.clicked.connect(self._fit_current_view)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._cursor_label = QLabel("")
        self._cursor_label.setObjectName("hint")
        self._status.addPermanentWidget(self._cursor_label)
        self._progress = QProgressBar()
        self._progress.setFixedWidth(220)
        self._progress.setFixedHeight(14)
        self._progress.setTextVisible(True)
        self._progress.hide()
        self._status.addPermanentWidget(self._progress)

    def _make_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        return panel

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("section")
        return lbl

    def _spin_with_buttons(self, spin: QDoubleSpinBox) -> QWidget:
        wrapper = QWidget()
        lay = QHBoxLayout(wrapper)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(spin)

        buttons = QVBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(2)

        up_btn = QPushButton("▲")
        up_btn.setObjectName("spin_step")
        up_btn.setAutoRepeat(True)
        up_btn.clicked.connect(spin.stepUp)
        buttons.addWidget(up_btn)

        down_btn = QPushButton("▼")
        down_btn.setObjectName("spin_step")
        down_btn.setAutoRepeat(True)
        down_btn.clicked.connect(spin.stepDown)
        buttons.addWidget(down_btn)

        lay.addLayout(buttons)
        return wrapper

    def _begin_progress(self, total: int, text: str) -> None:
        self._progress.setRange(0, max(total, 1))
        self._progress.setValue(0)
        self._progress.setFormat("%v / %m")
        self._progress.show()
        self._status.showMessage(text)
        QApplication.processEvents()

    def _advance_progress(self, value: int, text: str | None = None) -> None:
        self._progress.setValue(value)
        if text is not None:
            self._status.showMessage(text)
        QApplication.processEvents()

    def _end_progress(self) -> None:
        self._progress.hide()
        self._refresh_metadata_labels()

    def _on_backend_choice_changed(self, *_args) -> None:
        pass

    def _on_play_toggled(self, checked: bool) -> None:
        if checked:
            if self._t_slider.maximum() < 1:
                self._play_btn.setChecked(False)
                return
            self._play_btn.setText("⏸")
            self._play_btn.setStyleSheet("font-size: 21px;")
            self._apply_play_speed()
            self._play_timer.start()
        else:
            self._play_btn.setText("▶")
            self._play_btn.setStyleSheet("font-size: 21px;")
            self._play_timer.stop()

    def _on_speed_changed(self, _index: int) -> None:
        if self._play_btn.isChecked():
            self._apply_play_speed()

    def _apply_play_speed(self) -> None:
        speed = self._speed_combo.currentText()
        intervals = {"1x": 1000, "2x": 500, "3x": 333, "fast": 0}
        self._play_timer.setInterval(intervals.get(speed, 1000))

    def _on_play_tick(self) -> None:
        t = self._t_slider.value()
        t_max = self._t_slider.maximum()
        if t >= t_max:
            self._t_slider.setValue(0)
        else:
            self._t_slider.setValue(t + 1)

    def _request_view_update(self, *_args) -> None:
        self._view_request_id += 1
        if self._rendering:
            self._pending_render = True
            return
        self._update_viewer(self._view_request_id)

    def _ensure_render_current(self, request_id: int) -> None:
        if request_id != self._view_request_id:
            raise _RenderCancelled

    def _update_cursor_readout(self, x: float, y: float, inside: bool) -> None:
        if not inside or not self._metadata:
            self._cursor_label.setText("")
            return
        sx = max(int(self._metadata.get("size_x", 0)), 0)
        sy = max(int(self._metadata.get("size_y", 0)), 0)
        xi = max(0, min(int(x), max(sx - 1, 0)))
        yi = max(0, min(int(y), max(sy - 1, 0)))
        self._cursor_label.setText(
            f"X {xi}  Y {yi}  Z {self._current_z()}  T {self._current_t()}"
        )

    def _fit_after_load(self) -> None:
        """Reset zoom and fit newly loaded content after layout settles."""
        QTimer.singleShot(0, self._fit_current_view)

    # ------------------------------------------------------------------
    # Open from OMERO
    # ------------------------------------------------------------------

    def _open_3d_viewer(self) -> None:
        """Toggle between 2D and 3D view in the same viewer panel."""
        if not _HAS_VISPY:
            QMessageBox.warning(self, "vispy not installed",
                                "Install vispy to use the 3D viewer:\n  pip install vispy")
            return

        # If already in 3D mode, switch back to 2D
        if self._view_stack.currentIndex() == 1:
            self._switch_to_2d()
            return

        channels = self._current_channel_render_settings()
        active = [(i, ch) for i, ch in enumerate(channels) if ch.get("active", True)]
        if not active:
            self._status.showMessage("No active channels for 3D view")
            return

        nz = max(int(self._metadata.get("size_z", 1)), 1)
        if nz < 2:
            self._status.showMessage("Need at least 2 Z-planes for 3D view")
            return

        self._status.showMessage("Loading channel stacks for 3D viewer…")
        QApplication.processEvents()

        try:
            self._load_3d_volumes(active, nz)
        except Exception as exc:  # noqa: BLE001
            self._status.clearMessage()
            QMessageBox.critical(self, "Error", f"Failed to load 3D data:\n{exc}")
            return

        self._view_stack.setCurrentIndex(1)
        self._3d_bar.setVisible(True)
        self._3d_btn.setText("2D")
        self._status.showMessage("3D volume view — drag to rotate, scroll to zoom")

    def _switch_to_2d(self, *, show_status: bool = True) -> None:
        self._view_stack.setCurrentIndex(0)
        self._3d_bar.setVisible(False)
        self._3d_btn.setText("3D")
        if show_status:
            self._status.showMessage("2D view")

    def _prepare_for_new_image(self) -> None:
        """Reset view state so a newly loaded image always opens in 2D."""
        self._switch_to_2d(show_status=False)
        self._3d_debounce.stop()

    def _fit_current_view(self) -> None:
        """Fit the current view, refreshing tiled overviews when needed."""
        if self._view_stack.currentIndex() == 1:
            self._reset_vol_camera()
            return

        if self._tiled_item is not None:
            mode = self._proj_combo.currentText()
            z = self._current_z()
            t = self._current_t()
            active = [
                i for i, btn in enumerate(self._channel_buttons)
                if btn.isChecked()
            ]
            request_id = self._view_request_id
            overview_volumes = self._tiled_overview(mode, z, t, request_id)
            contrast: dict[int, tuple[float, float]] = {}
            for c in active:
                if c < len(overview_volumes):
                    arr = overview_volumes[c][0]
                    contrast[c] = self._get_contrast(arr, c)

            overview_pix = self._build_overview_pixmap(active, contrast, overview_volumes)
            self._tiled_item.set_overview(overview_pix)
            self._tiled_item.set_display(
                active,
                self._channel_colors,
                contrast,
                z,
                t,
                mode,
            )
            self._viewer.fit_rect(self._tiled_item.boundingRect())
            self._tiled_item.update()
        else:
            self._viewer.fit_in_view()
        self._viewer.viewport().update()

    def _load_3d_volumes(self, active: list, nz: int) -> None:
        """Build vispy Volume visuals from active channel stacks."""
        for v in self._vispy_volumes:
            v.parent = None
        self._vispy_volumes.clear()
        self._vol_raw_stacks.clear()
        self._vol_camera_ranges = None

        ds = [1, 2, 4][self._vol_ds_combo.currentIndex()]
        px = self._metadata.get("pixel_size_x")
        pz = self._metadata.get("pixel_size_z")
        z_scale = (pz / px) if (pz and px and px > 0) else 1.0

        method = self._current_volume_method()
        slider_val = self._volume_slider_value(method)
        gain = self._volume_gain(method, slider_val)

        total_planes = nz * len(active)
        self._begin_progress(total_planes, "Loading 3D volumes\u2026")
        planes_loaded = 0
        reference_shape: tuple[int, int, int] | None = None

        for ci, ch in active:
            offset = planes_loaded

            def _prog(done, total, _off=offset, _ci=ci):
                self._advance_progress(
                    _off + done,
                    f"Loading 3D volumes\u2026 channel {_ci} \u2014 plane {done}/{total}",
                )

            stack = self._regular_channel_stack(ci, progress=_prog)
            planes_loaded += nz
            original_shape = stack.shape
            if reference_shape is None:
                reference_shape = tuple(int(v) for v in original_shape)

            if ds > 1:
                stack = stack[::ds, ::ds, ::ds]
            color = self._channel_colors[ci] if ci < len(self._channel_colors) else (255, 255, 255)

            self._vol_raw_stacks.append((stack, color, ci))

            mid_z = min(nz // 2, stack.shape[0] - 1)
            lo, hi = self._get_contrast(stack[mid_z], ci)

            fvol = self._prepare_volume_data(stack, lo, hi, method, gain)

            cmap = _ChannelColormap(color, translucent_boost=(method == "translucent"))
            v = vispy_scene.visuals.Volume(
                fvol,
                parent=self._vispy_view.scene,
                method=method,
                threshold=slider_val if self._volume_slider_role(method) == "threshold" else 0.0,
                attenuation=slider_val if self._volume_slider_role(method) == "attenuation" else 1.0,
                mip_cutoff=slider_val if self._volume_slider_role(method) == "mip_cutoff" else None,
                minip_cutoff=slider_val if self._volume_slider_role(method) == "minip_cutoff" else None,
                cmap=cmap,
                interpolation=self._current_volume_interpolation(),
            )
            # Preserve the original physical aspect ratio after voxel decimation.
            v.transform = STTransform(scale=(ds, ds, z_scale * ds))
            v.set_gl_state('additive', depth_test=False)
            self._vispy_volumes.append(v)

        self._end_progress()
        if reference_shape is not None:
            oz, oy, ox = reference_shape
            self._vol_camera_ranges = (
                (0.0, float(max(ox - 1, 0))),
                (0.0, float(max(oy - 1, 0))),
                (0.0, float(max(oz - 1, 0)) * z_scale),
            )
        self._reset_vol_camera()

    def _refresh_3d_contrast(self) -> None:
        """Re-normalize cached 3D volumes using current Lo/Hi contrast + gain."""
        if not self._vol_raw_stacks or not self._vispy_volumes:
            return
        method = self._current_volume_method()
        slider_val = self._volume_slider_value(method)
        gain = self._volume_gain(method, slider_val)
        for (stack, color, ci), vol in zip(self._vol_raw_stacks, self._vispy_volumes):
            self._apply_volume_method_param(vol, method, slider_val)
            vol.cmap = _ChannelColormap(color, translucent_boost=(method == "translucent"))
            mid_z = min(stack.shape[0] // 2, stack.shape[0] - 1)
            lo, hi = self._get_contrast(stack[mid_z], ci)
            fvol = self._prepare_volume_data(stack, lo, hi, method, gain)
            vol.set_data(fvol)
        if hasattr(self, "_vispy_canvas"):
            self._vispy_canvas.update()

    def _on_vol_method_changed(self, idx: int) -> None:
        if idx < 0:
            return
        self._vol_method_values[self._active_vol_method] = self._vol_thresh_slider.value()
        if idx >= len(self._available_volume_methods):
            return
        method = self._available_volume_methods[idx]
        self._active_vol_method = method
        self._set_volume_slider_ui(
            method,
            self._vol_method_values.get(method, _VOLUME_METHOD_UI[method]["default"]),
        )
        self._apply_3d_settings(immediate=True)

    def _on_vol_interpolation_toggled(self, checked: bool) -> None:
        self._vol_linear_interpolation = checked
        if self._view_stack.currentIndex() != 1:
            return
        interpolation = self._current_volume_interpolation()
        for vol in self._vispy_volumes:
            vol.interpolation = interpolation
        if hasattr(self, "_vispy_canvas"):
            self._vispy_canvas.update()

    def _on_vol_threshold_changed(self, value: int) -> None:
        self._vol_method_values[self._current_volume_method()] = value
        self._vol_slider_val.setText(f"{value / 100:.2f}")
        if self._view_stack.currentIndex() != 1:
            return
        method = self._current_volume_method()
        role = self._volume_slider_role(method)
        if role in {"threshold", "attenuation", "mip_cutoff", "minip_cutoff"}:
            for vol in self._vispy_volumes:
                self._apply_volume_method_param(vol, method, value / 100.0)
            if hasattr(self, "_vispy_canvas"):
                self._vispy_canvas.update()
            return
        if self._vol_slider_dragging or self._vol_thresh_slider.isSliderDown():
            self._3d_debounce.stop()
            return
        self._3d_debounce.start()

    def _on_vol_slider_pressed(self) -> None:
        self._vol_slider_dragging = True
        if self._volume_slider_role(self._current_volume_method()) == "gain":
            self._3d_debounce.stop()

    def _on_vol_slider_released(self) -> None:
        self._vol_slider_dragging = False
        if self._view_stack.currentIndex() != 1:
            return
        if self._volume_slider_role(self._current_volume_method()) != "gain":
            return
        self._apply_3d_settings(immediate=True)

    def _debounced_3d_refresh(self) -> None:
        """Apply the latest 3D threshold/gain/contrast settings."""
        self._apply_3d_settings(immediate=False)

    def _on_vol_downsample_changed(self, _idx: int) -> None:
        if self._view_stack.currentIndex() != 1:
            return
        channels = self._current_channel_render_settings()
        active = [(i, ch) for i, ch in enumerate(channels) if ch.get("active", True)]
        nz = max(int(self._metadata.get("size_z", 1)), 1)
        if active and nz >= 2:
            self._load_3d_volumes(active, nz)

    def _reset_vol_camera(self) -> None:
        if not hasattr(self, "_vispy_view"):
            return
        camera = vispy_scene.ArcballCamera(fov=60, distance=None)
        self._vispy_view.camera = camera
        if self._vol_camera_ranges is not None:
            x_range, y_range, z_range = self._vol_camera_ranges
            camera.set_range(x=x_range, y=y_range, z=z_range, margin=0.05)
            camera.center = (
                0.5 * (x_range[0] + x_range[1]),
                0.5 * (y_range[0] + y_range[1]),
                0.5 * (z_range[0] + z_range[1]),
            )
            camera.set_default_state()
        self._vispy_canvas.update()

    def _current_volume_method(self) -> str:
        idx = self._vol_method_combo.currentIndex()
        if idx < 0 or idx >= len(self._available_volume_methods):
            return self._active_vol_method
        return self._available_volume_methods[idx]

    def _refresh_volume_method_options(self, channels: list[dict] | None = None) -> None:
        current = self._active_vol_method
        fluorescence_like = _channels_look_fluorescence_like(channels or self._display_channels())
        methods = [m for m in _VOLUME_METHODS if m != "minip" or not fluorescence_like]
        if current not in methods:
            current = methods[0]
        self._available_volume_methods = methods
        self._active_vol_method = current
        self._vol_method_combo.blockSignals(True)
        self._vol_method_combo.clear()
        self._vol_method_combo.addItems([_VOLUME_METHOD_LABELS[m] for m in methods])
        self._vol_method_combo.setCurrentIndex(methods.index(current))
        self._vol_method_combo.blockSignals(False)
        self._set_volume_slider_ui(
            current,
            self._vol_method_values.get(current, _VOLUME_METHOD_UI[current]["default"]),
        )
        self._update_volume_interpolation_ui(current)

    def _volume_slider_role(self, method: str | None = None) -> str:
        method = method or self._current_volume_method()
        return _VOLUME_METHOD_UI[method]["role"]

    def _volume_slider_value(self, method: str | None = None) -> float:
        method = method or self._current_volume_method()
        return self._vol_thresh_slider.value() / 100.0

    def _volume_gain(self, method: str, slider_val: float) -> float:
        return slider_val if self._volume_slider_role(method) == "gain" else 1.0

    def _prepare_volume_data(
        self,
        stack: np.ndarray,
        lo: float,
        hi: float,
        method: str,
        gain: float,
    ) -> np.ndarray:
        fvol = stack.astype(np.float32)
        denom = hi - lo if hi > lo else 1.0
        fvol = (fvol - lo) / denom
        np.clip(fvol, 0.0, 1.0, out=fvol)
        if method == "translucent":
            np.power(fvol, 0.5, out=fvol)  # brighter midtones without losing depth too fast
            fvol *= gain
        elif method == "average":
            # Average rendering benefits from a smooth exposure curve: it
            # brightens faint structures without immediately clipping the
            # whole volume, so the gain slider stays visually responsive.
            np.power(fvol, 0.7, out=fvol)
            fvol = 1.0 - np.exp(-(fvol * gain * 1.8))
        else:
            fvol *= gain
        np.clip(fvol, 0.0, 1.0, out=fvol)
        return fvol

    def _current_volume_interpolation(self) -> str:
        return "linear" if self._vol_linear_interpolation else "nearest"

    def _update_volume_interpolation_ui(self, method: str | None = None) -> None:
        method = method or self._current_volume_method()
        visible = method in _INTERPOLATION_TOGGLE_METHODS
        self._vol_interp_check.setVisible(visible)

    def _apply_volume_method_param(self, vol, method: str, slider_val: float) -> None:
        role = self._volume_slider_role(method)
        if role == "threshold":
            vol.threshold = slider_val
        elif role == "attenuation":
            vol.attenuation = slider_val
        elif role == "mip_cutoff":
            vol.mip_cutoff = slider_val
        elif role == "minip_cutoff":
            vol.minip_cutoff = slider_val

    def _set_volume_slider_ui(self, method: str, value: int) -> None:
        spec = _VOLUME_METHOD_UI[method]
        min_val, max_val = spec["range"]
        clamped = max(min_val, min(int(value), max_val))
        self._vol_slider_label.setText(spec["label"])
        self._vol_thresh_slider.blockSignals(True)
        self._vol_thresh_slider.setRange(min_val, max_val)
        self._vol_thresh_slider.setValue(clamped)
        self._vol_thresh_slider.blockSignals(False)
        self._vol_slider_val.setText(f"{clamped / 100:.2f}")

    def _apply_3d_settings(self, *, immediate: bool) -> None:
        if self._view_stack.currentIndex() != 1:
            return
        if immediate:
            self._3d_debounce.stop()
        method = self._current_volume_method()
        slider_val = self._vol_thresh_slider.value() / 100.0
        for vol in self._vispy_volumes:
            vol.method = method
            vol.interpolation = self._current_volume_interpolation()
            if method == "iso":
                vol.threshold = slider_val
        self._refresh_3d_contrast()

    def _open_omero(self):
        try:
            from omero_browser_qt import (
                OmeroBrowserDialog,
                RegularImagePlaneProvider,
                is_large_image,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Import Error",
                                 f"Failed to load OMERO modules:\n{exc}")
            return

        try:
            contexts = OmeroBrowserDialog.select_image_contexts(self)
        except Exception as exc:
            QMessageBox.critical(self, "OMERO Browser Error",
                                 f"Failed to open browser dialog:\n{exc}")
            return
        if not contexts:
            return

        context = contexts[0]
        image = context.image
        self._status.showMessage(f"Loading {image.getName()} from OMERO…")
        QApplication.processEvents()

        try:
            if is_large_image(image):
                from omero_browser_qt import PyramidTileProvider

                provider = PyramidTileProvider(image)
                self._set_tiled_data(provider, context=context)
            else:
                provider = RegularImagePlaneProvider(image)
                self._set_regular_provider(provider, context=context)
        except Exception as exc:  # noqa: BLE001
            self._status.clearMessage()
            QMessageBox.critical(self, "OMERO Error", str(exc))

    # ------------------------------------------------------------------
    # Set image data
    # ------------------------------------------------------------------

    def _set_data(
        self,
        volumes: list[np.ndarray],
        metadata: dict[str, Any],
        *,
        context=None,
    ) -> None:
        self._prepare_for_new_image()
        # Clean up tiled mode if active
        if self._tiled_item is not None:
            self._viewer._scene.removeItem(self._tiled_item)
            self._tiled_item = None
            self._tile_provider = None
            self._overview_cache.clear()
        self._viewer._pix_item.setVisible(True)
        self._regular_provider = None

        self._volumes = volumes
        self._metadata = metadata
        self._selection_context = context
        self._pct_cache.clear()
        self._viewer.set_scale_bar_um_per_pixel(self._metadata.get("pixel_size_x"))

        channels = self._display_channels()
        self._refresh_volume_method_options(channels)
        self._channel_colors = _resolve_channel_colors(channels)
        self._rebuild_channel_toggles(channels)

        self._configure_dimension_controls()

        self._request_view_update()

        self._refresh_metadata_labels()
        self._3d_btn.setEnabled(_HAS_VISPY and len(self._volumes) > 0)
        if _HAS_VISPY:
            self._3d_btn.setToolTip("Open 3D volume viewer")
        else:
            self._3d_btn.setToolTip("vispy not installed — pip install vispy")

        self._fit_after_load()

    def _set_regular_provider(self, provider, *, context=None) -> None:
        """Set up the viewer for on-demand plane loading."""
        self._prepare_for_new_image()
        if self._tiled_item is not None:
            self._viewer._scene.removeItem(self._tiled_item)
            self._tiled_item = None
            self._tile_provider = None
            self._overview_cache.clear()
        self._viewer._pix_item.setVisible(True)

        self._regular_provider = provider
        self._volumes = []
        self._metadata = provider.metadata
        self._selection_context = context
        self._pct_cache.clear()
        self._viewer.set_scale_bar_um_per_pixel(self._metadata.get("pixel_size_x"))
        self._set_projection_modes(_PROJECTION_MODES)
        self._3d_btn.setEnabled(_HAS_VISPY)
        if _HAS_VISPY:
            self._3d_btn.setToolTip("Open 3D volume viewer")
        else:
            self._3d_btn.setToolTip("vispy not installed — pip install vispy")

        channels = self._display_channels()
        self._refresh_volume_method_options(channels)
        self._channel_colors = _resolve_channel_colors(channels)
        self._rebuild_channel_toggles(channels)
        self._configure_dimension_controls()
        self._request_view_update()
        self._refresh_metadata_labels()
        self._fit_after_load()

    def _set_tiled_data(self, provider, *, context=None) -> None:
        """Set up the viewer in tiled pyramid mode."""
        self._prepare_for_new_image()
        # Clean up previous tiled item
        if self._tiled_item is not None:
            self._viewer._scene.removeItem(self._tiled_item)
            self._tiled_item = None

        # Clear non-tiled state
        self._volumes = []
        self._pct_cache.clear()
        self._overview_cache.clear()
        self._regular_provider = None
        self._tile_provider = provider
        self._viewer._pix_item.setVisible(False)
        self._viewer._pix_item.setPixmap(QPixmap())

        meta = provider.metadata
        self._metadata = meta
        self._selection_context = context
        self._viewer.set_scale_bar_um_per_pixel(self._metadata.get("pixel_size_x"))
        self._set_projection_modes(_PROJECTION_MODES)
        self._3d_btn.setEnabled(False)
        self._3d_btn.setToolTip("3D viewer not available for tiled images")

        channels = self._display_channels()
        self._refresh_volume_method_options(channels)
        self._channel_colors = _resolve_channel_colors(channels)
        self._rebuild_channel_toggles(channels)

        if self._proj_combo.currentText() != "Slice":
            self._proj_combo.setCurrentText("Slice")
        self._configure_dimension_controls()

        # Create tiled item and add to scene
        item = TiledImageItem(provider)
        self._tiled_item = item
        self._viewer._scene.addItem(item)

        fw, fh = provider.full_size()
        self._viewer._scene.setSceneRect(QRectF(0, 0, fw, fh))

        self._request_view_update()
        self._refresh_metadata_labels()
        self._fit_after_load()

    def _build_overview_pixmap(self, active, contrast, overview_volumes):
        """Composite the level-0 overview for all *active* channels."""
        slices = []
        for c in active:
            if c >= len(overview_volumes):
                continue
            arr = overview_volumes[c][0]  # (Y, X)
            color = (
                self._channel_colors[c]
                if c < len(self._channel_colors)
                else (255, 255, 255)
            )
            lo_hi = contrast.get(c, (float(arr.min()), float(arr.max())))
            slices.append((arr, color, lo_hi))
        if slices:
            return _composite_to_pixmap(slices)
        return QPixmap()

    # ------------------------------------------------------------------
    # Channel toggles
    # ------------------------------------------------------------------

    def _rebuild_channel_toggles(self, channels: list[dict]) -> None:
        # Remove old buttons
        for btn in self._channel_buttons:
            self._ch_row.removeWidget(btn)
            btn.deleteLater()
        self._channel_buttons.clear()

        rgb_ui = _channels_look_like_rgb(channels)
        for i, ch in enumerate(channels):
            name = ch.get("name", f"Ch{i}")
            r, g, b = self._channel_colors[i] if i < len(self._channel_colors) else (200, 200, 200)
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(bool(ch.get("active", True)))
            if rgb_ui:
                hover_r = min(r + 24, 255)
                hover_g = min(g + 24, 255)
                hover_b = min(b + 24, 255)
                off_r = max(int(r * 0.4), 42)
                off_g = max(int(g * 0.4), 42)
                off_b = max(int(b * 0.4), 42)
                btn.setStyleSheet(
                    f"QPushButton {{ background: rgba({r},{g},{b},224); color: #f8fafc; "
                    f"border: none; border-radius: 999px; padding: 3px 12px; font-weight: 700; }}"
                    f"QPushButton:hover {{ background: rgba({hover_r},{hover_g},{hover_b},242); }}"
                    f"QPushButton:checked {{ border: 2px solid rgba(255,255,255,140); }}"
                    f"QPushButton:!checked {{ background: rgba({off_r},{off_g},{off_b},188); color: #dbe4ee; border: 1px solid rgba({r},{g},{b},155); }}"
                )
            else:
                gray = max(96, min(int(0.2126 * r + 0.7152 * g + 0.0722 * b), 220))
                hover_gray = min(gray + 18, 238)
                text_color = "#111315" if gray >= 150 else "#f3f4f6"
                btn.setStyleSheet(
                    f"QPushButton {{ background: rgba({gray},{gray},{gray},216); color: {text_color}; "
                    f"border: none; border-radius: 999px; padding: 3px 12px; font-weight: 700; }}"
                    f"QPushButton:hover {{ background: rgba({hover_gray},{hover_gray},{hover_gray},235); }}"
                    f"QPushButton:checked {{ border: 2px solid rgba(255,255,255,110); }}"
                    f"QPushButton:!checked {{ background: #262a2e; color: #8f969d; border: 1px solid #43484d; }}"
                )
            btn.toggled.connect(self._request_view_update)
            # Insert before the stretch
            self._ch_row.insertWidget(self._ch_row.count() - 1, btn)
            self._channel_buttons.append(btn)

    def _display_channels(self) -> list[dict]:
        from omero_browser_qt import get_image_display_settings

        settings = get_image_display_settings(self._metadata)
        channels = [
            {
                "index": ch.index,
                "name": ch.name,
                "color": ch.color,
                "emission_wavelength": ch.emission_wavelength,
                "active": ch.active,
                "window_start": ch.window_start,
                "window_end": ch.window_end,
            }
            for ch in settings.channels
        ]
        if _channels_look_like_rgb(channels):
            for i, ch in enumerate(channels):
                ch["name"] = _RGB_CHANNEL_NAMES[i]
        return channels

    def _current_channel_render_settings(self) -> list[dict]:
        channels = self._display_channels()
        for i, ch in enumerate(channels):
            if i < len(self._channel_buttons):
                ch["active"] = self._channel_buttons[i].isChecked()
        return channels

    def _set_projection_modes(self, modes: list[str]) -> None:
        current = self._proj_combo.currentText()
        self._proj_combo.blockSignals(True)
        self._proj_combo.clear()
        self._proj_combo.addItems(modes)
        if current in modes:
            self._proj_combo.setCurrentText(current)
        else:
            self._proj_combo.setCurrentIndex(0)
        self._proj_combo.blockSignals(False)

    def _configure_dimension_controls(self) -> None:
        nz = max(int(self._metadata.get("size_z", 1)), 1)
        nt = max(int(self._metadata.get("size_t", 1)), 1)
        if nz <= 1 and self._proj_combo.currentText() != "Slice":
            self._proj_combo.blockSignals(True)
            self._proj_combo.setCurrentText("Slice")
            self._proj_combo.blockSignals(False)
        self._t_slider.blockSignals(True)
        self._t_slider.setMaximum(nt - 1)
        self._t_slider.setValue(max(0, min(int(self._metadata.get("default_t", 0)), nt - 1)))
        self._t_slider.blockSignals(False)
        default_z = max(0, min(int(self._metadata.get("default_z", nz // 2 if nz > 1 else 0)), nz - 1))
        self._z_slider.blockSignals(True)
        self._z_slider.setMaximum(nz - 1)
        self._z_slider.setValue(default_z)
        self._z_slider.blockSignals(False)

    def _current_t(self) -> int:
        nt = max(int(self._metadata.get("size_t", 1)), 1)
        return max(0, min(self._t_slider.value(), nt - 1))

    def _current_z(self) -> int:
        nz = max(int(self._metadata.get("size_z", 1)), 1)
        return max(0, min(self._z_slider.value(), nz - 1))

    def _volume_frame(self, vol: np.ndarray) -> np.ndarray:
        if vol.ndim == 4:
            return vol[self._current_t()]
        return vol

    def _regular_channel_stack(self, c: int, progress=None) -> np.ndarray:
        if self._regular_provider is not None:
            return self._regular_provider.get_stack(c, self._current_t(), progress=progress)
        if self._volumes:
            if progress is not None:
                frame = self._volume_frame(self._volumes[c])
                progress(frame.shape[0], frame.shape[0])
                return frame
            return self._volume_frame(self._volumes[c])
        raise RuntimeError("No regular image data available")

    def _regular_channel_plane(self, c: int) -> np.ndarray:
        if self._regular_provider is not None:
            return self._regular_provider.get_plane(c, self._current_z(), self._current_t())
        if self._volumes:
            frame = self._volume_frame(self._volumes[c])
            return frame[self._current_z()]
        raise RuntimeError("No regular image data available")

    def _prefetch_regular_neighbors(self, channels: list[int]) -> None:
        if self._regular_provider is None or not channels:
            return
        try:
            self._regular_provider.prefetch_neighbors(
                channels,
                self._current_z(),
                self._current_t(),
            )
        except Exception:  # noqa: BLE001
            pass

    def _tiled_overview(
        self,
        mode: str,
        z: int,
        t: int,
        request_id: int,
    ) -> list[np.ndarray]:
        key = (mode, z if mode == "Slice" else -1, t)
        if key not in self._overview_cache:
            if mode == "Slice":
                self._overview_cache[key] = self._tile_provider.load_overview(z=z, t=t)
            else:
                planes_per_channel = None
                z_count = max(int(self._metadata.get("size_z", 1)), 1)
                self._begin_progress(z_count, f"Computing {mode} overview…")
                try:
                    for zi in range(z_count):
                        self._advance_progress(zi, f"Computing {mode} overview… Z {zi + 1}/{z_count}")
                        self._ensure_render_current(request_id)
                        volumes = self._tile_provider.load_overview(z=zi, t=t)
                        if planes_per_channel is None:
                            planes_per_channel = [[arr[0]] for arr in volumes]
                        else:
                            for stack, arr in zip(planes_per_channel, volumes, strict=False):
                                stack.append(arr[0])
                    if planes_per_channel is None:
                        planes_per_channel = []
                    self._ensure_render_current(request_id)
                    self._overview_cache[key] = [
                        _project_stack(np.stack(stack, axis=0), mode, z)[np.newaxis, ...]
                        for stack in planes_per_channel
                    ]
                finally:
                    self._advance_progress(z_count)
                    self._end_progress()
        return self._overview_cache[key]

    def _refresh_metadata_labels(self) -> None:
        name = self._metadata.get("name", "No image loaded")
        sx = self._metadata.get("size_x", "—")
        sy = self._metadata.get("size_y", "—")
        sz = self._metadata.get("size_z", "—")
        st = self._metadata.get("size_t", "—")
        sc = self._metadata.get("size_c", "—")
        extra = ""
        if self._tile_provider is not None:
            extra = f"  |  Pyramid levels {self._tile_provider.n_levels}"
        pix_x = self._metadata.get("pixel_size_x")
        pix_y = self._metadata.get("pixel_size_y")
        scale = ""
        if pix_x and pix_y:
            scale = f"  |  {pix_x:g}×{pix_y:g} um/px"
        elif pix_x:
            scale = f"  |  {pix_x:g} um/px"
        breadcrumb = ""
        if self._selection_context is not None and self._selection_context.breadcrumb:
            breadcrumb = self._selection_context.breadcrumb
        self._path_label.setText(breadcrumb)
        info = f"{name}  |  {sx}×{sy} px  |  Z {sz}  C {sc}  T {st}{scale}{extra}"
        self._status.showMessage(info)

    # ------------------------------------------------------------------
    # Get current slice / projection
    # ------------------------------------------------------------------

    def _get_slice(self, vol: np.ndarray) -> np.ndarray:
        frame = self._volume_frame(vol)
        mode = self._proj_combo.currentText()
        return _project_stack(frame, mode, self._current_z())

    def _get_contrast(self, arr: np.ndarray, ch_idx: int) -> tuple[float, float]:
        lo_pct = self._lo_spin.value()
        hi_pct = self._hi_spin.value()
        mode = self._proj_combo.currentText()
        z = self._current_z() if mode == "Slice" else -1
        t = self._current_t()
        key = (ch_idx, mode, z, t, lo_pct, hi_pct)
        if key in self._pct_cache:
            return self._pct_cache[key]
        lo_val = float(np.percentile(arr, lo_pct))
        hi_val = float(np.percentile(arr, hi_pct))
        self._pct_cache[key] = (lo_val, hi_val)
        return lo_val, hi_val

    # ------------------------------------------------------------------
    # Update display
    # ------------------------------------------------------------------

    def _on_contrast_changed(self):
        self._pct_cache.clear()
        self._request_view_update()
        if self._view_stack.currentIndex() == 1:
            self._3d_debounce.start()  # coalesce rapid contrast edits

    def _update_viewer(self, request_id: int | None = None):
        if request_id is None:
            self._request_view_update()
            return
        if self._rendering:
            self._pending_render = True
            return
        self._rendering = True
        mode = self._proj_combo.currentText()
        try:
            # ---- Tiled pyramid mode ------------------------------------
            if self._tiled_item is not None:
                nz = self._metadata.get("size_z", 1)
                nt = self._metadata.get("size_t", 1)
                z = self._current_z()
                t = self._current_t()
                self._z_label.setText(f"{z}/{nz - 1}")
                self._t_label.setText(f"{t}/{nt - 1}")
                self._z_slider.setEnabled(nz > 1 and mode == "Slice")
                self._t_slider.setEnabled(nt > 1)
                self._proj_combo.setEnabled(nz > 1)

                active = []
                for i, btn in enumerate(self._channel_buttons):
                    if btn.isChecked():
                        active.append(i)

                contrast: dict[int, tuple[float, float]] = {}

                overview_volumes = self._tiled_overview(
                    mode,
                    z,
                    t,
                    request_id,
                )
                self._ensure_render_current(request_id)
                for c in active:
                    if c < len(overview_volumes):
                        arr = overview_volumes[c][0]  # (Y, X)
                        contrast[c] = self._get_contrast(arr, c)

                overview_pix = self._build_overview_pixmap(active, contrast, overview_volumes)
                self._tiled_item.set_overview(overview_pix)
                self._tiled_item.set_display(
                    active, self._channel_colors, contrast, z, t, mode
                )
                return

            # ---- Regular (non-tiled) mode ------------------------------
            if self._regular_provider is None and not self._volumes:
                return

            nz = max(int(self._metadata.get("size_z", 1)), 1)
            nt = max(int(self._metadata.get("size_t", 1)), 1)
            z = self._current_z()
            t = self._current_t()
            self._z_label.setText(f"{z}/{nz - 1}")
            self._t_label.setText(f"{t}/{nt - 1}")
            self._z_slider.setEnabled(mode == "Slice")
            self._t_slider.setEnabled(nt > 1)
            self._proj_combo.setEnabled(nz > 1)

            slices = []
            channel_count = self._metadata.get("size_c", len(self._volumes))
            active_indices = [
                i for i, btn in enumerate(self._channel_buttons[:channel_count])
                if btn.isChecked()
            ]
            use_progress = mode != "Slice" and len(active_indices) > 0
            channel_stacks: dict[int, np.ndarray] = {}
            progress_total = 0
            if use_progress:
                for i in active_indices:
                    progress_total += nz
                    progress_total += _projection_step_count(
                        np.empty((nz, 1, 1), dtype=np.uint8),
                        mode,
                    )
            if use_progress and progress_total > 0:
                self._begin_progress(progress_total, f"Loading {mode} data…")
            try:
                progress_done = 0
                if use_progress:
                    for done, i in enumerate(active_indices, start=1):
                        self._ensure_render_current(request_id)
                        load_steps = nz

                        def load_progress(step: int, total: int, *, channel_index=done, base=progress_done):
                            overall = base + min(step, total)
                            self._advance_progress(
                                overall,
                                f"Loading {mode} data… channel {channel_index}/{len(active_indices)}",
                            )
                            self._ensure_render_current(request_id)

                        stack = self._regular_channel_stack(i, progress=load_progress)
                        channel_stacks[i] = stack
                        progress_done += load_steps
                        self._advance_progress(
                            progress_done,
                            f"Loaded {mode} data… channel {done}/{len(active_indices)}",
                        )
                        self._ensure_render_current(request_id)

                for done, i in enumerate(active_indices, start=1):
                    self._ensure_render_current(request_id)
                    if mode == "Slice":
                        arr = self._regular_channel_plane(i)
                        stack = arr[np.newaxis, ...]
                    else:
                        stack = channel_stacks[i] if i in channel_stacks else self._regular_channel_stack(i)
                    channel_steps = _projection_step_count(stack, mode)
                    if use_progress:
                        def progress(step: int, total: int, *, channel_index=done, base=progress_done):
                            overall = base + min(step, total)
                            self._advance_progress(
                                overall,
                                f"Computing {mode} projection… channel {channel_index}/{len(active_indices)}",
                            )
                            self._ensure_render_current(request_id)
                    else:
                        progress = None
                    if mode != "Slice":
                        arr = _project_stack(stack, mode, self._current_z(), progress=progress)
                    color = self._channel_colors[i] if i < len(self._channel_colors) else (255, 255, 255)
                    lo, hi = self._get_contrast(arr, i)
                    slices.append((arr, color, (lo, hi)))
                    if use_progress:
                        progress_done += channel_steps
                        self._advance_progress(
                            progress_done,
                            f"Computing {mode} projection… channel {done}/{len(active_indices)}",
                        )
                        self._ensure_render_current(request_id)
            finally:
                if use_progress:
                    self._end_progress()

            self._ensure_render_current(request_id)
            if slices:
                pix = _composite_to_pixmap(slices)
                self._viewer.set_pixmap(pix)
            else:
                self._viewer.set_pixmap(QPixmap())
            if mode == "Slice":
                self._prefetch_regular_neighbors(active_indices)
        except _RenderCancelled:
            return
        finally:
            self._rendering = False
            if self._pending_render:
                self._pending_render = False
                QTimer.singleShot(0, self._request_view_update)


# ======================================================================
# 3D Volume Viewer helpers (vispy)
# ======================================================================

if _HAS_VISPY:

    class _ChannelColormap(BaseColormap):
        """Translucent colormap that ramps a single RGB channel color."""

        glsl_map = ""

        def __init__(self, rgb: tuple[int, int, int], *, translucent_boost: bool = False):
            r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
            if translucent_boost:
                # Brighter midtones with a softer alpha ramp help translucent mode
                # keep depth while making structures read more clearly.
                self.glsl_map = (
                    "vec4 channel_cmap(float t) {{\n"
                    "    float c = clamp(pow(t, 0.55) * 1.18, 0.0, 1.0);\n"
                    "    float a = clamp(pow(t, 1.35) * 0.82, 0.0, 1.0);\n"
                    "    return vec4({r:.4f} * c, {g:.4f} * c, {b:.4f} * c, a);\n"
                    "}}\n"
                ).format(r=r, g=g, b=b)
            else:
                self.glsl_map = (
                    "vec4 channel_cmap(float t) {{\n"
                    "    return vec4({r:.4f} * t, {g:.4f} * t, {b:.4f} * t, "
                    "clamp(t * 1.2, 0.0, 1.0));\n"
                    "}}\n"
                ).format(r=r, g=g, b=b)
            super().__init__()


# ======================================================================
# Entry point
# ======================================================================

def main():
    # Install global exception hook so PyQt6 slots don't silently kill the app
    def _excepthook(etype, value, tb):
        import traceback
        msg = "".join(traceback.format_exception(etype, value, tb))
        print(msg, file=sys.stderr, flush=True)
        try:
            QMessageBox.critical(None, "Unhandled Error", msg[:2000])
        except Exception:
            pass
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setApplicationName("OMERO Viewer")
    win = ViewerWindow()
    win.show()
    code = app.exec()

    # Preserve a reusable OMERO session token for a short period if one is
    # cached, otherwise fall back to a normal disconnect.
    from omero_browser_qt import OmeroGateway
    OmeroGateway().shutdown_for_exit()

    sys.exit(code)


if __name__ == "__main__":
    main()
