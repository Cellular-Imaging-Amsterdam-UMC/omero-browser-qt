"""Reusable image-view backend helpers.

Defines the :class:`WebRenderedImageBackend` which fetches
server-rendered images from OMERO.web/WebGateway, and the
backend identifier constants :data:`VIEW_BACKEND_ICE` and
:data:`VIEW_BACKEND_WEB`.
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtGui import QImage, QPixmap

from .image_loader import get_image_metadata
from .webclient import OmeroWebClient

VIEW_BACKEND_ICE = "ICE"
VIEW_BACKEND_WEB = "WEB"

_WEB_PROJECTION_MAP = {
    "Slice": "normal",
    "MIP": "intmax",
    "Mean": "intmean",
}


class WebRenderedImageBackend:
    """Rendered-image backend backed by OMERO.web/WebGateway."""

    def __init__(self, image, gateway, *, web_client: OmeroWebClient | None = None):
        """Create a WEB rendering backend for *image*.

        Parameters
        ----------
        image : omero.gateway.ImageWrapper
            The OMERO image to render.
        gateway : OmeroGateway
            Connected gateway (used for web credentials).
        web_client : OmeroWebClient | None
            Optional pre-configured web client.  A new one is created
            automatically if omitted.
        """
        self._image = image
        self._gateway = gateway
        self._client = web_client or OmeroWebClient(gateway)
        self._metadata = get_image_metadata(image)
        self._merge_img_data_defaults()

    @property
    def metadata(self) -> dict:
        return self._metadata

    @property
    def image(self):
        return self._image

    def supports_projection(self, mode: str) -> bool:
        """Return *True* if *mode* (e.g. ``"Slice"``, ``"MIP"``) is supported."""
        return mode in _WEB_PROJECTION_MAP

    def render_pixmap(
        self,
        *,
        z: int,
        t: int,
        mode: str,
        channels: list[dict],
    ) -> QPixmap:
        """Fetch a server-rendered image and return it as a ``QPixmap``.

        Parameters
        ----------
        z : int
            Z-plane index (only used for ``"Slice"`` mode).
        t : int
            Timepoint index.
        mode : str
            Projection mode: ``"Slice"``, ``"MIP"``, or ``"Mean"``.
        channels : list[dict]
            Channel rendering settings (active, color, window).

        Returns
        -------
        QPixmap
            The rendered image, or an empty pixmap if no channels are active.
        """
        if not self.supports_projection(mode):
            raise RuntimeError(f"WEB backend does not support projection mode '{mode}' yet")
        spec = _build_channel_spec(channels)
        if spec is None:
            return QPixmap()
        data = self._client.render_image(
            self._metadata["id"],
            z=z if mode == "Slice" else None,
            t=t if mode == "Slice" else None,
            channel_spec=spec,
            projection=_WEB_PROJECTION_MAP[mode],
        )
        qimg = QImage.fromData(data)
        if qimg.isNull():
            raise RuntimeError("OMERO.web returned an invalid rendered image")
        return QPixmap.fromImage(qimg)

    def get_rendered_plane(
        self,
        z: int,
        t: int,
        channels: list[dict],
    ) -> np.ndarray:
        """Fetch a single server-rendered plane as an RGB ``(H, W, 3)`` uint8 array."""
        spec = _build_channel_spec(channels)
        if spec is None:
            return np.empty((0, 0, 3), dtype=np.uint8)
        data = self._client.render_image(
            self._metadata["id"],
            z=z,
            t=t,
            channel_spec=spec,
            projection="normal",
        )
        qimg = QImage.fromData(data)
        if qimg.isNull():
            raise RuntimeError("OMERO.web returned an invalid rendered image")
        qimg = qimg.convertToFormat(QImage.Format.Format_RGB888)
        w, h = qimg.width(), qimg.height()
        ptr = qimg.bits()
        ptr.setsize(h * w * 3)
        return np.frombuffer(ptr, dtype=np.uint8).reshape(h, w, 3).copy()

    def get_rendered_stack(
        self,
        t: int,
        channels: list[dict],
        *,
        progress=None,
    ) -> np.ndarray:
        """Fetch all Z-planes as a ``(Z, H, W, 3)`` uint8 RGB stack."""
        nz = max(int(self._metadata.get("size_z", 1)), 1)
        planes: list[np.ndarray] = []
        for zi in range(nz):
            planes.append(self.get_rendered_plane(zi, t, channels))
            if progress is not None:
                progress(zi + 1, nz)
        return np.stack(planes, axis=0)

    def _merge_img_data_defaults(self) -> None:
        try:
            img_data = self._client.get_img_data(self._metadata["id"])
        except Exception:
            return
        rdefs = img_data.get("rdefs", {}) or {}
        channels = img_data.get("channels", []) or []
        self._metadata["default_z"] = int(rdefs.get("defaultZ", 0) or 0)
        self._metadata["default_t"] = int(rdefs.get("defaultT", 0) or 0)
        for idx, ch in enumerate(channels):
            if idx >= len(self._metadata["channels"]):
                continue
            meta_ch = self._metadata["channels"][idx]
            meta_ch["active"] = bool(ch.get("active", meta_ch.get("active", True)))
            label = ch.get("label")
            if label:
                meta_ch["name"] = label
            color = ch.get("color")
            if isinstance(color, str) and len(color) == 6:
                meta_ch["color"] = (
                    int(color[0:2], 16),
                    int(color[2:4], 16),
                    int(color[4:6], 16),
                )
            window = ch.get("window") or {}
            if "start" in window:
                meta_ch["window_start"] = float(window["start"])
            if "end" in window:
                meta_ch["window_end"] = float(window["end"])


def _build_channel_spec(channels: list[dict]) -> str | None:
    parts: list[str] = []
    for i, ch in enumerate(channels, start=1):
        if not ch.get("active", True):
            continue
        part = str(ch.get("index", i - 1) + 1)
        lo = ch.get("window_start")
        hi = ch.get("window_end")
        if lo is not None and hi is not None:
            part += f"|{float(lo)}:{float(hi)}"
        color = ch.get("color")
        if color is not None:
            r, g, b = color
            part += f"${r:02X}{g:02X}{b:02X}"
        parts.append(part)
    return ",".join(parts) if parts else None
