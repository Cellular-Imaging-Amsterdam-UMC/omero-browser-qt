# Demo Viewer

A full-featured multi-channel image viewer is included at
[`examples/viewer_demo.py`](https://github.com/Cellular-Imaging-Amsterdam-UMC/omero-browser-qt/blob/main/examples/viewer_demo.py).

<p align="center">
  <img src="../images/viewer_and_login.png" alt="Viewer with login dialog" width="700">
</p>

## Running

```bash
python examples/viewer_demo.py
```

For 3D volume rendering, install the optional extra first:

```bash
pip install "omero-browser-qt[viewer3d]"
```

## Features

<p align="center">
  <img src="../images/viewer_image_open.png" alt="Viewer with image open" width="700">
</p>

### 2D viewer

- **Open from OMERO** — launches the login + browser dialogs
- **Projection modes** — Slice, MIP, SUM, Mean, Median, Extended Focus, Local Contrast
- **Z slider** — navigate slices (enabled in Slice mode)
- **Timepoint slider** — navigate T dimension
- **Lo% / Hi%** — percentile-based contrast adjustment
- **Channel buttons** — toggle individual channels, coloured to match OMERO metadata
- **Scale bar** — automatic overlay when physical pixel size is available
- **Cursor readout** — live X / Y / Z / T in the status bar
- **Backend selector** — ICE for raw pixels, WEB for server-rendered preview
- **Fit to View** — reset zoom and fit content to the window
- **Mouse** — scroll to zoom, drag to pan
- **Play/Pause** — animate through Z-slices or timepoints

### 3D viewer

- **Render modes** — MIP, Translucent, Isosurface, Additive
- **Gain slider** — brightness, context-aware defaults per render mode
- **Threshold slider** — isosurface cutoff (Isosurface mode only)
- **Multi-channel** — additive blending with OMERO channel colours
- **Progress bar** — shown while loading Z-stacks
- **Cached stacks** — contrast adjustments don't re-fetch data

## Architecture

The viewer demonstrates:

- `OmeroBrowserDialog.select_image_contexts()` for structured selection
- `RegularImagePlaneProvider` for on-demand plane fetching
- `get_image_display_settings()` for channel metadata
- `compute_scale_bar()` for physical scale overlay
- `WebRenderedImageBackend` for WEB mode rendering
- vispy `SceneCanvas` embedded via `QStackedWidget` for 3D
