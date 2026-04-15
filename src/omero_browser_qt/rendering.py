"""Normalized OMERO rendering/display metadata helpers.

Provides :class:`ChannelDisplay` and :class:`ImageDisplaySettings`
dataclasses that present OMERO channel metadata in a uniform way,
and :func:`get_image_display_settings` to build them from a raw
metadata dict.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ChannelDisplay:
    """Normalized per-channel display information.

    Attributes
    ----------
    index : int
        Zero-based channel index.
    name : str
        Channel label (e.g. ``"DAPI"``, ``"GFP"``).
    color : tuple[int, int, int] | None
        RGB colour as ``(R, G, B)`` integers 0–255.
    emission_wavelength : float | None
        Emission wavelength in nm, if available.
    active : bool
        Whether the channel is toggled on by default.
    window_start : float | None
        Contrast window lower bound (intensity value).
    window_end : float | None
        Contrast window upper bound (intensity value).
    """

    index: int
    name: str
    color: tuple[int, int, int] | None = None
    emission_wavelength: float | None = None
    active: bool = True
    window_start: float | None = None
    window_end: float | None = None


@dataclass(slots=True)
class ImageDisplaySettings:
    """Normalized image display settings derived from OMERO metadata.

    Attributes
    ----------
    channels : list[ChannelDisplay]
        One entry per channel with colour, window, and active state.
    default_z : int
        Server-suggested default Z-plane.
    default_t : int
        Server-suggested default timepoint.
    """

    channels: list[ChannelDisplay]
    default_z: int = 0
    default_t: int = 0


def get_image_display_settings(metadata: dict) -> ImageDisplaySettings:
    """Return normalized channel/display settings from image metadata.

    Parameters
    ----------
    metadata : dict
        Metadata dict as returned by :func:`get_image_metadata`.

    Returns
    -------
    ImageDisplaySettings
        Dataclass with ``channels``, ``default_z``, and ``default_t``.
    """
    size_c = max(int(metadata.get("size_c", 0)), 0)
    channels_in = metadata.get("channels", []) or []
    channels: list[ChannelDisplay] = []

    for idx in range(max(size_c, len(channels_in))):
        raw = channels_in[idx] if idx < len(channels_in) else {}
        name = raw.get("name") or raw.get("label") or f"Ch{idx}"
        channels.append(
            ChannelDisplay(
                index=idx,
                name=name,
                color=raw.get("color"),
                emission_wavelength=raw.get("emission_wavelength"),
                active=bool(raw.get("active", True)),
                window_start=raw.get("window_start"),
                window_end=raw.get("window_end"),
            )
        )

    return ImageDisplaySettings(channels=channels)
