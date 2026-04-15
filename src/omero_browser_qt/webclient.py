"""Minimal OMERO.web client helpers for rendered image access.

Provides :class:`OmeroWebClient`, an HTTP client that talks to the
OMERO.web JSON API and WebGateway endpoints.  Authentication uses
CSRF tokens and session cookies obtained from the running OMERO.web
instance.
"""

from __future__ import annotations

import json
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


class OmeroWebClient:
    """HTTP client for OMERO.web JSON/WebGateway endpoints."""

    def __init__(self, gateway, *, base_url: str | None = None, server_id: int = 1):
        """Create a web client for the given gateway.

        Parameters
        ----------
        gateway : OmeroGateway
            Connected gateway instance (used for host URL and credentials).
        base_url : str | None
            Override the OMERO.web base URL.  Defaults to the gateway's
            :attr:`~OmeroGateway.web_base_url`.
        server_id : int
            OMERO server id used in the web login request (default ``1``).
        """
        self._gateway = gateway
        self._base_url = (base_url or gateway.web_base_url).rstrip("/")
        self._server_id = server_id
        self._cookies = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookies))
        self._logged_in = False
        self._login_key: tuple[str, str, int] | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    def ensure_logged_in(self) -> None:
        """Authenticate with OMERO.web if not already logged in.

        Obtains a CSRF token, posts credentials, and stores the session
        cookie for subsequent requests.

        Raises
        ------
        RuntimeError
            If credentials are missing or the login request fails.
        """
        username, password = self._gateway.web_credentials()
        login_key = (self._gateway.host, username, self._server_id)
        if self._logged_in and self._login_key == login_key:
            return
        if not username or not password:
            raise RuntimeError("WEB backend requires current OMERO credentials")

        token_data = self._json_get("/api/v0/token/")
        csrf = token_data.get("data")
        if not csrf:
            raise RuntimeError("Could not obtain OMERO.web CSRF token")

        data = urlencode(
            {
                "server": self._server_id,
                "username": username,
                "password": password,
                "csrfmiddlewaretoken": csrf,
            }
        ).encode("utf-8")
        req = Request(
            self._url("/api/v0/login/"),
            data=data,
            headers={
                "X-CSRFToken": csrf,
                "Referer": f"{self._base_url}/webclient/",
            },
        )
        with self._opener.open(req) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not payload.get("success"):
            raise RuntimeError("OMERO.web login failed")
        self._logged_in = True
        self._login_key = login_key

    def get_img_data(self, image_id: int) -> dict:
        """Fetch image metadata from the WebGateway ``imgData`` endpoint.

        Parameters
        ----------
        image_id : int
            OMERO image id.

        Returns
        -------
        dict
            Parsed JSON with keys ``rdefs``, ``channels``, ``size``, etc.
        """
        self.ensure_logged_in()
        return self._json_get(f"/webgateway/imgData/{image_id}/")

    def render_image(
        self,
        image_id: int,
        *,
        z: int | None = None,
        t: int | None = None,
        channel_spec: str | None = None,
        projection: str | None = None,
    ) -> bytes:
        """Download a server-rendered image as raw JPEG/PNG bytes.

        Parameters
        ----------
        image_id : int
            OMERO image id.
        z : int | None
            Z-plane index (omitted for projections).
        t : int | None
            Timepoint index (omitted for projections).
        channel_spec : str | None
            OMERO channel rendering string, e.g. ``"1|0:255$FF0000,2|0:255$00FF00"``.
        projection : str | None
            Projection type: ``"normal"``, ``"intmax"``, or ``"intmean"``.

        Returns
        -------
        bytes
            Raw image data (typically JPEG).
        """
        self.ensure_logged_in()
        path = f"/webgateway/render_image/{image_id}/"
        if z is not None and t is not None:
            path = f"/webgateway/render_image/{image_id}/{z}/{t}/"
        query: dict[str, str] = {}
        if channel_spec:
            query["c"] = channel_spec
        if projection and projection != "normal":
            query["p"] = projection
        req = Request(self._url(path, query))
        with self._opener.open(req) as resp:
            return resp.read()

    def _json_get(self, path: str) -> dict:
        req = Request(self._url(path))
        with self._opener.open(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _url(self, path: str, query: dict[str, str] | None = None) -> str:
        url = f"{self._base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        return url
