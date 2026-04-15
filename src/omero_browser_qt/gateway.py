"""
OmeroGateway — singleton QObject wrapping omero.gateway.BlitzGateway.

Uses ICE transport exclusively for raw pixel access.  Session persistence
is handled via omero.util.sessions.SessionsStore; only the server name(s)
are remembered across runs (stored in QSettings), never credentials.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from PyQt6.QtCore import QObject, QSettings, pyqtSignal

log = logging.getLogger(__name__)

# Pixel-type string → numpy dtype mapping
PIXEL_TYPES: dict[str, str] = {
    "int8": "i1",
    "uint8": "u1",
    "int16": "i2",
    "uint16": "u2",
    "int32": "i4",
    "uint32": "u4",
    "float": "f4",
    "double": "f8",
}

_SETTINGS_KEY = "omero_browser_qt/servers"


class NonCachedPixelsWrapper:
    """Thin wrapper that creates a fresh RawPixelsStore on every call.

    Avoids reuse issues that can occur when a single store is shared
    across multiple fetch operations.
    """

    def __init__(self, image):
        self._image = image
        self._conn = image._conn

    def get_raw_pixels_store(self):
        ps = self._conn.c.sf.createRawPixelsStore()
        pid = self._image.getPrimaryPixels().getId()
        ps.setPixelsId(pid, True, self._conn.SERVICE_OPTS)
        return ps


class OmeroGateway(QObject):
    """Singleton gateway for OMERO server connections.

    Signals
    -------
    connected(str)
        Emitted with the server host when a connection is established.
    disconnected()
        Emitted when the connection is closed.
    error(str)
        Emitted with a human-readable message on connection failure.
    """

    connected = pyqtSignal(str)
    disconnected = pyqtSignal()
    error = pyqtSignal(str)

    _instance: OmeroGateway | None = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._initialised = False
            cls._instance = inst
        return cls._instance

    def __init__(self, parent: QObject | None = None):
        if self._initialised:
            return
        super().__init__(parent)
        self._initialised = True
        self._conn = None
        self._host: str = ""
        self._port: int = 4064
        self._username: str = ""
        self._password: str = ""

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self, host: str, port: int, username: str, password: str) -> bool:
        """Open a connection to the OMERO server.

        Returns *True* on success.  On failure emits :pyqtSignal:`error`
        and returns *False*.
        """
        from omero.gateway import BlitzGateway

        self.disconnect()

        try:
            conn = BlitzGateway(username, password, host=host, port=port, secure=True)
            if not conn.connect():
                msg = conn.getLastError() or "Connection refused"
                self.error.emit(str(msg))
                return False

            conn.c.enableKeepAlive(60)
            self._conn = conn
            self._host = host
            self._port = port
            self._username = username
            self._password = password
            self._save_server(host)
            log.info("Connected to %s as %s", host, username)
            self.connected.emit(host)
            return True
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
            return False

    def disconnect(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None
            self._password = ""
            self.disconnected.emit()

    def is_connected(self) -> bool:
        if self._conn is None:
            return False
        try:
            return self._conn.isConnected()
        except Exception:  # noqa: BLE001
            return False

    def get_connection(self):
        """Return the underlying BlitzGateway or *None*."""
        return self._conn

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def username(self) -> str:
        return self._username

    @property
    def web_base_url(self) -> str:
        if self._host.startswith(("http://", "https://")):
            return self._host.rstrip("/")
        return f"https://{self._host}".rstrip("/")

    def web_credentials(self) -> tuple[str, str]:
        return self._username, self._password

    # ------------------------------------------------------------------
    # Group / user helpers
    # ------------------------------------------------------------------

    def get_groups(self):
        """Return list of (id, name) tuples for groups the user belongs to."""
        if not self.is_connected():
            return []
        return [
            (g.getId(), g.getName())
            for g in self._conn.getGroupsMemberOf()
        ]

    def get_experimenters_in_group(self, group_id: int):
        """Return list of (id, full_name) tuples for a group."""
        if not self.is_connected():
            return []
        self._conn.SERVICE_OPTS.setOmeroGroup(group_id)
        result = []
        for exp in self._conn.containedExperimenters(group_id):
            result.append((exp.getId(), exp.getFullName()))
        return result

    def set_group(self, group_id: int) -> None:
        if self.is_connected():
            self._conn.SERVICE_OPTS.setOmeroGroup(group_id)

    # ------------------------------------------------------------------
    # Server name storage (QSettings — NO credentials)
    # ------------------------------------------------------------------

    @staticmethod
    def saved_servers() -> list[str]:
        """Return previously-used server hostnames."""
        s = QSettings("omero_browser_qt", "omero_browser_qt")
        raw = s.value(_SETTINGS_KEY, [])
        if isinstance(raw, str):
            return [raw] if raw else []
        return list(raw) if raw else []

    @staticmethod
    def _save_server(host: str) -> None:
        s = QSettings("omero_browser_qt", "omero_browser_qt")
        servers = OmeroGateway.saved_servers()
        if host not in servers:
            servers.insert(0, host)
        else:
            servers.remove(host)
            servers.insert(0, host)
        s.setValue(_SETTINGS_KEY, servers[:10])

    # ------------------------------------------------------------------
    # Pixel helpers (ICE)
    # ------------------------------------------------------------------

    def pixels_wrapper(self, image) -> NonCachedPixelsWrapper:
        """Return a NonCachedPixelsWrapper for *image*."""
        return NonCachedPixelsWrapper(image)


@contextmanager
def raw_pixels_store(image) -> Generator:
    """Context manager that yields an ICE RawPixelsStore for *image*,
    ensuring proper cleanup."""
    conn = image._conn
    ps = conn.c.sf.createRawPixelsStore()
    pid = image.getPrimaryPixels().getId()
    ps.setPixelsId(pid, True, conn.SERVICE_OPTS)
    try:
        yield ps
    finally:
        try:
            ps.close()
        except Exception:  # noqa: BLE001
            pass
