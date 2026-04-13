"""
Image loader — ICE-based pixel retrieval from OMERO.

Provides two paths:
* **Plain images** — ``load_image_data()`` fetches the full 5-D array
  (T, C, Z, Y, X) via ``getPlane()`` on ``RawPixelsStore``.
* **Pyramidal / large images** — ``load_pyramid_lazy()`` returns a list of
  dask arrays (one per resolution level) using ``getTile()``.

All pixel access goes through ICE so values are the *real* stored values
(not rendered RGB).
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any

import dask
import dask.array as da
import numpy as np

from .gateway import PIXEL_TYPES, raw_pixels_store

log = logging.getLogger(__name__)

# Tile size used for pyramidal / large image access
TILE_SIZE = 1024

# Images with width*height above this threshold are treated as "large"
# and loaded lazily / via tiles.
LARGE_IMAGE_THRESHOLD = 4096 * 4096


# ------------------------------------------------------------------
# Metadata extraction
# ------------------------------------------------------------------

def get_image_metadata(image) -> dict[str, Any]:
    """Extract useful metadata from an OMERO ImageWrapper.

    Returns a dict with keys:
      size_x, size_y, size_z, size_c, size_t,
      pixel_type (numpy dtype string),
      pixel_size_x, pixel_size_y, pixel_size_z (µm, float | None),
      channels (list of dicts with 'name', 'color', 'emission_wavelength',
                'window_start', 'window_end', 'active'),
      name, id
    """
    pixels = image.getPrimaryPixels()

    ptype = pixels.getPixelsType().value
    dtype = PIXEL_TYPES.get(ptype, "u2")

    size_x = image.getSizeX()
    size_y = image.getSizeY()
    size_z = image.getSizeZ()
    size_c = image.getSizeC()
    size_t = image.getSizeT()

    # Physical pixel sizes
    def _pix_size(obj):
        if obj is None:
            return None
        try:
            return float(obj.getValue())
        except Exception:  # noqa: BLE001
            return None

    psx = _pix_size(pixels.getPhysicalSizeX())
    psy = _pix_size(pixels.getPhysicalSizeY())
    psz = _pix_size(pixels.getPhysicalSizeZ())

    # Channel info
    channels = []
    for idx, ch in enumerate(image.getChannels()):
        info: dict[str, Any] = {
            "name": ch.getLabel(),
            "index": idx,
            "emission_wavelength": None,
            "color": _omero_color_to_rgb(ch.getColor()),
            "active": ch.isActive(),
        }
        em = ch.getEmissionWave()
        if em is not None:
            try:
                info["emission_wavelength"] = float(em)
            except Exception:  # noqa: BLE001
                pass
        # Contrast window
        info["window_start"] = ch.getWindowStart()
        info["window_end"] = ch.getWindowEnd()
        channels.append(info)

    return {
        "name": image.getName(),
        "id": image.getId(),
        "size_x": size_x,
        "size_y": size_y,
        "size_z": size_z,
        "size_c": size_c,
        "size_t": size_t,
        "pixel_type": dtype,
        "pixel_size_x": psx,
        "pixel_size_y": psy,
        "pixel_size_z": psz,
        "channels": channels,
    }


def _omero_color_to_rgb(color) -> tuple[int, int, int]:
    """Convert an OMERO Color object to (R, G, B) 0-255."""
    try:
        return (color.getRed(), color.getGreen(), color.getBlue())
    except Exception:  # noqa: BLE001
        return (255, 255, 255)


# ------------------------------------------------------------------
# Full-image loading (ICE, RawPixelsStore.getPlane)
# ------------------------------------------------------------------

def load_image_data(image) -> dict[str, Any]:
    """Load a regular (non-pyramidal) OMERO image into numpy arrays.

    Returns
    -------
    dict with:
        ``images`` — list of numpy arrays, one per channel, shape (Z, Y, X)
        ``metadata`` — result of :func:`get_image_metadata`
    """
    meta = get_image_metadata(image)
    dtype = np.dtype(meta["pixel_type"])
    sz = meta["size_z"]
    sc = meta["size_c"]
    st = meta["size_t"]

    # OMERO ICE returns pixel data in big-endian byte order
    be_dtype = dtype.newbyteorder(">")

    volumes: list[np.ndarray] = []
    with raw_pixels_store(image) as ps:
        for c in range(sc):
            planes = []
            for t in range(st):
                for z in range(sz):
                    raw = ps.getPlane(z, c, t)
                    arr = np.frombuffer(raw, dtype=be_dtype).reshape(
                        meta["size_y"], meta["size_x"]
                    ).astype(dtype)
                    planes.append(arr)
            vol = np.stack(planes, axis=0)  # (Z*T, Y, X)
            if st > 1:
                vol = vol.reshape(st, sz, meta["size_y"], meta["size_x"])
            else:
                vol = vol.reshape(sz, meta["size_y"], meta["size_x"])
            volumes.append(vol)

    return {"images": volumes, "metadata": meta}


# ------------------------------------------------------------------
# Lazy / dask-based loading for large or pyramidal images (ICE tiles)
# ------------------------------------------------------------------

def _is_large(image) -> bool:
    return image.getSizeX() * image.getSizeY() > LARGE_IMAGE_THRESHOLD


def _get_resolution_count(image) -> int:
    """Return the number of resolution levels for *image*."""
    conn = image._conn
    ps = conn.c.sf.createRawPixelsStore()
    pid = image.getPrimaryPixels().getId()
    ps.setPixelsId(pid, True, conn.SERVICE_OPTS)
    try:
        return ps.getResolutionLevels()
    finally:
        ps.close()


def load_image_lazy(image) -> dict[str, Any]:
    """Load image data lazily as dask arrays.

    For pyramidal images with multiple resolution levels this returns a
    list-of-lists (one sub-list per channel, each containing one dask
    array per resolution level, coarsest last).  For plain large images
    it returns a single-resolution lazy array per channel.

    Returns
    -------
    dict with keys ``images`` (list of dask arrays or list-of-lists)
    and ``metadata``.
    """
    meta = get_image_metadata(image)
    dtype = np.dtype(meta["pixel_type"])
    n_levels = _get_resolution_count(image)

    if n_levels > 1:
        return _load_pyramid_lazy(image, meta, dtype, n_levels)
    return _load_planes_lazy(image, meta, dtype)


def _load_planes_lazy(image, meta, dtype) -> dict[str, Any]:
    """Build lazy dask arrays using getPlane (one plane per chunk)."""
    conn = image._conn
    pid = image.getPrimaryPixels().getId()
    sy, sx = meta["size_y"], meta["size_x"]

    be_dtype = dtype.newbyteorder(">")

    def _fetch_plane(z, c, t):
        ps = conn.c.sf.createRawPixelsStore()
        ps.setPixelsId(pid, True, conn.SERVICE_OPTS)
        try:
            raw = ps.getPlane(z, c, t)
            return np.frombuffer(raw, dtype=be_dtype).reshape(sy, sx).astype(dtype)
        finally:
            ps.close()

    volumes = []
    for c in range(meta["size_c"]):
        planes = []
        for z in range(meta["size_z"]):
            plane = dask.delayed(_fetch_plane)(z, c, 0)
            arr = da.from_delayed(plane, shape=(sy, sx), dtype=dtype)
            planes.append(arr)
        vol = da.stack(planes, axis=0)  # (Z, Y, X)
        volumes.append(vol)

    return {"images": volumes, "metadata": meta}


def _load_pyramid_lazy(image, meta, dtype, n_levels) -> dict[str, Any]:
    """Build lazy dask arrays using getTile for each resolution level."""
    conn = image._conn
    pid = image.getPrimaryPixels().getId()

    def _get_level_info(level):
        """Return (sizeX, sizeY, tileW, tileH) for a resolution level."""
        ps = conn.c.sf.createRawPixelsStore()
        ps.setPixelsId(pid, True, conn.SERVICE_OPTS)
        try:
            ps.setResolutionLevel(level)
            # Server-optimal tile size
            ts = ps.getTileSize()
            tw, th = int(ts[0]), int(ts[1])
            # Compute actual dimensions from byte sizes (more reliable
            # than getResolutionDescriptions which can report padded sizes)
            bpp = ps.getByteWidth()
            row_size = ps.getRowSize()
            plane_size = ps.getPlaneSize()
            lx = row_size // bpp
            ly = plane_size // row_size if row_size > 0 else 0
            return lx, ly, tw, th
        except Exception:
            # Fallback: scale from full res, use small safe tile
            factor = 2 ** (n_levels - 1 - level)
            return (meta["size_x"] // factor, meta["size_y"] // factor,
                    min(256, meta["size_x"] // factor),
                    min(256, meta["size_y"] // factor))
        finally:
            ps.close()

    volumes_per_channel: list[list[da.Array]] = []

    for c in range(meta["size_c"]):
        level_arrays = []
        for level in range(n_levels):
            lx, ly, tile_w, tile_h = _get_level_info(level)

            be_dtype = dtype.newbyteorder(">")

            def _fetch_tile(_z, _c, _t, _x, _y, _w, _h, _level=level):
                ps = conn.c.sf.createRawPixelsStore()
                ps.setPixelsId(pid, True, conn.SERVICE_OPTS)
                try:
                    ps.setResolutionLevel(_level)
                    raw = ps.getTile(_z, _c, _t, _x, _y, _w, _h)
                    return np.frombuffer(raw, dtype=be_dtype).reshape(_h, _w).astype(dtype)
                finally:
                    ps.close()

            # Build tiled dask array for this level
            rows = []
            for y0 in range(0, ly, tile_h):
                row_tiles = []
                th = min(tile_h, ly - y0)
                for x0 in range(0, lx, tile_w):
                    tw = min(tile_w, lx - x0)
                    tile = dask.delayed(_fetch_tile)(0, c, 0, x0, y0, tw, th)
                    arr = da.from_delayed(tile, shape=(th, tw), dtype=dtype)
                    row_tiles.append(arr)
                rows.append(da.concatenate(row_tiles, axis=1))
            plane = da.concatenate(rows, axis=0)  # (ly, lx)

            # Stack Z-slices
            if meta["size_z"] > 1:
                z_planes = []
                for z in range(meta["size_z"]):
                    z_rows = []
                    for y0 in range(0, ly, tile_h):
                        z_row = []
                        th = min(tile_h, ly - y0)
                        for x0 in range(0, lx, tile_w):
                            tw = min(tile_w, lx - x0)
                            t = dask.delayed(_fetch_tile)(z, c, 0, x0, y0, tw, th)
                            z_row.append(da.from_delayed(t, shape=(th, tw), dtype=dtype))
                        z_rows.append(da.concatenate(z_row, axis=1))
                    z_planes.append(da.concatenate(z_rows, axis=0))
                level_arr = da.stack(z_planes, axis=0)  # (Z, ly, lx)
            else:
                level_arr = plane[np.newaxis, ...]  # (1, ly, lx)

            level_arrays.append(level_arr)
        volumes_per_channel.append(level_arrays)

    return {"images": volumes_per_channel, "metadata": meta}


def is_large_image(image) -> bool:
    """Return True if *image* should be loaded via tile-based access."""
    return _is_large(image)


# ------------------------------------------------------------------
# Multi-resolution tile provider with LRU cache
# ------------------------------------------------------------------

class PyramidTileProvider:
    """Thread-safe multi-resolution tile provider for OMERO pyramid images.

    Fetches single-channel 2-D tiles via ICE ``RawPixelsStore.getTile()``
    and caches them in an LRU dictionary.  Safe to call ``get_tile`` from
    a background thread while the main thread calls ``get_cached_tile``.

    Parameters
    ----------
    image : omero.gateway.ImageWrapper
        Connected OMERO image.
    max_cache_mb : int
        Maximum tile cache size in megabytes.
    """

    def __init__(self, image, max_cache_mb: int = 512):
        self._conn = image._conn
        self._pid = image.getPrimaryPixels().getId()
        self._meta = get_image_metadata(image)
        self._dtype = np.dtype(self._meta["pixel_type"])
        self._be_dtype = self._dtype.newbyteorder(">")
        self._n_levels = _get_resolution_count(image)
        self._lock = threading.Lock()

        # Pre-query each level's (sizeX, sizeY, tileW, tileH)
        self._levels: list[tuple[int, int, int, int]] = []
        for lev in range(self._n_levels):
            self._levels.append(self._query_level(lev))

        # LRU tile cache: (level, c, z, t, tx, ty) -> ndarray
        self._cache: OrderedDict = OrderedDict()
        self._max_bytes = max_cache_mb * 1024 * 1024
        self._cur_bytes = 0

    # ---- internal ---------------------------------------------------

    def _query_level(self, level):
        ps = self._conn.c.sf.createRawPixelsStore()
        ps.setPixelsId(self._pid, True, self._conn.SERVICE_OPTS)
        try:
            ps.setResolutionLevel(level)
            ts = ps.getTileSize()
            tw, th = int(ts[0]), int(ts[1])
            bpp = ps.getByteWidth()
            row = ps.getRowSize()
            plane = ps.getPlaneSize()
            sx = row // bpp
            sy = plane // row if row > 0 else 0
            return (sx, sy, tw, th)
        except Exception:
            factor = 2 ** (self._n_levels - 1 - level)
            w = max(1, self._meta["size_x"] // factor)
            h = max(1, self._meta["size_y"] // factor)
            return (w, h, min(256, w), min(256, h))
        finally:
            ps.close()

    def _evict(self, needed: int):
        while self._cur_bytes + needed > self._max_bytes and self._cache:
            _, arr = self._cache.popitem(last=False)
            self._cur_bytes -= arr.nbytes

    # ---- public API -------------------------------------------------

    @property
    def n_levels(self) -> int:
        return self._n_levels

    @property
    def metadata(self) -> dict:
        return self._meta

    @property
    def dtype(self):
        return self._dtype

    def full_size(self) -> tuple[int, int]:
        """(width, height) at the highest resolution level."""
        return self._levels[-1][0], self._levels[-1][1]

    def level_size(self, level: int) -> tuple[int, int]:
        return self._levels[level][0], self._levels[level][1]

    def tile_size(self, level: int) -> tuple[int, int]:
        return self._levels[level][2], self._levels[level][3]

    def best_level_for_scale(self, scale: float) -> int:
        """Return pyramid level where one level-pixel ~= one screen-pixel.

        *scale* = screen_pixels / full_res_image_pixels.
        """
        full_w = self._levels[-1][0]
        for level in range(self._n_levels):
            if self._levels[level][0] / full_w >= scale * 0.5:
                return level
        return self._n_levels - 1

    def has_tile(self, level, c, z, t, tx, ty) -> bool:
        with self._lock:
            return (level, c, z, t, tx, ty) in self._cache

    def get_cached_tile(self, level, c, z, t, tx, ty):
        """Return tile from cache (no server fetch). Returns *None* if not cached."""
        with self._lock:
            key = (level, c, z, t, tx, ty)
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        return None

    def get_tile(self, level, c, z, t, tx, ty):
        """Return tile, fetching from server if not in cache."""
        with self._lock:
            key = (level, c, z, t, tx, ty)
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

        # Fetch outside lock (network I/O)
        sx, sy, tw, th = self._levels[level]
        x0, y0 = tx * tw, ty * th
        aw, ah = min(tw, sx - x0), min(th, sy - y0)
        if aw <= 0 or ah <= 0:
            return None

        ps = self._conn.c.sf.createRawPixelsStore()
        ps.setPixelsId(self._pid, True, self._conn.SERVICE_OPTS)
        try:
            ps.setResolutionLevel(level)
            raw = ps.getTile(z, c, t, x0, y0, aw, ah)
            arr = np.frombuffer(raw, dtype=self._be_dtype).reshape(ah, aw).astype(self._dtype)
        finally:
            ps.close()

        with self._lock:
            self._evict(arr.nbytes)
            self._cache[(level, c, z, t, tx, ty)] = arr
            self._cur_bytes += arr.nbytes
        return arr

    def load_overview(self, z: int = 0, t: int = 0) -> list[np.ndarray]:
        """Load all channels at level 0 (smallest) as numpy arrays.

        Returns a list of arrays, one per channel, each shape ``(1, Y, X)``.
        Useful for quick overview display and contrast percentile computation.
        """
        sx, sy, tw, th = self._levels[0]
        sc = self._meta["size_c"]
        volumes = []
        for c in range(sc):
            n_tx = (sx + tw - 1) // tw
            n_ty = (sy + th - 1) // th
            plane = np.zeros((sy, sx), dtype=self._dtype)
            for tyi in range(n_ty):
                for txi in range(n_tx):
                    tile = self.get_tile(0, c, z, t, txi, tyi)
                    if tile is not None:
                        y0t = tyi * th
                        x0t = txi * tw
                        plane[y0t:y0t + tile.shape[0], x0t:x0t + tile.shape[1]] = tile
            volumes.append(plane[np.newaxis, ...])
        return volumes
