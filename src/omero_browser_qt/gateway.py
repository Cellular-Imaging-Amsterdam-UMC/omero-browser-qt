"""
OmeroGateway — singleton QObject wrapping omero.gateway.BlitzGateway.

Uses ICE transport exclusively for raw pixel access. Server host names are
remembered across runs and, when available, a temporary OMERO session UUID
is cached locally for up to ten minutes so the app can reconnect without
storing the user's password.
"""

from __future__ import annotations

import logging
import time
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
_SESSION_HOST_KEY = "omero_browser_qt/session/host"
_SESSION_PORT_KEY = "omero_browser_qt/session/port"
_SESSION_USER_KEY = "omero_browser_qt/session/username"
_SESSION_TOKEN_KEY = "omero_browser_qt/session/token"
_SESSION_EXPIRES_AT_KEY = "omero_browser_qt/session/expires_at_ms"
_SESSION_TTL_MS = 10 * 60 * 1000
_SESSION_AGENT = "omero-browser-qt"


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
        self._runtime_host: str = ""
        self._runtime_port: int = 4064
        self._runtime_username: str = ""
        self._runtime_password: str = ""

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        remember_session: bool = False,
    ) -> bool:
        """Open a connection to the OMERO server.

        Returns *True* on success.  On failure emits :pyqtSignal:`error`
        and returns *False*.
        """
        from omero.gateway import BlitzGateway

        self._runtime_host = host
        self._runtime_port = port
        self._runtime_username = username
        self._runtime_password = password
        self.disconnect()

        try:
            conn = BlitzGateway(username, password, host=host, port=port, secure=True)
            if not conn.connect():
                msg = conn.getLastError() or "Connection refused"
                self._clear_saved_session()
                self.error.emit(str(msg))
                return False

            self._prepare_live_connection(conn)
            self._conn = conn
            self._host = host
            self._port = port
            self._username = username
            self._save_server(host)
            if remember_session:
                self._save_session(
                    host=host,
                    port=port,
                    username=username,
                    session_uuid=self._session_uuid(conn),
                )
            else:
                self._clear_saved_session()
            log.info("Connected to %s as %s", host, username)
            self.connected.emit(host)
            return True
        except Exception as exc:  # noqa: BLE001
            self._clear_saved_session()
            self.error.emit(str(exc))
            return False

    def disconnect(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None
            self.disconnected.emit()
        self._host = ""
        self._port = 4064
        self._username = ""
        self._clear_saved_session()

    def try_restore_session(self) -> bool:
        """Try to reconnect using a cached OMERO session UUID.

        Returns *True* if the cached session was reused successfully.
        Invalid or expired cached sessions are cleared automatically.
        """
        if self.is_connected():
            return True

        cached = self._load_saved_session()
        if cached is None:
            return False

        expires_at = int(cached["expires_at_ms"])
        if expires_at <= self._now_ms():
            self._clear_saved_session()
            return False

        from omero import client as OmeroClient
        from omero.gateway import BlitzGateway

        host = str(cached["host"])
        port = int(cached["port"])
        username = str(cached["username"])
        session_uuid = str(cached["session_uuid"])
        client = None
        try:
            client = OmeroClient(host, port)
            try:
                client.setAgent(_SESSION_AGENT)
            except Exception:  # noqa: BLE001
                pass
            client.joinSession(session_uuid)

            conn = BlitzGateway(client_obj=client)
            self._prepare_live_connection(conn)
            self._conn = conn
            self._host = host
            self._port = port
            self._username = username
            self._save_server(host)
            self._save_session(
                host=host,
                port=port,
                username=username,
                session_uuid=self._session_uuid(conn),
            )
            log.info("Restored cached OMERO session for %s on %s", username, host)
            self.connected.emit(host)
            return True
        except Exception as exc:  # noqa: BLE001
            log.info("Cached OMERO session restore failed: %s", exc)
            self._clear_saved_session()
            if client is not None:
                try:
                    client.setFastShutdown(True)
                except Exception:  # noqa: BLE001
                    pass
            return False

    def shutdown_for_exit(self) -> None:
        """Release the local client at app shutdown without closing a valid cached session."""
        if self._conn is None:
            return
        if not self._has_reusable_saved_session():
            self.disconnect()
            return

        conn = self._conn
        self._conn = None
        try:
            client = getattr(conn, "c", None)
            if client is not None:
                try:
                    session = client.getSession()
                    session.detachOnDestroy()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    client.setFastShutdown(True)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

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

    def runtime_login_fields(self) -> dict[str, str | int]:
        """Return in-memory login values cached for the current app run only."""
        return {
            "host": self._runtime_host,
            "port": self._runtime_port,
            "username": self._runtime_username,
            "password": self._runtime_password,
        }

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

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _save_session(*, host: str, port: int, username: str, session_uuid: str) -> None:
        settings = QSettings("omero_browser_qt", "omero_browser_qt")
        settings.setValue(_SESSION_HOST_KEY, host)
        settings.setValue(_SESSION_PORT_KEY, int(port))
        settings.setValue(_SESSION_USER_KEY, username)
        settings.setValue(_SESSION_TOKEN_KEY, session_uuid)
        settings.setValue(_SESSION_EXPIRES_AT_KEY, OmeroGateway._now_ms() + _SESSION_TTL_MS)

    @staticmethod
    def _clear_saved_session() -> None:
        settings = QSettings("omero_browser_qt", "omero_browser_qt")
        settings.remove("omero_browser_qt/session")

    @staticmethod
    def _load_saved_session() -> dict[str, str | int] | None:
        settings = QSettings("omero_browser_qt", "omero_browser_qt")
        host = settings.value(_SESSION_HOST_KEY, "")
        token = settings.value(_SESSION_TOKEN_KEY, "")
        username = settings.value(_SESSION_USER_KEY, "")
        port = settings.value(_SESSION_PORT_KEY, 4064)
        expires_at = settings.value(_SESSION_EXPIRES_AT_KEY, 0)
        if not host or not token or not username:
            return None
        try:
            return {
                "host": str(host),
                "port": int(port),
                "username": str(username),
                "session_uuid": str(token),
                "expires_at_ms": int(expires_at),
            }
        except (TypeError, ValueError):
            return None

    def _has_reusable_saved_session(self) -> bool:
        cached = self._load_saved_session()
        if cached is None:
            return False
        return int(cached["expires_at_ms"]) > self._now_ms()

    @staticmethod
    def _session_uuid(conn) -> str:
        client = getattr(conn, "c", None)
        if client is not None:
            try:
                return str(client.getSessionId())
            except Exception:  # noqa: BLE001
                pass
        ctx = conn.getEventContext()
        return str(ctx.sessionUuid)

    @staticmethod
    def _prepare_live_connection(conn) -> None:
        client = getattr(conn, "c", None)
        if client is not None:
            try:
                client.enableKeepAlive(60)
            except Exception:  # noqa: BLE001
                pass
            try:
                client.setAgent(_SESSION_AGENT)
            except Exception:  # noqa: BLE001
                pass
            try:
                client.getSession().detachOnDestroy()
            except Exception:  # noqa: BLE001
                pass

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
