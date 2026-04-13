"""
omero-browser-qt — Reusable PyQt6 dialog for browsing OMERO via ICE.

Quick start::

    from omero_browser_qt import OmeroGateway, LoginDialog, OmeroBrowserDialog

    gw = OmeroGateway()

    # Show login (only server names are remembered, never credentials)
    if LoginDialog(gateway=gw).exec():
        dlg = OmeroBrowserDialog(gateway=gw)
        if dlg.exec():
            for img in dlg.get_selected_images():
                print(img.getName(), img.getId())
"""

from .browser_dialog import OmeroBrowserDialog
from .gateway import OmeroGateway, raw_pixels_store
from .image_loader import (
    PyramidTileProvider,
    get_image_metadata,
    is_large_image,
    load_image_data,
    load_image_lazy,
)
from .login_dialog import LoginDialog
from .tree_model import NodeType, OmeroTreeModel

__all__ = [
    "LoginDialog",
    "OmeroBrowserDialog",
    "OmeroGateway",
    "OmeroTreeModel",
    "NodeType",
    "PyramidTileProvider",
    "get_image_metadata",
    "is_large_image",
    "load_image_data",
    "load_image_lazy",
    "raw_pixels_store",
]

__version__ = "0.1.3"
