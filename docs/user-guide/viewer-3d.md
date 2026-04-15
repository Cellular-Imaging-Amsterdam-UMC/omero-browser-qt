# 3D Volume Viewer

The demo viewer includes an optional GPU-accelerated 3D volume renderer
powered by [vispy](https://vispy.org/).

## Requirements

Install the `viewer3d` extra:

```bash
pip install "omero-browser-qt[viewer3d]"
```

This adds `vispy` and `PyOpenGL`.

## Render modes

| Mode | Description |
|------|-------------|
| **MIP** | Maximum intensity projection through the volume |
| **Translucent** | Semi-transparent volume with gamma correction (γ = 0.5) |
| **Isosurface** | Surface rendering at the current threshold |
| **Additive** | Additive blending of all voxels |

Switch modes via the **Render** combo box in the viewer toolbar.

## Controls

| Control | Effect |
|---------|--------|
| **Gain** slider | Brightness multiplier (context-aware default per render mode) |
| **Lo% / Hi%** spinboxes | Percentile-based contrast windowing |
| **Threshold** slider | Isosurface threshold (only visible in Isosurface mode) |
| **Channel buttons** | Toggle individual channels on/off |
| **2D / 3D** toggle | Switch between the 2D slice viewer and 3D volume view |

!!! note "Debounce"
    Lo/Hi and threshold changes are debounced with a 3-second timer to
    avoid excessive re-renders while dragging.

## Multi-channel rendering

Multiple active channels are rendered with additive blending
(`set_gl_state('additive', depth_test=False)`). Each channel uses
its OMERO display colour as a custom vispy colormap.

## Performance

- Stacks are loaded in a background thread with a progress bar
- Raw stacks are cached so contrast adjustments don't re-fetch data
- Only active channels are rendered

## Embedding in your own application

The 3D viewer in the demo uses vispy's `SceneCanvas` embedded as a
`QWidget` inside a `QStackedWidget`. See
[examples/viewer_demo.py](https://github.com/Cellular-Imaging-Amsterdam-UMC/omero-browser-qt/blob/main/examples/viewer_demo.py)
for the full implementation.
