# Getting Started

## Prerequisites

### ZeroC ICE + omero-py

`omero-py` depends on **ZeroC ICE**, which cannot be built from source on
modern Python. Pre-built wheels are available for **Python 3.10 – 3.12**
from [Glencoe Software](https://www.glencoesoftware.com/blog/2023/12/08/ice-binaries-for-omero.html).

=== "pip (Linux)"

    ```bash
    pip install https://github.com/glencoesoftware/zeroc-ice-py-linux-x86_64/releases/download/20240202/zeroc_ice-3.6.5-cp311-cp311-manylinux_2_28_x86_64.whl
    pip install omero-py
    ```

=== "pip (Windows)"

    ```bash
    pip install https://github.com/glencoesoftware/zeroc-ice-py-win-x86_64/releases/download/20240325/zeroc_ice-3.6.5-cp311-cp311-win_amd64.whl
    pip install omero-py
    ```

=== "pip (macOS)"

    ```bash
    pip install https://github.com/glencoesoftware/zeroc-ice-py-macos-universal2/releases/download/20240131/zeroc_ice-3.6.5-cp311-cp311-macosx_11_0_universal2.whl
    pip install omero-py
    ```

=== "conda"

    ```bash
    conda install -c conda-forge zeroc-ice omero-py
    ```

!!! tip
    Replace `cp311` with `cp310` or `cp312` to match your Python version.

### PyQt6

PyQt6 is listed as a dependency and will be installed automatically via pip.

## Installation

```bash
pip install omero-browser-qt
```

### Optional extras

| Extra | Install command | Adds |
|-------|----------------|------|
| `viewer3d` | `pip install "omero-browser-qt[viewer3d]"` | vispy + PyOpenGL for 3D volume rendering |
| `docs` | `pip install "omero-browser-qt[docs]"` | MkDocs toolchain (maintainers only) |

## First workflow

The simplest way to browse and select images:

```python
from PyQt6.QtWidgets import QApplication
from omero_browser_qt import OmeroGateway, OmeroBrowserDialog

app = QApplication([])
gw = OmeroGateway()

# Shows login dialog, then the browser. Returns selected ImageWrappers.
for img in OmeroBrowserDialog.select_images(gateway=gw):
    print(img.getName(), img.getId())

gw.disconnect()
```

If you also need the project/dataset breadcrumb for each image:

```python
for ctx in OmeroBrowserDialog.select_image_contexts():
    print(ctx.breadcrumb)          # e.g. "Project > Dataset > image.tiff"
    print(ctx.image.getId())
```

## ICE vs WEB backends

| | ICE (default) | WEB |
|---|---|---|
| **Transport** | OMERO.blitz (Ice RPC) | OMERO.web REST API |
| **Pixel data** | Raw stored values | Server-rendered RGB |
| **Large images** | Tile-based dask arrays | Not supported (falls back to ICE) |
| **Projections** | Client-side (Slice, MIP, SUM, …) | Server-side (Slice, MIP, Mean) |
| **Use case** | Quantitative analysis | Quick preview / lightweight clients |

Choose the backend at runtime via the **ICE / WEB** selector in the browser
dialog, or programmatically via `VIEW_BACKEND_ICE` / `VIEW_BACKEND_WEB`.

## Next steps

- [Browser Dialog guide](user-guide/browser-dialog.md) — learn the full UI
- [Pixel Loading guide](user-guide/pixel-loading.md) — load arrays from OMERO
- [API Reference](api/index.md) — complete class & function docs
