# omero-browser-qt

Reusable PyQt6 dialog for browsing and retrieving images from [OMERO](https://www.openmicroscopy.org/omero/) servers.
Pixel data is fetched exclusively through the **ICE** transport layer so you always get the real stored values (not rendered RGB).

## Features

- **Login dialog** with server-name history (credentials are never stored)
- **QuPath-style browser** — resizable dialog with:
  - Group / Owner filter combos
  - Lazy-loading Project → Dataset → Image tree
  - Image thumbnail preview (256 × 256)
  - Attribute table (dimensions, pixel sizes, acquisition date, …)
  - Name filter
- **ICE pixel loading** via `RawPixelsStore`
  - Full 5-D array fetch (`getPlane`)
  - Tile-based dask lazy loading for large / pyramidal images (`getTile`)
- Designed to be **embedded in any PyQt6 application**

## Prerequisites: ZeroC ICE + omero-py

`omero-py` depends on **ZeroC ICE**, which cannot be built from source on
modern Python.  Pre-built wheels are available for **Python 3.10 – 3.12**
from Glencoe Software:

> <https://www.glencoesoftware.com/blog/2023/12/08/ice-binaries-for-omero.html>

Install them **before** installing this package:

```bash
# 1. Install the pre-built ZeroC ICE wheel (pick your platform)
pip install https://github.com/glencoesoftware/zeroc-ice-py-linux-x86_64/releases/download/20240202/zeroc_ice-3.6.5-cp311-cp311-manylinux_2_28_x86_64.whl       # Linux
pip install https://github.com/glencoesoftware/zeroc-ice-py-win-x86_64/releases/download/20240325/zeroc_ice-3.6.5-cp311-cp311-win_amd64.whl                     # Windows
pip install https://github.com/glencoesoftware/zeroc-ice-py-macos-universal2/releases/download/20240131/zeroc_ice-3.6.5-cp311-cp311-macosx_11_0_universal2.whl  # macOS (Intel + Apple Silicon)
# Replace cp311 with cp310 or cp312 as needed. See the blog post above for details.

# 2. Install omero-py (now that ICE is available)
pip install omero-py
```

Alternatively, use **conda**:

```bash
conda install -c conda-forge zeroc-ice omero-py
```

## Installation

```bash
pip install omero-browser-qt
```

### With the demo viewer

```bash
pip install "omero-browser-qt[viewer]"
```

## Quick start

```python
from PyQt6.QtWidgets import QApplication
from omero_browser_qt import OmeroGateway, LoginDialog, OmeroBrowserDialog

app = QApplication([])
gw = OmeroGateway()

# 1. Login (only server names are remembered)
if LoginDialog(gateway=gw).exec():

    # 2. Browse & select images
    dlg = OmeroBrowserDialog(gateway=gw)
    if dlg.exec():
        for img in dlg.get_selected_images():
            print(img.getName(), img.getId())

    gw.disconnect()
```

### Loading pixel data

```python
from omero_browser_qt import load_image_data, load_image_lazy, is_large_image

# For regular images — returns numpy arrays
result = load_image_data(image_wrapper)
volumes = result["images"]   # list of np.ndarray, one per channel, shape (Z, Y, X)
meta    = result["metadata"]

# For large / pyramidal images — returns dask arrays
if is_large_image(image_wrapper):
    result = load_image_lazy(image_wrapper)
    # result["images"] is a list (per channel) of lists (per resolution level) of dask arrays
```

## Demo viewer

A stripped-down multi-channel image viewer is included as an example.
It supports opening images from local files (via bioio) and from OMERO:

```bash
python examples/viewer_demo.py
```

**Controls:**
- **Open File…** — load a local OME-TIFF, ND2, CZI, etc.
- **Open from OMERO…** — launches the login + browser dialogs
- **Projection** — Slice / MIP (max intensity) / SUM
- **Z slider** — navigate slices (enabled in Slice mode)
- **Lo% / Hi%** — percentile-based contrast adjustment
- **Channel buttons** — toggle individual channels on/off
- **Mouse wheel** — zoom; **middle-drag** — pan

## License

MIT
