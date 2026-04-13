#!/usr/bin/env python
"""
viewer_demo.py — Minimal multi-channel image viewer that demonstrates
the ``omero-browser-qt`` reusable dialog.

Keeps from gui_deconvolve_ci.py:
  * ZoomableImageView (single window)
  * Multi-channel compositing (_composite_to_pixmap)
  * Channel toggle buttons (dynamic, coloured by emission wavelength)
  * Projection combo (Slice / MIP / SUM)
  * Z-slider
  * Lo% / Hi% contrast sliders with percentile caching
  * Open from OMERO

Removed:
  * Deconvolution, PSF, optics, second viewer, resource monitor, settings
"""

from __future__ import annotations

import math
import sys
from collections import OrderedDict
from functools import lru_cache
from typing import Any

import numpy as np
from PyQt6.QtCore import QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
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
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QGraphicsObject,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

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


# ======================================================================
# ZoomableImageView
# ======================================================================

class ZoomableImageView(QGraphicsView):
    """Pannable, zoomable image viewer widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pix_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pix_item)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setStyleSheet("background: #1a1a1a; border: none;")

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pix_item.setPixmap(pix)
        self._scene.setSceneRect(QRectF(pix.rect()))

    def fit_in_view(self) -> None:
        if not self._pix_item.pixmap().isNull():
            self.fitInView(self._pix_item, Qt.AspectRatioMode.KeepAspectRatio)
        elif self._scene.sceneRect().width() > 0:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event):  # noqa: N802
        factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(factor, factor)
        else:
            self.scale(1 / factor, 1 / factor)


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

        # Composited overview pixmap (level 0, all channels)
        self._overview_pix: QPixmap = QPixmap()
        # Composited tile cache: (level, tx, ty) -> QPixmap
        self._tile_cache: OrderedDict = OrderedDict()
        self._max_comp_tiles = 500

        self._worker: _TileFetchWorker | None = None

    def boundingRect(self):
        return QRectF(0, 0, self._fw, self._fh)

    def set_overview(self, pix: QPixmap):
        self._overview_pix = pix

    def set_display(self, active, colors, contrast, z):
        self._active = active
        self._colors = colors
        self._contrast = contrast
        self._z = z
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

        missing = []
        for tyi in range(ty0, ty1):
            for txi in range(tx0, tx1):
                comp_key = (level, txi, tyi)

                if comp_key in self._tile_cache:
                    self._tile_cache.move_to_end(comp_key)
                    pix = self._tile_cache[comp_key]
                else:
                    pix = self._try_composite(level, txi, tyi)
                    if pix is not None:
                        while len(self._tile_cache) >= self._max_comp_tiles:
                            self._tile_cache.popitem(last=False)
                        self._tile_cache[comp_key] = pix
                    else:
                        # Queue missing channel tiles for background fetch
                        for c in self._active:
                            if not self._prov.has_tile(
                                level, c, self._z, self._t, txi, tyi
                            ):
                                missing.append(
                                    (level, c, self._z, self._t, txi, tyi)
                                )
                        continue

                if pix and not pix.isNull():
                    ix = txi * tw / fx
                    iy = tyi * th / fy
                    iw = pix.width() / fx
                    ih = pix.height() / fy
                    painter.drawPixmap(
                        QRectF(ix, iy, iw, ih),
                        pix,
                        QRectF(0, 0, pix.width(), pix.height()),
                    )

        if missing and (self._worker is None or not self._worker.isRunning()):
            self._worker = _TileFetchWorker(self._prov, missing)
            self._worker.done.connect(self.update)
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
            arr = self._prov.get_cached_tile(level, c, self._z, self._t, tx, ty)
            if arr is None:
                return None  # Not all channels cached yet
            tiles.append((arr, c))

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


# ======================================================================
# Main window
# ======================================================================

class ViewerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OMERO Viewer Demo")
        self.setWindowIcon(_make_app_icon())
        self.resize(1000, 700)

        # State
        self._volumes: list[np.ndarray] = []  # per-channel (Z, Y, X)
        self._metadata: dict[str, Any] = {}
        self._channel_colors: list[tuple[int, int, int]] = []
        self._channel_buttons: list[QPushButton] = []
        self._pct_cache: dict = {}

        # Tiled pyramid mode
        self._tiled_item: TiledImageItem | None = None
        self._tile_provider = None
        self._overview_volumes: list[np.ndarray] = []

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Toolbar
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        open_omero_act = QAction("Open from OMERO…", self)
        open_omero_act.triggered.connect(self._open_omero)
        tb.addAction(open_omero_act)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_lay = QVBoxLayout(central)
        main_lay.setContentsMargins(4, 4, 4, 4)

        # --- Controls row ---
        ctrl = QHBoxLayout()

        # Projection
        ctrl.addWidget(QLabel("Projection:"))
        self._proj_combo = QComboBox()
        self._proj_combo.addItems(["Slice", "MIP", "SUM"])
        self._proj_combo.currentIndexChanged.connect(self._update_viewer)
        ctrl.addWidget(self._proj_combo)

        # Z slider
        ctrl.addWidget(QLabel("Z:"))
        self._z_slider = QSlider(Qt.Orientation.Horizontal)
        self._z_slider.setMinimum(0)
        self._z_slider.setMaximum(0)
        self._z_slider.valueChanged.connect(self._update_viewer)
        self._z_slider.setMinimumWidth(120)
        ctrl.addWidget(self._z_slider)
        self._z_label = QLabel("0/0")
        ctrl.addWidget(self._z_label)

        # Lo% / Hi%
        ctrl.addWidget(QLabel("Lo%:"))
        self._lo_spin = QDoubleSpinBox()
        self._lo_spin.setRange(0.0, 50.0)
        self._lo_spin.setValue(0.1)
        self._lo_spin.setSingleStep(0.1)
        self._lo_spin.setDecimals(2)
        self._lo_spin.valueChanged.connect(self._on_contrast_changed)
        ctrl.addWidget(self._lo_spin)

        ctrl.addWidget(QLabel("Hi%:"))
        self._hi_spin = QDoubleSpinBox()
        self._hi_spin.setRange(50.0, 100.0)
        self._hi_spin.setValue(100.0)
        self._hi_spin.setSingleStep(0.5)
        self._hi_spin.setDecimals(2)
        self._hi_spin.valueChanged.connect(self._on_contrast_changed)
        ctrl.addWidget(self._hi_spin)

        ctrl.addStretch()
        main_lay.addLayout(ctrl)

        # --- Channel toggles row ---
        self._ch_row = QHBoxLayout()
        self._ch_row.addWidget(QLabel("Channels:"))
        self._ch_row.addStretch()
        main_lay.addLayout(self._ch_row)

        # --- Image viewer ---
        self._viewer = ZoomableImageView()
        main_lay.addWidget(self._viewer, 1)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)

    # ------------------------------------------------------------------
    # Open from OMERO
    # ------------------------------------------------------------------

    def _open_omero(self):
        from omero_browser_qt import (
            LoginDialog,
            OmeroBrowserDialog,
            OmeroGateway,
            is_large_image,
            load_image_data,
            load_image_lazy,
        )

        gw = OmeroGateway()

        # Login if not connected
        if not gw.is_connected():
            dlg = LoginDialog(self, gateway=gw)
            if dlg.exec() != LoginDialog.DialogCode.Accepted:
                return

        # Browse
        browser = OmeroBrowserDialog(self, gateway=gw)
        if browser.exec() != OmeroBrowserDialog.DialogCode.Accepted:
            return

        images = browser.get_selected_images()
        if not images:
            return

        image = images[0]  # Load the first selected image
        self._status.showMessage(f"Loading {image.getName()} from OMERO…")
        QApplication.processEvents()

        try:
            if is_large_image(image):
                from omero_browser_qt import PyramidTileProvider

                provider = PyramidTileProvider(image)
                self._set_tiled_data(provider)
            else:
                result = load_image_data(image)
                self._set_data(result["images"], result["metadata"])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "OMERO Error", str(exc))
        finally:
            self._status.clearMessage()

    # ------------------------------------------------------------------
    # Set image data
    # ------------------------------------------------------------------

    def _set_data(
        self, volumes: list[np.ndarray], metadata: dict[str, Any]
    ) -> None:
        # Clean up tiled mode if active
        if self._tiled_item is not None:
            self._viewer._scene.removeItem(self._tiled_item)
            self._tiled_item = None
            self._tile_provider = None
            self._overview_volumes = []
        self._viewer._pix_item.setVisible(True)

        self._volumes = volumes
        self._metadata = metadata
        self._pct_cache.clear()

        # Channel colours
        channels = metadata.get("channels", [])
        if not channels:
            channels = [
                {"name": f"Ch{i}", "emission_wavelength": None, "color": None}
                for i in range(len(volumes))
            ]
        self._channel_colors = _resolve_channel_colors(channels)

        # Rebuild channel toggles
        self._rebuild_channel_toggles(channels)

        # Z slider
        nz = volumes[0].shape[0] if volumes else 0
        self._z_slider.setMaximum(max(nz - 1, 0))
        self._z_slider.setValue(nz // 2)

        self._update_viewer()

        name = metadata.get("name", "")
        sx = metadata.get("size_x", "?")
        sy = metadata.get("size_y", "?")
        nz = metadata.get("size_z", "?")
        nc = metadata.get("size_c", "?")
        self._status.showMessage(f"{name}  —  {sx}×{sy}  Z={nz}  C={nc}")

        # Fit after the event loop has laid out the widget
        QTimer.singleShot(50, self._viewer.fit_in_view)

    def _set_tiled_data(self, provider) -> None:
        """Set up the viewer in tiled pyramid mode."""
        # Clean up previous tiled item
        if self._tiled_item is not None:
            self._viewer._scene.removeItem(self._tiled_item)
            self._tiled_item = None

        # Clear non-tiled state
        self._volumes = []
        self._pct_cache.clear()
        self._tile_provider = provider
        self._viewer._pix_item.setVisible(False)

        meta = provider.metadata
        self._metadata = meta

        # Load overview (level 0, smallest) for quick display & contrast
        self._overview_volumes = provider.load_overview()

        # Channel colours
        channels = meta.get("channels", [])
        if not channels:
            channels = [
                {"name": f"Ch{i}", "emission_wavelength": None, "color": None}
                for i in range(meta["size_c"])
            ]
        self._channel_colors = _resolve_channel_colors(channels)
        self._rebuild_channel_toggles(channels)

        # Z slider
        nz = meta.get("size_z", 1)
        self._z_slider.setMaximum(max(nz - 1, 0))
        self._z_slider.setValue(nz // 2)

        # Create tiled item and add to scene
        item = TiledImageItem(provider)
        self._tiled_item = item
        self._viewer._scene.addItem(item)

        fw, fh = provider.full_size()
        self._viewer._scene.setSceneRect(QRectF(0, 0, fw, fh))

        self._update_viewer()

        name = meta.get("name", "")
        sx, sy = meta.get("size_x", "?"), meta.get("size_y", "?")
        nc = meta.get("size_c", "?")
        lvl = provider.n_levels
        self._status.showMessage(
            f"{name}  —  {sx}×{sy}  Z={nz}  C={nc}  ({lvl} pyramid levels)"
        )
        QTimer.singleShot(50, self._viewer.fit_in_view)

    def _build_overview_pixmap(self, active, contrast):
        """Composite the level-0 overview for all *active* channels."""
        slices = []
        for c in active:
            if c >= len(self._overview_volumes):
                continue
            arr = self._overview_volumes[c][0]  # (Y, X)
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

        for i, ch in enumerate(channels):
            name = ch.get("name", f"Ch{i}")
            r, g, b = self._channel_colors[i] if i < len(self._channel_colors) else (200, 200, 200)
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setStyleSheet(
                f"QPushButton {{ background: rgb({r},{g},{b}); color: #000; "
                f"border-radius: 3px; padding: 2px 8px; font-weight: bold; }}"
                f"QPushButton:!checked {{ background: #555; color: #aaa; }}"
            )
            btn.toggled.connect(self._update_viewer)
            # Insert before the stretch
            self._ch_row.insertWidget(self._ch_row.count() - 1, btn)
            self._channel_buttons.append(btn)

    # ------------------------------------------------------------------
    # Get current slice / projection
    # ------------------------------------------------------------------

    def _get_slice(self, vol: np.ndarray) -> np.ndarray:
        mode = self._proj_combo.currentText()
        if mode == "MIP":
            return vol.max(axis=0)
        if mode == "SUM":
            return vol.sum(axis=0).astype(np.float64)
        # Slice
        z = self._z_slider.value()
        z = max(0, min(z, vol.shape[0] - 1))
        return vol[z]

    def _get_contrast(self, arr: np.ndarray, ch_idx: int) -> tuple[float, float]:
        lo_pct = self._lo_spin.value()
        hi_pct = self._hi_spin.value()
        mode = self._proj_combo.currentText()
        z = self._z_slider.value() if mode == "Slice" else -1
        key = (ch_idx, mode, z, lo_pct, hi_pct)
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
        self._update_viewer()

    def _update_viewer(self):
        # ---- Tiled pyramid mode ------------------------------------
        if self._tiled_item is not None:
            nz = self._metadata.get("size_z", 1)
            z = self._z_slider.value()
            self._z_label.setText(f"{z}/{nz - 1}")
            self._z_slider.setEnabled(nz > 1)

            active = []
            for i, btn in enumerate(self._channel_buttons):
                if btn.isChecked():
                    active.append(i)

            contrast: dict[int, tuple[float, float]] = {}
            for c in active:
                if c < len(self._overview_volumes):
                    arr = self._overview_volumes[c][0]  # (Y, X)
                    contrast[c] = self._get_contrast(arr, c)

            overview_pix = self._build_overview_pixmap(active, contrast)
            self._tiled_item.set_overview(overview_pix)
            self._tiled_item.set_display(
                active, self._channel_colors, contrast, z
            )
            return

        # ---- Regular (non-tiled) mode ------------------------------
        if not self._volumes:
            return

        # Update Z label
        nz = self._volumes[0].shape[0]
        z = self._z_slider.value()
        self._z_label.setText(f"{z}/{nz - 1}")
        self._z_slider.setEnabled(self._proj_combo.currentText() == "Slice")

        slices = []
        for i, vol in enumerate(self._volumes):
            if i >= len(self._channel_buttons):
                break
            if not self._channel_buttons[i].isChecked():
                continue
            arr = self._get_slice(vol)
            color = self._channel_colors[i] if i < len(self._channel_colors) else (255, 255, 255)
            lo, hi = self._get_contrast(arr, i)
            slices.append((arr, color, (lo, hi)))

        if slices:
            pix = _composite_to_pixmap(slices)
            self._viewer.set_pixmap(pix)
        else:
            self._viewer.set_pixmap(QPixmap())


# ======================================================================
# Entry point
# ======================================================================

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("OMERO Viewer Demo")
    win = ViewerWindow()
    win.show()
    code = app.exec()

    # Cleanly close the OMERO connection so the ICE communicator is
    # destroyed before Python's global teardown.
    from omero_browser_qt import OmeroGateway
    OmeroGateway().disconnect()

    sys.exit(code)


if __name__ == "__main__":
    main()
