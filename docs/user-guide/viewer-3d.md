# 3D Volume Viewer

The OMERO Viewer includes an optional GPU-accelerated 3D volume renderer
powered by [vispy](https://vispy.org/).

## Requirements

Install the `viewer` extra:

```bash
pip install "omero-browser-qt[viewer]"
```

This adds `vispy` and `PyOpenGL`.

## Render modes

| Mode | Description | Slider |
|------|-------------|--------|
| **MIP** | Maximum intensity projection through the volume | Gain |
| **Attenuated MIP** | MIP with depth attenuation for stronger front-to-back separation | Attenuation |
| **Translucent** | Semi-transparent volume rendering with boosted midtones and softened opacity | Gain |
| **Average** | Mean intensity along the ray, useful for softer context views | Gain |
| **Isosurface** | Surface rendering at the current threshold | Threshold |
| **Additive** | Additive blending of all voxels | Gain |
| **MinIP** | Minimum intensity projection, mainly useful for bright-background / non-fluorescence data | Cutoff |

Switch modes via the **Render** combo box in the viewer toolbar.

!!! note
    `MinIP` is hidden automatically for fluorescence-like images because it
    usually collapses to black when the background is near zero.

## Controls

| Control | Effect |
|---------|--------|
| **Mode-specific slider** | Gain, attenuation, threshold, or cutoff depending on the current render mode |
| **Lo% / Hi%** spinboxes | Percentile-based contrast windowing |
| **Downsample** | Load every 1st, 2nd, or 4th voxel in X/Y/Z while preserving physical aspect ratio |
| **Smooth** | Toggle volume interpolation between linear and nearest when supported by the current render mode |
| **Channel buttons** | Toggle individual channels on/off |
| **2D / 3D** toggle | Switch between the 2D slice viewer and 3D volume view |
| **Reset View** | Reset the 3D camera framing |

### Slider behavior

- **Isosurface**, **Attenuated MIP**, and **MinIP** update immediately while dragging because they only change a lightweight shader parameter
- **MIP**, **Translucent**, **Average**, and **Additive** update their label live, but the expensive volume refresh is deferred slightly or applied on release to keep the slider motion smooth

!!! note "Refresh timing"
    Contrast and gain-related 3D updates are coalesced with a short debounce
    so the UI stays responsive. The raw OMERO stack is cached locally, so
    these updates do not re-fetch the volume from the server.

## Multi-channel rendering

Multiple active channels are rendered with additive blending
(`set_gl_state('additive', depth_test=False)`). Each channel uses
its OMERO display colour as a custom vispy colormap.

For plain RGB images imported as three channels, the channel toggles are
shown as **R**, **G**, and **B** with matching button colours.

## Camera and interaction

- The 3D canvas uses an **Arcball** camera, so you can freely rotate the
  volume, including upside-down views
- Mouse drag rotates the volume
- Mouse wheel zooms
- **Reset View** restores a sensible framing after large rotations

## Performance

- Stacks are loaded in a background thread with a progress bar
- Raw stacks are cached so contrast, gain, and interpolation adjustments don't re-fetch data
- Only active channels are rendered
- Downsampling reduces voxel count in all three axes while preserving the
  original physical aspect ratio

## Embedding in your own application

The 3D viewer in the OMERO Viewer uses vispy's `SceneCanvas` embedded as a
`QWidget` inside a `QStackedWidget`. See
[src/omero_browser_qt/omero_viewer.py](https://github.com/Cellular-Imaging-Amsterdam-UMC/omero-browser-qt/blob/main/src/omero_browser_qt/omero_viewer.py)
for the full implementation.
