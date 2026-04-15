"""
omero-browser-qt — Reusable PyQt6 dialog for browsing OMERO via ICE.

Quick start::

    from omero_browser_qt import OmeroGateway, OmeroBrowserDialog

    gw = OmeroGateway()

    # Show login if needed, then browse and select images
    for img in OmeroBrowserDialog.select_images(gateway=gw):
        print(img.getName(), img.getId())

    for ctx in OmeroBrowserDialog.select_image_contexts(gateway=gw):
        print(ctx.breadcrumb)
"""

from .browser_dialog import OmeroBrowserDialog
from .gateway import OmeroGateway, raw_pixels_store
from .image_loader import (
    PyramidTileProvider,
    RegularImagePlaneProvider,
    get_image_metadata,
    is_large_image,
    load_image_data,
    load_image_lazy,
)
from .login_dialog import LoginDialog
from .rendering import ChannelDisplay, ImageDisplaySettings, get_image_display_settings
from .scale_bar import ScaleBarSpec, compute_scale_bar
from .selection_context import SelectedImageContext
from .tree_model import NodeType, OmeroTreeModel
from .view_backends import VIEW_BACKEND_ICE, VIEW_BACKEND_WEB, WebRenderedImageBackend
from .webclient import OmeroWebClient

__all__ = [
    "LoginDialog",
    "OmeroBrowserDialog",
    "OmeroGateway",
    "OmeroTreeModel",
    "NodeType",
    "ChannelDisplay",
    "ImageDisplaySettings",
    "PyramidTileProvider",
    "RegularImagePlaneProvider",
    "ScaleBarSpec",
    "SelectedImageContext",
    "VIEW_BACKEND_ICE",
    "VIEW_BACKEND_WEB",
    "WebRenderedImageBackend",
    "OmeroWebClient",
    "compute_scale_bar",
    "get_image_display_settings",
    "get_image_metadata",
    "is_large_image",
    "load_image_data",
    "load_image_lazy",
    "raw_pixels_store",
]

__version__ = "0.1.5"
